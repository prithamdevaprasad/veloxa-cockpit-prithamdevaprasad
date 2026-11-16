import hashlib

import pytest
from synthetic_pack import D1, EXPECTED_FILES, make_d1

from veloxa.catalogue import UnclassifiedSource, classify, load_catalogue
from veloxa.ingest.manifest import DataMissing, build_manifest, manifest_sha256
from veloxa.ingest.parsers import parse, parse_docx, parse_eml, parse_xlsx_rows


def test_manifest_hashes_and_classifies_every_file(tmp_path):
    pack = make_d1(tmp_path)
    entries = build_manifest(pack, load_catalogue())
    assert len(entries) == EXPECTED_FILES
    for e in entries:
        assert e.sha256 == hashlib.sha256(e.path.read_bytes()).hexdigest()
        assert e.size == e.path.stat().st_size
        assert "\\" not in e.relpath and not e.relpath.startswith("/")
    assert manifest_sha256(entries) == manifest_sha256(list(reversed(entries)))  # order-independent


def test_unclassified_file_is_rejected(tmp_path):
    pack = make_d1(tmp_path)
    (pack / "data_room" / "mystery.bin").write_bytes(b"x")
    with pytest.raises(UnclassifiedSource):
        build_manifest(pack, load_catalogue())


def test_missing_or_empty_pack_is_reported(tmp_path):
    with pytest.raises(DataMissing):
        build_manifest(tmp_path / D1, load_catalogue())
    (tmp_path / D1).mkdir()
    with pytest.raises(DataMissing):
        build_manifest(tmp_path / D1, load_catalogue())


def test_templates_are_registered_but_never_admitted_as_evidence(tmp_path):
    pack = make_d1(tmp_path)
    rule = classify(load_catalogue(), "data_room/templates/Blank_Template.xlsx")
    assert rule.kind == "template" and rule.records == "none"
    data = (pack / "data_room" / "templates" / "Blank_Template.xlsx").read_bytes()
    assert parse(data, ".xlsx", rule.records) == []


def test_xlsx_rows_skip_title_rows_and_keep_exact_sheet_row_locators(tmp_path):
    pack = make_d1(tmp_path)
    envs = parse_xlsx_rows((pack / "data_room" / "hr" / "Employee_Master.xlsx").read_bytes())
    assert [e.locator["row"] for e in envs] == [4, 5]  # title row 1, blank row 2, header row 3
    assert all(e.locator["header_row"] == 3 for e in envs)
    assert envs[0].payload == {"Emp ID": "T001", "Full Name": "Person Alpha", "Department": "Testing"}


def test_ledger_payroll_line_is_classed_as_payroll_aggregate(tmp_path):
    pack = make_d1(tmp_path)
    rule = classify(load_catalogue(), "data_room/finance/GL_Export_Synthetic.xlsx")
    rows = parse_xlsx_rows((pack / "data_room" / "finance" / "GL_Export_Synthetic.xlsx").read_bytes())
    classes = [rule.class_for_row(e.payload) for e in rows]
    assert classes == ["confidential", "payroll_aggregate", "confidential"]


def test_payroll_register_rows_are_sensitive_personal():
    rule = classify(load_catalogue(), "data_room/finance/Payroll_Register_Synthetic.xlsx")
    assert rule.data_class == "sensitive_personal"


def test_docx_keeps_every_paragraph_in_order(tmp_path):
    pack = make_d1(tmp_path)
    (env,) = parse_docx((pack / "transcripts" / "T1_Synthetic.docx").read_bytes())
    assert env.payload["paragraphs"] == ["T1 synthetic", "Summary line."]
    assert env.locator == {"paragraphs": 2, "tables": 0}


def test_eml_headers_and_body(tmp_path):
    pack = make_d1(tmp_path)
    (env,) = parse_eml((pack / "data_room" / "email" / "01.eml").read_bytes())
    assert env.payload["headers"]["Date"] == "Mon, 05 Jan 2099 10:00:00 +0530"
    assert env.payload["body"].strip() == "Synthetic body."
    assert env.payload["attachments"] == []


def test_parsed_bytes_are_exactly_the_hashed_bytes(tmp_path):
    """Regression: a file changed on disk after hashing must not be parsed under the old hash."""
    pack = make_d1(tmp_path)
    entries = {e.relpath: e for e in build_manifest(pack, load_catalogue())}
    entry = entries["data_room/README.txt"]
    original_hash = entry.sha256
    (pack / "data_room" / "README.txt").write_text("TAMPERED AFTER HASHING", encoding="utf-8")
    (env,) = parse(entry.data, entry.suffix, entry.rule.records)
    assert not any("TAMPERED" in line for line in env.payload["lines"])
    assert hashlib.sha256(entry.data).hexdigest() == original_hash and entry.size == len(entry.data)


def test_each_file_records_its_actual_read_time(tmp_path):
    from datetime import UTC, datetime

    before = datetime.now(UTC)
    entries = build_manifest(make_d1(tmp_path), load_catalogue())
    after = datetime.now(UTC)
    assert all(before <= e.read_at <= after for e in entries)


def test_unknown_files_are_never_read(tmp_path, monkeypatch):
    from pathlib import Path

    pack = make_d1(tmp_path)
    (pack / "zz_unknown.bin").write_bytes(b"x")
    reads = []
    real = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: reads.append(self.name) or real(self))
    with pytest.raises(UnclassifiedSource):
        build_manifest(pack, load_catalogue())
    assert "zz_unknown.bin" not in reads
