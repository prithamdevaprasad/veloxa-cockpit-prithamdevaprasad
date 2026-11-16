"""Checks against the REAL Day 1 release pack. Local only: needs VELOXA_DATA_PATH and a database.

Never runs in CI (the data is never in the repository). Asserts counts, classes and hashes only;
it never prints or compares record values.
"""

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

import veloxa.ingest.pipeline as pipeline
from veloxa import ledger
from veloxa.db import tenant_tx
from veloxa.releases import AdmissionRefused

pytestmark = [pytest.mark.db, pytest.mark.realdata]

DATA = os.environ.get("VELOXA_DATA_PATH")
EXPECTED_FILES = 15
EXPECTED_RECORDS_BY_SYSTEM = {
    "challenge_brief": 1, "data_room": 1, "documents": 2, "email": 3, "meeting_summaries": 2,
    "keka_hrms": 19, "payroll_register": 56, "zoho_books": 28,
}


@pytest.fixture
def data_root():
    if not DATA or not (Path(DATA) / "Day1_Mon_16Nov").is_dir():
        pytest.skip("VELOXA_DATA_PATH with Day1_Mon_16Nov not available (expected outside CI)")
    return Path(DATA)


def test_real_d1_admission(ingest_conn, app_conn, tenant, schedule, data_root, monkeypatch):
    opened = []
    real_build = pipeline.build_manifest

    def spy(pack_dir, rules):
        opened.append(pack_dir.name)
        return real_build(pack_dir, rules)

    monkeypatch.setattr(pipeline, "build_manifest", spy)
    now = datetime.now(UTC)
    result = pipeline.admit_pack(ingest_conn, tenant, schedule, "D1", data_root, now)
    assert result.file_count == EXPECTED_FILES
    assert result.record_count == sum(EXPECTED_RECORDS_BY_SYSTEM.values())

    with tenant_tx(ingest_conn, tenant):
        by_system = {r["system"]: r["n"] for r in ingest_conn.execute(
            "SELECT system, count(*) AS n FROM ledger.source_record GROUP BY system").fetchall()}
        by_class = {r["data_class"]: r["n"] for r in ingest_conn.execute(
            "SELECT data_class, count(*) AS n FROM ledger.source_record GROUP BY data_class").fetchall()}
        raws = ingest_conn.execute(
            "SELECT source_path, content_sha256, record_mode FROM raw_index.raw_object").fetchall()
    assert by_system == EXPECTED_RECORDS_BY_SYSTEM
    assert by_class["sensitive_personal"] == 56 and by_class["payroll_aggregate"] == 6
    assert sum(r["record_mode"] == "none" for r in raws) == 3  # templates registered, never admitted
    for raw in raws:  # provenance: stored hash equals the bytes of the released file
        f = data_root / "Day1_Mon_16Nov" / raw["source_path"]
        assert raw["content_sha256"] == hashlib.sha256(f.read_bytes()).hexdigest()

    # The app role sees neither individual payroll lines (56) nor payroll aggregates (6).
    assert len(ledger.visible_record_ids(app_conn, tenant, 1)) == result.record_count - 56 - 6

    # Times stay distinct and ordered: release < extraction <= admission <= seal.
    with tenant_tx(ingest_conn, tenant):
        b = ingest_conn.execute("SELECT release_at, admitted_at, sealed_at FROM ledger.admission_batch").fetchone()
        e = ingest_conn.execute(
            "SELECT min(extracted_at) AS first, max(extracted_at) AS last FROM ledger.source_record").fetchone()
    assert b["release_at"] < e["first"] <= e["last"] <= b["admitted_at"] <= b["sealed_at"]

    # Later packs are refused today, before their folders are opened.
    if now < schedule.pack("D2").release_at:
        with pytest.raises(AdmissionRefused) as exc:
            pipeline.admit_pack(ingest_conn, tenant, schedule, "D2", data_root, now)
        assert exc.value.reason == "not_yet_released"
    assert opened == ["Day1_Mon_16Nov"]
