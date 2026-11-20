"""Risk lifecycle across knowledge points (Sprint 8): derived, reproducible, unranked. SYNTHETIC only."""

import psycopg
import pytest
from test_risk import ALPHA, MAPPINGS, two_batches  # noqa: F401  (fixture re-export)

from veloxa.risk import lifecycle


def body(*risks, signals=()):
    return {"watchlist": [{"risk_id": rid, "description": f"syn {rid}", "business_object": f"obj-{rid}",
                           "evidence": [{"record_id": e} for e in ev], "measures": {"C4": m},
                           "quantification_state": "bounded", "unknowns": []} for rid, ev, m in risks],
            "today": {"items": [{"source": "C1 evidence signals", "business_object": s, "title": f"{s}: delayed",
                                 "why_shown": "C1 delay", "evidence": ["rec:s"], "unknowns": []} for s in signals]}}


def test_opened_changed_resolved_and_reopened_are_derived_from_consecutive_bases():
    h = lifecycle.history([(1, body(("R1", ["rec:a"], "x"))),
                           (2, body(("R1", ["rec:a", "rec:b"], "x"), signals=["Syn launch"])),
                           (3, body(signals=["Syn launch"])),
                           (4, body(("R1", ["rec:a"], "y"), signals=["Syn launch"]))])
    by = {r["item_id"]: r for r in h}
    assert [(e["batch_seq"], e["event"]) for e in by["risk:R1"]["events"]] == [
        (1, "opened"), (2, "changed"), (3, "resolved"), (4, "reopened")]
    assert by["risk:R1"]["events"][1]["fields"] == ["evidence"]
    assert [(e["batch_seq"], e["event"]) for e in by["signal:Syn launch"]["events"]] == [(2, "opened")]
    assert all("score" not in e and "priority" not in e and "probability" not in e for r in h for e in r["events"])


def test_the_history_is_reproducible_and_order_independent_of_dict_order():
    bodies = [(1, body(("R2", ["rec:z"], "x"), ("R1", ["rec:a"], "x"))), (2, body(("R1", ["rec:a"], "x")))]
    assert lifecycle.history(bodies) == lifecycle.history(bodies)
    assert [r["item_id"] for r in lifecycle.history(bodies)] == ["risk:R1", "risk:R2"]


@pytest.mark.db
def test_the_real_pipeline_derives_history_without_writing_anything(db, app_conn, two_batches):  # noqa: F811
    tenant = two_batches
    def counts():
        with psycopg.connect(db.admin_dsn, autocommit=True) as a:
            return a.execute("SELECT (SELECT count(*) FROM runtime.briefing_snapshot WHERE tenant_id = %s),"
                             " (SELECT count(*) FROM runtime.detector_run WHERE tenant_id = %s),"
                             " (SELECT count(*) FROM audit.event WHERE tenant_id = %s)",
                             (tenant, tenant, tenant)).fetchone()
    before = counts()
    out = lifecycle.compute(app_conn, tenant, ALPHA, MAPPINGS)
    assert counts() == before                                     # derived: no snapshot, run or audit written
    assert out["knowledge_points"] == [1, 2] and out["ranked"] is False
    risks = [r for r in out["items"] if r["kind"] == "watchlist_risk"]
    assert risks and all(r["events"][0]["batch_seq"] == 2 and r["events"][0]["event"] == "opened" for r in risks)
    assert lifecycle.compute(app_conn, tenant, ALPHA, MAPPINGS)["items"] == out["items"]   # reproducible


@pytest.mark.db
def test_the_history_endpoint_fails_closed_and_is_unranked(db, monkeypatch, tmp_path):
    from conftest import DEV_TOKEN, api_client, dev_fixture, ensure_configured_d1

    ensure_configured_d1(db, tmp_path)
    with api_client(db, monkeypatch) as anon:
        assert anon.get("/v1/risks/history").status_code == 401
    with api_client(db, monkeypatch, **dev_fixture()) as c:
        r = c.get("/v1/risks/history", headers={"Authorization": f"Bearer {DEV_TOKEN}"})
        assert r.status_code == 200
        j = r.json()
        assert j["ranked"] is False and j["lifecycle_version"] == "risk-lifecycle/1" and 1 in j["knowledge_points"]
