"""Agent API contract for the Sprint 7 frontend (§19: agents, interviews, memories). SYNTHETIC only."""

import pytest
from agent_fixtures import SyntheticTestKnowledgeBoundary, grant_source, syn_agent
from conftest import API_CLOCK, DEV_TOKEN, dev_fixture, ensure_configured_d1

from veloxa.agents import registry
from veloxa.db import connect

pytestmark = pytest.mark.db
AUTH = {"Authorization": f"Bearer {DEV_TOKEN}"}


def client(db, monkeypatch, principal="T001", runtime=None, anonymous=False):
    from fastapi.testclient import TestClient

    from veloxa.agents.runtime import AgentRuntime
    from veloxa.api import PRIVILEGED_ENV, create_app

    for name in PRIVILEGED_ENV + ("VELOXA_DEV_AUTH", "VELOXA_DEV_TOKEN", "VELOXA_DEV_PRINCIPAL"):
        monkeypatch.delenv(name, raising=False)
    if not anonymous:
        for k, v in dev_fixture(principal).items():
            monkeypatch.setenv(k, v)
    factory = (lambda conn: AgentRuntime(conn, "Asia/Kolkata", kb=SyntheticTestKnowledgeBoundary())) \
        if runtime == "synthetic" else None
    return TestClient(create_app(dsn=db.dsn("app"), clock=lambda: API_CLOCK, agent_runtime=factory))


@pytest.fixture
def seeded(db, tmp_path):
    tenant = ensure_configured_d1(db, tmp_path)
    with connect(db.dsn("projection")) as proj:
        registry.seed_slots(proj, tenant)
        cfg = syn_agent("people_ops")
        registry.publish(proj, tenant, cfg)
    grant_source(db, tenant, "keka_hrms")
    return tenant, cfg


def test_the_registry_lists_unassigned_blocked_slots_and_fails_closed(db, monkeypatch, seeded):
    with client(db, monkeypatch, anonymous=True) as anon:
        assert anon.get("/v1/agents").status_code == 401
    with client(db, monkeypatch) as c:
        body = c.get("/v1/agents", headers=AUTH).json()
        slots = [a for a in body["agents"] if a["agent_id"].startswith("agent-")]
        assert len(slots) == 14 and all(a["activation"] == "blocked" and not a["assigned"] for a in slots)
        r = c.post("/v1/agents/agent-eng-01/sessions", json={}, headers=AUTH)
        assert r.status_code == 403 and r.json()["error"] == "agent_not_activated"
        assert c.post("/v1/agents/agent-zzz-99/sessions", json={}, headers=AUTH).status_code == 404
        assert c.post("/v1/agents/agent-eng-01/sessions", json={"interviewer": "person:T002"},
                      headers=AUTH).status_code == 422                   # the interviewer is never a field


def test_an_interview_round_trip_is_idempotent_and_private_to_its_interviewer(db, monkeypatch, seeded):
    _, cfg = seeded
    with client(db, monkeypatch, runtime="synthetic") as c:
        s = c.post(f"/v1/agents/{cfg.agent_id}/sessions", json={}, headers=AUTH).json()
        assert s["label"].startswith("AI representation")
        msg = {"message": "What is our headcount?", "idempotency_key": "syn-api-0001"}
        t1 = c.post(f"/v1/sessions/{s['session_id']}/messages", json=msg, headers=AUTH).json()
        assert t1["status"] in ("answered", "qualified") and t1["claims"] and t1["replayed"] is False
        t2 = c.post(f"/v1/sessions/{s['session_id']}/messages", json=msg, headers=AUTH).json()
        assert t2["replayed"] is True and t2["execution_id"] == t1["execution_id"]
        clash = c.post(f"/v1/sessions/{s['session_id']}/messages", json=msg | {"message": "Ledger spend?"},
                       headers=AUTH)
        assert clash.status_code == 409
        mem = c.get(f"/v1/agents/{cfg.agent_id}/memories", headers=AUTH).json()
        assert {"working", "episodic"} <= {i["tier"] for i in mem["items"]}
        assert c.post(f"/v1/sessions/{s['session_id']}/close", headers=AUTH).json()["working_memory_closed"] >= 1
    with client(db, monkeypatch, principal="T002", runtime="synthetic") as other:
        r = other.post(f"/v1/sessions/{s['session_id']}/messages", json=msg, headers=AUTH)
        assert r.status_code == 404                                       # another interviewer's session
        mem = other.get(f"/v1/agents/{cfg.agent_id}/memories", headers=AUTH).json()
        assert all(i["tier"] in ("episodic", "semantic") for i in mem["items"])
        assert mem["withheld"] >= 1                                       # T002 holds no grant: withheld


def test_the_default_runtime_uses_the_real_knowledge_boundary_and_answers_nothing_it_cannot_ground(
        db, monkeypatch, seeded):
    _, cfg = seeded
    with client(db, monkeypatch) as c:                                    # production wiring: C32 KB, no model
        s = c.post(f"/v1/agents/{cfg.agent_id}/sessions", json={}, headers=AUTH).json()
        t = c.post(f"/v1/sessions/{s['session_id']}/messages",
                   json={"message": "What is our headcount?", "idempotency_key": "syn-api-0002"}, headers=AUTH).json()
        assert t["status"] == "abstained" and t["claims"] == [] and t["excluded"]["knowledge"] > 0
