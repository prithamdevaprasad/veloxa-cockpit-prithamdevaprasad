"""Gold-question evaluation (H-15, H-16): `pytest tests/gold` with VELOXA_GOLD_PATH and VELOXA_DATA_PATH set.

The cases are NOT in this repository: their expected answers are derived from the real challenge material, which
is never published. Each case file is JSON: a list of
  {"id", "question", "mode"?, "as_of"?, "expected_status": qualified|abstained|refused|conflict,
   "expected_policy"?, "expected_grade"?, "required_evidence"?: [record ids], "forbidden_evidence_kinds"?: [...],
   "expected_answer_contains"?: [...], "rationale"}
The runner admits only the packs the cases' basis allows (D1 today), asks every question through the real Ask
pipeline and checks status, refusal policy, grade, cited evidence, temporal correctness and wording.
Without the private cases and data the suite is skipped, never silently passed.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

pytestmark = [pytest.mark.db, pytest.mark.realdata]
GOLD = os.environ.get("VELOXA_GOLD_PATH")
DATA = os.environ.get("VELOXA_DATA_PATH")


def _cases():
    if not GOLD or not Path(GOLD).is_dir():
        return []
    out = []
    for f in sorted(Path(GOLD).glob("*.json")):
        out += json.loads(f.read_text(encoding="utf-8"))
    return out


CASES = _cases()


@pytest.fixture(scope="module")
def gold_env(db):
    if not CASES or not DATA:
        pytest.skip("VELOXA_GOLD_PATH and VELOXA_DATA_PATH are required (private material, local only)")
    import uuid

    import psycopg

    from veloxa import config, projector
    from veloxa.db import connect
    from veloxa.ingest.pipeline import admit_pack
    from veloxa.releases import load_schedule

    schedule = load_schedule()
    tenant = uuid.uuid4()
    with psycopg.connect(db.admin_dsn, autocommit=True) as admin:
        admin.execute("INSERT INTO directory.tenant (tenant_id, slug, synthetic, time_zone) VALUES (%s, %s, true,"
                      " %s)", (tenant, f"gold-{tenant.hex[:8]}", schedule.time_zone))
    with connect(db.dsn("ingest")) as conn:
        admit_pack(conn, tenant, schedule, "D1", Path(DATA), datetime.now(UTC))   # D1 only: the authorised pack
    with connect(db.dsn("projection")) as prj:
        projector.build(prj, tenant)
    return tenant, schedule, config


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES] or None)
def test_gold_question(db, app_conn, gold_env, case):
    from veloxa import ledger, tools
    from veloxa.ask import pipeline
    from veloxa.temporal import Mode, resolve_basis

    tenant, schedule, _ = gold_env
    now = datetime.now(UTC)
    basis = resolve_basis(tenant, ledger.sealed_releases(app_conn, tenant), Mode.CURRENT, None, schedule.time_zone, now)
    ceo = tools.resolve_principal_by_title(app_conn, basis, "CEO")    # test-only lookup; the API never uses titles
    kw = {}
    if case.get("mode"):
        kw = {"mode": case["mode"], "as_of": case.get("as_of")}
    a = pipeline.answer(app_conn, tenant, schedule.time_zone, ceo, case["question"], now, **kw)
    status = "refused" if a.get("refusal") else a["status"]
    assert status == case["expected_status"], (case["id"], a["status"], a.get("refusal"))
    if case.get("expected_policy"):
        assert a["refusal"]["policy"] == case["expected_policy"]
    if case.get("expected_grade"):
        assert a["grade"]["value"] == case["expected_grade"]
    cited = {e["record_id"] for e in a.get("evidence", [])}
    for rid in case.get("required_evidence", []):
        assert rid in cited, (case["id"], rid)
    assert not any(e["kind"] in case.get("forbidden_evidence_kinds", []) for e in a.get("evidence", []))
    assert all(e["batch_seq"] <= basis.batch_seq for e in a.get("evidence", []))      # temporal correctness
    text = json.dumps(a, default=str).lower()
    for phrase in case.get("expected_answer_contains", []):
        assert phrase.lower() in text, (case["id"], phrase)
