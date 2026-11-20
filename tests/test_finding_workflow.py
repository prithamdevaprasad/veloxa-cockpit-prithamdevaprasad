"""Finding, notification, investigation and follow-up are separate records (migration 0024).

- Acknowledging is per notification and person, idempotent, and leaves the finding's state alone.
- Investigation may name who is responsible; resolution, dismissal and reopening need a stated outcome or reason,
  in the application AND in the database.
- The daily review never creates a duplicate finding or notification for an unchanged concern; a concern whose premise
  vanished and later returns is reopened, not duplicated.
- Follow-up actions: a linked Jira issue is `created`/`assigned`, never `completed` by itself.
Uses the SYNTHETIC D1 test pack's tenant only."""

import dataclasses
import json
from datetime import date

import psycopg
import pytest
from conftest import ensure_configured_d1
from psycopg.rows import dict_row

from veloxa import findings
from veloxa.db import tenant_tx
from veloxa.risk import ranking, worry

pytestmark = pytest.mark.db

ITEM = ranking.Item(
    "test:workflow", "Synthetic workflow concern",
    {"C1": ranking.Measure("measured", 30, 30, "C1.documented_slip_days@1", "a synthetic slip"),
     "C2": ranking.Measure("not_applicable", reason="synthetic"), "C3": ranking.Measure("not_applicable", reason="s"),
     "C4": ranking.Measure("not_applicable", reason="s")},
    [], confidence="high", department="Engineering", follow_up="Ask the synthetic owner.")


@pytest.fixture
def env(db, tmp_path, monkeypatch):
    tenant = ensure_configured_d1(db, tmp_path)
    with psycopg.connect(db.admin_dsn, autocommit=True, row_factory=dict_row) as a:
        with tenant_tx(a, tenant):
            rec = a.execute("SELECT record_id FROM ledger.source_record WHERE tenant_id = %s ORDER BY record_id"
                            " LIMIT 1", (tenant,)).fetchone()          # the admin role bypasses RLS: filter
            k = a.execute("SELECT max(batch_seq) AS k FROM ledger.source_record WHERE tenant_id = %s",
                          (tenant,)).fetchone()["k"]
        item = dataclasses.replace(ITEM, item_id=f"test:workflow:{tmp_path.name}",
                                   evidence=[rec["record_id"]])
        state = {"items": [item]}
        monkeypatch.setattr(worry, "build_items", lambda conn, t, kk, d: state["items"])
        yield a, tenant, k, state


def _review(a, tenant, k):
    with tenant_tx(a, tenant):
        return findings.daily_review(a, tenant, k, date(2099, 3, 1), None)


def _state(a, tenant, fid):
    with tenant_tx(a, tenant):
        return a.execute("SELECT state FROM runtime.finding_current WHERE finding_id = %s", (fid,)).fetchone()["state"]


def test_daily_review_does_not_repeat_alerts_and_reopens_a_returning_concern(env):
    a, tenant, k, state = env
    first = _review(a, tenant, k)
    (fid,) = first["created"]
    with tenant_tx(a, tenant):
        n1 = a.execute("SELECT count(*) AS n FROM runtime.notification WHERE finding_id = %s", (fid,)).fetchone()["n"]
    again = _review(a, tenant, k)
    with tenant_tx(a, tenant):
        n2 = a.execute("SELECT count(*) AS n FROM runtime.notification WHERE finding_id = %s", (fid,)).fetchone()["n"]
    assert again["created"] == [] and n1 == n2 >= 1                     # unchanged finding: no new alert
    items = state["items"]
    state["items"] = []
    assert fid in _review(a, tenant, k)["reassessed"]
    state["items"] = items
    out = _review(a, tenant, k)
    assert fid in out["reopened"] and out["created"] == []                # reopened, never duplicated
    assert _state(a, tenant, fid) == "reopened"


def test_acknowledging_is_per_notification_and_does_not_resolve(env):
    a, tenant, k, _ = env
    (fid,) = _review(a, tenant, k)["created"]
    with tenant_tx(a, tenant):
        nid = str(a.execute("SELECT notification_id FROM runtime.notification WHERE finding_id = %s LIMIT 1",
                            (fid,)).fetchone()["notification_id"])
        one = findings.acknowledge(a, tenant, nid, "person:CEO")
        two = findings.acknowledge(a, tenant, nid, "person:CEO")
        rows = a.execute("SELECT actor, at FROM runtime.notification_ack WHERE notification_id = %s",
                         (nid,)).fetchall()
    assert (one["already"], two["already"]) == (False, True) and len(rows) == 1
    assert rows[0]["actor"] == "person:CEO" and rows[0]["at"] is not None
    assert _state(a, tenant, fid) == "notified"                           # seeing an alert is not resolving it


def test_investigation_resolution_and_reopen_rules(env):
    a, tenant, k, _ = env
    (fid,) = _review(a, tenant, k)["created"]
    with tenant_tx(a, tenant):
        findings.record_human_event(a, tenant, fid, "investigating", "person:CEO", "Looking into it",
                                    responsible="Engineering release owner")
        resp = a.execute("SELECT actor, responsible FROM runtime.finding_event WHERE finding_id = %s"
                         " AND state = 'investigating'", (fid,)).fetchone()
        with pytest.raises(findings.WorkflowError, match="what resolved it"):
            findings.record_human_event(a, tenant, fid, "resolved", "person:CEO", "")
        with pytest.raises(findings.WorkflowError, match="Only a resolved"):
            findings.record_human_event(a, tenant, fid, "reopened", "person:CEO", "it came back")
        findings.record_human_event(a, tenant, fid, "resolved", "person:CEO", "New date confirmed in VIT-9")
        with pytest.raises(findings.WorkflowError, match="closed"):
            findings.record_human_event(a, tenant, fid, "investigating", "person:CEO", "again")
        findings.record_human_event(a, tenant, fid, "reopened", "person:CEO", "The date moved again (record R2)")
    assert (resp["actor"], resp["responsible"]) == ("person:CEO", "Engineering release owner")
    assert _state(a, tenant, fid) == "reopened"
    with pytest.raises(psycopg.errors.CheckViolation), tenant_tx(a, tenant):  # the database enforces it too
        a.execute("INSERT INTO runtime.finding_event (tenant_id, finding_id, state, actor, note)"
                  " VALUES (%s, %s, 'resolved', 'person:X', '')", (tenant, fid))


def test_follow_up_actions_are_tracked_separately(env):
    a, tenant, k, _ = env
    (fid,) = _review(a, tenant, k)["created"]
    with tenant_tx(a, tenant):
        aid = findings.record_follow_up(a, tenant, "person:CEO", ITEM.item_id, "Ask the synthetic owner.",
                                        "assigned", finding_id=fid, jira_key="VIT-42", jira_assignee="Someone")
        with pytest.raises(findings.WorkflowError):
            findings.record_follow_up(a, tenant, "person:CEO", ITEM.item_id, "x", "blocked", action_id=aid)
        findings.record_follow_up(a, tenant, "person:CEO", ITEM.item_id, "Ask the synthetic owner.", "in_progress",
                                  action_id=aid, note="started")
        out = {x["action_id"]: x for x in findings.follow_ups(a, tenant)}
    assert out[aid]["status"] == "in_progress" and out[aid]["jira_key"] == "VIT-42"
    assert [h["status"] for h in out[aid]["history"]] == ["assigned", "in_progress"]
    assert _state(a, tenant, fid) == "notified"                           # work on it does not resolve the finding
    with tenant_tx(a, tenant):
        assert json.loads(json.dumps(findings.follow_ups(a, tenant)))      # JSON-serialisable for the API
