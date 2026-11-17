"""C06 ingestion isolation: limits before reading, type and archive guards, a killed hung parser, and a refused
pack that commits nothing. Synthetic files only."""

import io
import time
import zipfile

import pytest
from synthetic_pack import make_d1

from veloxa.catalogue import load_catalogue
from veloxa.ingest import isolation
from veloxa.ingest.isolation import Quarantined
from veloxa.ingest.manifest import build_manifest

LIM = {"file": 1000, "pack": 5000, "files": 3, "unzipped": 10_000, "timeout": 5}


def test_limits_are_checked_before_a_file_is_read():
    isolation.check_before_read("a.txt", 1000, 0, 0, LIM)
    for args, reason in (((1001, 0, 0), "file_too_large"), ((10, 3, 0), "too_many_files"),
                         ((900, 1, 4500), "pack_too_large")):
        with pytest.raises(Quarantined) as e:
            isolation.check_before_read("a.txt", *args, LIM)
        assert e.value.reason == reason


def test_an_oversized_file_in_a_pack_is_never_read(tmp_path, monkeypatch):
    pack = make_d1(tmp_path)
    (pack / "data_room" / "README.txt").write_text("x" * 5000, encoding="utf-8")
    monkeypatch.setenv("VELOXA_INGEST_MAX_FILE_BYTES", "4096")
    read = []
    real = type(pack).read_bytes
    monkeypatch.setattr(type(pack), "read_bytes", lambda self: read.append(self.name) or real(self))
    with pytest.raises(Quarantined) as e:
        build_manifest(pack, load_catalogue())
    assert e.value.reason == "file_too_large" and "README.txt" not in read


def _zip(members: dict[str, bytes], level=zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", level) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


@pytest.mark.parametrize("suffix,data,reason", [
    (".pdf", b"PK\x03\x04 not a pdf", "type_mismatch"),
    (".xlsx", b"%PDF-1.7 not a workbook", "type_mismatch"),
    (".docx", b"PK\x03\x04 broken archive", "type_mismatch"),
    (".txt", b"\xff\xfe\x00 not utf-8", "type_mismatch"),
    (".xlsx", _zip({"x.xml": b"0" * 9000, "y.xml": b"0" * 9000}), "archive_limits"),
    (".docx", _zip({"bomb.xml": b"0" * 9000}), "archive_limits"),          # ratio far above 200
])
def test_content_must_match_its_type_and_archives_are_bounded(suffix, data, reason):
    with pytest.raises(Quarantined) as e:
        isolation.check_content("f" + suffix, suffix, data, LIM)
    assert e.value.reason == reason


def test_well_formed_synthetic_files_pass(tmp_path):
    entries = build_manifest(make_d1(tmp_path), load_catalogue())
    assert entries and all(e.size > 0 for e in entries)


def _hang(*_):
    time.sleep(60)


def test_a_hung_parser_is_killed_at_the_timeout(monkeypatch):
    monkeypatch.setattr(isolation, "_parse_all", _hang)
    started = time.monotonic()
    with pytest.raises(Quarantined) as e:
        isolation.parse_all([("a.txt", b"x", ".txt", "document")], LIM | {"timeout": 2})
    assert e.value.reason == "parse_timeout" and time.monotonic() - started < 30


def test_isolated_and_inline_parsing_give_identical_envelopes(tmp_path, monkeypatch):
    entries = build_manifest(make_d1(tmp_path), load_catalogue())
    items = [(e.relpath, e.data, e.suffix, e.rule.records) for e in entries]
    isolated = isolation.parse_all(items)
    monkeypatch.setenv("VELOXA_INGEST_PARSE_ISOLATION", "inline")
    assert isolated == isolation.parse_all(items)


@pytest.mark.db
def test_a_quarantined_pack_commits_nothing_and_audits_only_the_reason(db, ingest_conn, tenant, schedule, tmp_path):
    import psycopg
    from conftest import D1_DAY

    from veloxa.ingest.pipeline import AdmissionFailure, admit_pack, record_failure

    pack = make_d1(tmp_path)
    (pack / "brief" / "Synthetic_Brief.docx").write_bytes(b"MZ this is not a word document")
    with pytest.raises(Quarantined) as e:
        admit_pack(ingest_conn, tenant, schedule, "D1", tmp_path, D1_DAY)
    record_failure(ingest_conn, tenant, AdmissionFailure("D1", e.value))
    with psycopg.connect(db.admin_dsn) as a:
        assert a.execute("SELECT count(*) FROM ledger.admission_batch WHERE tenant_id = %s", (tenant,)
                         ).fetchone()[0] == 0
        detail = a.execute("SELECT detail FROM audit.event WHERE tenant_id = %s AND kind = 'admission.failed'",
                           (tenant,)).fetchone()[0]
    assert detail == {"pack": "D1", "reason": "type_mismatch"}                 # no file name, no content
