"""C31 backup verification and restore drills on disposable databases (§22.2). SYNTHETIC data only.
Needs PostgreSQL client tools >= 18: on PATH, or VELOXA_PG_TOOLS=docker:<container> / a bin directory."""

import json
import shutil

import psycopg
import pytest

from veloxa.ops import backup as ops

pytestmark = pytest.mark.db
TOOLS = ops.PgTools.from_env()
needs_tools = pytest.mark.skipif(TOOLS is None, reason="PostgreSQL client tools (>= 18) are not available")


@pytest.fixture
def source(db, tmp_path):
    from conftest import ensure_configured_d1

    ensure_configured_d1(db, tmp_path / "packs")                    # synthetic D1: ledger, audit, projections
    return db


@needs_tools
def test_a_verified_backup_restores_to_identical_evidence_and_the_copy_is_dropped(source, tmp_path):
    from conftest import ADMIN_DSN

    m = ops.backup(TOOLS, ADMIN_DSN, source.name, tmp_path / "backups")
    dump = tmp_path / "backups" / m["file"]
    assert m["sha256"] and m["bytes"] > 0 and m["fingerprint"]["audit_chains_valid"] is True
    assert "0001_foundation.sql=" in m["fingerprint"]["migrations"]
    manifest = json.loads((tmp_path / "backups" / m["file"].replace(".dump", ".manifest.json")).read_text())
    assert manifest["sha256"] == m["sha256"]
    assert ops.verify(TOOLS, ADMIN_DSN, dump, m) == []
    report = ops.restore_drill(TOOLS, ADMIN_DSN, dump, m)
    assert report["ok"], report["differences"]
    assert report["target"].startswith(ops.RESTORE_PREFIX) and report["fingerprint"] == m["fingerprint"]
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:          # the drill copy never lingers
        assert c.execute("SELECT count(*) FROM pg_database WHERE datname = %s", (report["target"],)).fetchone()[0] == 0


@needs_tools
def test_a_tampered_or_missing_backup_is_detected_and_never_restored(source, tmp_path):
    from conftest import ADMIN_DSN

    m = ops.backup(TOOLS, ADMIN_DSN, source.name, tmp_path / "b")
    dump = tmp_path / "b" / m["file"]
    bad = tmp_path / "tampered.dump"
    shutil.copy(dump, bad)
    with bad.open("r+b") as f:
        f.seek(m["bytes"] // 2)
        f.write(b"\x00\x01\x02\x03")
    assert "checksum mismatch: the backup file changed or is corrupt" in ops.verify(TOOLS, ADMIN_DSN, bad, m)
    report = ops.restore_drill(TOOLS, ADMIN_DSN, bad, m)
    assert report["ok"] is False and report["target"] is None              # nothing was restored
    assert ops.verify(TOOLS, ADMIN_DSN, tmp_path / "nope.dump", m) == ["the backup file is missing"]


def test_the_fingerprint_covers_ledger_batches_migrations_and_audit_chains(source):
    from conftest import ADMIN_DSN

    from veloxa.ops.backup import _with_db

    fp = ops.fingerprint(_with_db(ADMIN_DSN, source.name))
    assert fp["audit_chains_valid"] is True and len(fp["ledger_md5"]) == 32 and fp["batches"]
