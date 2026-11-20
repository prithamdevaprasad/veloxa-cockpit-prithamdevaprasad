"""The D1-D4 -> Jira migration preview: actionable work only, sensitive HR matters excluded, nothing written."""

import pytest
from test_demo_lab import lab  # noqa: F401  (pytest fixture: the synthetic Lumora workspace)

pytestmark = pytest.mark.db
COUNTED = ("audit.event", "intake.submission", "runtime.connector_run", "ledger.source_record", "knowledge.claim")


def _counts(conn, tid):
    from veloxa.db import tenant_tx

    with tenant_tx(conn, tid, batch_seq=10**6):
        return {t: conn.execute(f"SELECT count(*) AS n FROM {t} WHERE tenant_id = %s", (tid,)).fetchone()["n"]  # noqa: S608
                for t in COUNTED}


def test_the_preview_proposes_work_not_records_and_writes_nothing(lab):  # noqa: F811
    import uuid
    from datetime import UTC, datetime

    from veloxa import ledger, migration
    from veloxa.db import connect
    from veloxa.temporal import Mode, resolve_basis

    tid = uuid.UUID(lab["tenant_id"])
    with connect(lab["dsns"]["admin"]) as a:
        before = _counts(a, tid)
        basis = resolve_basis(tid, ledger.sealed_releases(a, tid), Mode.CURRENT, None, "Asia/Kolkata",
                              datetime.now(UTC))
        out = migration.preview(a, basis, "Asia/Kolkata")
        after = _counts(a, tid)
    assert before == after and out["read_only"] is True
    assert out["summary"]["projects"] >= 1 and out["candidates"]
    assert all(c["type"] == "Task" and c["evidence"] for c in out["candidates"])
    assert all(c["due"] is None or c["due"] for c in out["candidates"])                 # never invented
    sources = {e["source"] for e in out["excluded"]}
    assert {"payroll_register", "leave_register", "pulse_survey"} <= sources
    assert len(out["candidates"]) < lab["facts"]["issues_total"]                     # not every record
