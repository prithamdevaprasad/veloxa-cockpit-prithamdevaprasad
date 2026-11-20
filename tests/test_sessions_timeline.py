"""Sprint 7 APIs: timeline, own-session list (paginated) and session history; migration 0016 isolation.
SYNTHETIC pack, agents and interviewers only."""

import psycopg
import pytest
from agent_fixtures import grant_source, syn_agent
from conftest import DEV_TOKEN, ensure_configured_d1
from test_agent_api import client

from veloxa.agents import registry
from veloxa.db import connect, tenant_tx

pytestmark = pytest.mark.db
AUTH = {"Authorization": f"Bearer {DEV_TOKEN}"}


@pytest.fixture
def agent(db, tmp_path):
    tenant = ensure_configured_d1(db, tmp_path)
    cfg = syn_agent("people_ops")
    with connect(db.dsn("projection")) as proj:
        registry.publish(proj, tenant, cfg)
    grant_source(db, tenant, "keka_hrms")
    return tenant, cfg


def test_the_timeline_lists_record_events_with_evidence_and_fails_closed(db, monkeypatch, agent):
    with client(db, monkeypatch, anonymous=True) as anon:
        assert anon.get("/v1/timeline").status_code == 401
    with client(db, monkeypatch) as c:
        t = c.get("/v1/timeline", headers=AUTH).json()
        assert t["status"] == "ok" and any("joined" in e["display"] for e in t["events"])
        assert all(e["evidence"] for e in t["events"]) and t["read_basis"]["batch_seq"] >= 1
        assert c.get("/v1/timeline?start=not-a-date", headers=AUTH).status_code == 422


def test_session_history_survives_reloads_is_paginated_and_private(db, monkeypatch, agent):
    tenant, cfg = agent
    with client(db, monkeypatch, runtime="synthetic") as c:
        ids = []
        for i in range(3):
            s = c.post(f"/v1/agents/{cfg.agent_id}/sessions", json={}, headers=AUTH).json()["session_id"]
            c.post(f"/v1/sessions/{s}/messages", json={"message": "What is our headcount?",
                                                       "idempotency_key": f"syn-hist-{i:04d}"}, headers=AUTH)
            ids.append(s)
        page1 = c.get(f"/v1/agents/{cfg.agent_id}/sessions?limit=2", headers=AUTH).json()
        page2 = c.get(f"/v1/agents/{cfg.agent_id}/sessions?limit=2&offset=2", headers=AUTH).json()
        assert len(page1["sessions"]) == 2 and page1["has_more"] is True and page2["has_more"] is False
        assert {x["session_id"] for x in page1["sessions"] + page2["sessions"]} == set(ids)
        detail = c.get(f"/v1/sessions/{ids[0]}", headers=AUTH).json()
        assert detail["turns"][0]["message"] == "What is our headcount?" and detail["turns"][0]["claims"]
    with client(db, monkeypatch, principal="T002", runtime="synthetic") as other:
        assert other.get(f"/v1/sessions/{ids[0]}", headers=AUTH).status_code == 404
        assert other.get(f"/v1/agents/{cfg.agent_id}/sessions", headers=AUTH).json()["sessions"] == []
    with connect(db.dsn("app")) as app, tenant_tx(app, tenant, "person:T002"):       # 0016: the database itself
        assert app.execute("SELECT count(*) AS n FROM runtime.agent_execution WHERE session_id = ANY(%s::uuid[])",
                           (ids,)).fetchone()["n"] == 0                       # T001's turns are invisible to T002
    with psycopg.connect(db.admin_dsn, autocommit=True) as a:
        assert a.execute("SELECT count(*) FROM runtime.agent_execution WHERE session_id = ANY(%s::uuid[])",
                         (ids,)).fetchone()[0] == 3
