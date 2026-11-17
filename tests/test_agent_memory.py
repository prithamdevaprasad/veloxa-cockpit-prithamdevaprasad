"""Five-tier agent memory (§14.5): isolation, tier rules, provenance, events, retention, revoked knowledge.
SYNTHETIC agents, interviewers and records only."""

import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from agent_fixtures import grant_source, revoke, syn_agent

from veloxa.agents import registry
from veloxa.agents.authorization import CurrentAuthorization
from veloxa.agents.memory import MemoryScope, MemoryStore, ScopeError
from veloxa.db import connect, tenant_tx

pytestmark = pytest.mark.db
U, V = "person:T001", "person:T002"
EV = {"record_id": "documents:data_room/documents/Syn.pdf#document", "kind": "document", "data_class": "confidential",
      "batch_seq": 1, "excerpt": "synthetic excerpt", "system": "documents"}


@pytest.fixture
def agents(db, tenant):
    with connect(db.dsn("projection")) as proj:
        a, b = syn_agent(), syn_agent()
        registry.publish(proj, tenant, a)
        registry.publish(proj, tenant, b)
    return a.agent_id, b.agent_id


@pytest.fixture
def store(app_conn):
    return MemoryStore(app_conn, CurrentAuthorization(app_conn))


def session(app_conn, tenant, agent_id, interviewer, k=1):
    sid = uuid.uuid4()
    with tenant_tx(app_conn, tenant, interviewer):
        v = app_conn.execute("SELECT max(config_version) AS v FROM runtime.agent_config WHERE agent_id = %s",
                             (agent_id,)).fetchone()["v"]
        app_conn.execute("INSERT INTO runtime.agent_session (tenant_id, session_id, agent_id, config_version,"
                         " interviewer_id, batch_seq, mode) VALUES (%s,%s,%s,%s,%s,%s,'CURRENT')",
                         (tenant, sid, agent_id, v, interviewer, k))
    return sid


def test_reads_need_a_full_scope(app_conn, tenant, agents):
    with pytest.raises(ScopeError):
        MemoryScope(tenant, agents[0], "")
    with pytest.raises(psycopg.errors.UndefinedObject):           # RLS: an unset agent scope raises, never matches
        with tenant_tx(app_conn, tenant, U):
            app_conn.execute("SELECT * FROM runtime.agent_memory").fetchall()


def test_agents_and_interviewers_are_isolated(db, app_conn, tenant, agents, store):
    a, b = agents
    grant_source(db, tenant, "documents", "T001")
    grant_source(db, tenant, "documents", "T002")
    su = session(app_conn, tenant, a, U)
    sa_u, sa_v, sb_u = MemoryScope(tenant, a, U), MemoryScope(tenant, a, V), MemoryScope(tenant, b, U)
    store.append_working(sa_u, su, {"m": "u turn"}, 1)
    store.append_interview(sa_u, su, {"cited": [EV["record_id"]]}, [EV["record_id"]], ["confidential"], 1)
    store.append_reflective(sa_u, "u reflection", "test/scripted", "agent-turn/1", 1)
    store.append_episodic(sa_u, EV, "participant", 1)
    tiers = lambda items: sorted(i["tier"] for i in items)  # noqa: E731
    assert tiers(store.recall(sa_u, 1)[0]) == ["episodic", "interview", "reflective", "working"]
    assert tiers(store.recall(sa_v, 1)[0]) == ["episodic"]                   # V never sees U's session memory
    assert store.recall(sb_u, 1)[0] == []                                    # another agent sees nothing
    other = uuid.uuid4()
    with psycopg.connect(db.admin_dsn, autocommit=True) as adm:
        adm.execute("INSERT INTO directory.tenant (tenant_id, slug, synthetic, time_zone) VALUES (%s,%s,true,'UTC')",
                    (other, f"t-{other.hex[:12]}"))
    assert store.recall(MemoryScope(other, a, U), 1)[0] == []                # cross-tenant: nothing
    with pytest.raises(psycopg.errors.InsufficientPrivilege):                # RLS WITH CHECK: no foreign writes
        with tenant_tx(app_conn, tenant, U):
            app_conn.execute("SELECT set_config('app.agent_id', %s, true), set_config('app.interviewer_id', %s,"
                             " true)", (a, U))
            app_conn.execute("INSERT INTO runtime.agent_memory (tenant_id, memory_id, agent_id, interviewer_id,"
                             " session_id, tier, label, content, batch_seq) VALUES (%s,%s,%s,%s,%s,'working',"
                             "'execution_context','{}',1)", (tenant, uuid.uuid4(), b, U, su))


def test_tier_rules_and_model_output_never_become_facts(app_conn, tenant, agents, store):
    a = agents[0]
    scope = MemoryScope(tenant, a, U)
    r = store.append_reflective(scope, "a guess", "test/scripted", "agent-turn/1", 1)
    (item,) = [i for i in store.recall(scope, 1)[0] if i["memory_id"] == r]
    assert item["label"] == "inference" and item["content"]["note"].startswith("model-generated")
    with pytest.raises(ScopeError):
        store.consolidate_semantic(scope, "a fact", [r])                     # not an episode: refused
    with pytest.raises(ScopeError):
        store.consolidate_semantic(scope, "a fact", [])
    with pytest.raises(ScopeError):
        store.append_episodic(scope, EV, "remembers", 1)
    with pytest.raises(ScopeError, match="protected"):
        store.append_episodic(scope, EV | {"data_class": "payroll_aggregate"}, "participant", 1)
    with pytest.raises(psycopg.errors.CheckViolation):                       # inference only in reflective
        with tenant_tx(app_conn, tenant, U):
            app_conn.execute("SELECT set_config('app.agent_id', %s, true), set_config('app.interviewer_id', %s,"
                             " true)", (a, U))
            app_conn.execute("INSERT INTO runtime.agent_memory (tenant_id, memory_id, agent_id, tier, label,"
                             " content, source_records, provenance_episodes, batch_seq) VALUES (%s,%s,%s,'semantic',"
                             "'inference','{}','{}',ARRAY[%s]::uuid[],1)", (tenant, uuid.uuid4(), a, r))


def test_semantic_memory_keeps_provenance_and_the_most_restrictive_basis(db, app_conn, tenant, agents, store):
    grant_source(db, tenant, "documents", "T001")
    scope = MemoryScope(tenant, agents[0], U)
    e1 = store.append_episodic(scope, EV, "participant", 1)
    e2 = store.append_episodic(scope, EV | {"record_id": "documents:x#2", "batch_seq": 2}, "possible", 1)
    s = store.consolidate_semantic(scope, "synthetic consolidated statement", [e1, e2],
                                   valid_from=datetime(2099, 1, 1, tzinfo=UTC))
    (item,) = [i for i in store.recall(scope, 2, ("semantic",))[0] if i["memory_id"] == s]
    assert set(item["provenance_episodes"]) == {e1, e2} and item["knowledge_basis"] == "possible"
    assert set(item["source_records"]) == {EV["record_id"], "documents:x#2"} and item["batch_seq"] == 2
    assert store.recall(scope, 1, ("semantic",))[0] == []                    # hidden at an earlier K


def test_events_expire_without_deleting_and_history_is_replayable(db, app_conn, tenant, agents, store):
    grant_source(db, tenant, "documents", "T001")
    scope = MemoryScope(tenant, agents[0], U)
    old = store.append_episodic(scope, EV, "participant", 1)
    before = datetime.now(UTC)
    new = store.append_episodic(scope, EV | {"record_id": "documents:y#1"}, "participant", 1)
    store.event(scope, old, "superseded", "corrected record", superseded_by=new)
    ids = [i["memory_id"] for i in store.recall(scope, 1)[0]]
    assert old not in ids and new in ids
    assert old in [i["memory_id"] for i in store.recall(scope, 1, known_at=before)[0]]   # as known then
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with tenant_tx(app_conn, tenant, U):
            app_conn.execute("SELECT set_config('app.agent_id', %s, true), set_config('app.interviewer_id', %s,"
                             " true)", (agents[0], U))
            app_conn.execute("DELETE FROM runtime.agent_memory WHERE memory_id = %s", (new,))
    with pytest.raises(ScopeError):
        store.event(scope, new, "forgotten", "x")
    n = admin_count(db, tenant, "agent.memory_event")
    assert n == 1


def admin_count(db, tenant, kind):
    with psycopg.connect(db.admin_dsn, autocommit=True) as adm:
        return adm.execute("SELECT count(*) FROM audit.event WHERE tenant_id = %s AND kind = %s",
                           (tenant, kind)).fetchone()[0]


def test_revoked_knowledge_is_withheld_and_missing_authorization_fails_closed(db, app_conn, tenant, agents, store):
    t = grant_source(db, tenant, "documents", "T001")
    scope = MemoryScope(tenant, agents[0], U)
    store.append_episodic(scope, EV, "participant", 1)
    assert len(store.recall(scope, 1)[0]) == 1
    revoke(db, tenant, t)
    fresh = MemoryStore(app_conn, CurrentAuthorization(app_conn))           # authorization is evaluated now
    items, withheld = fresh.recall(scope, 1)
    assert items == [] and withheld == 1
    assert MemoryStore(app_conn, None).recall(scope, 1) == ([], 1)           # no authorizer: fail closed


def test_retention_and_session_close(app_conn, tenant, agents, store):
    scope = MemoryScope(tenant, agents[0], U)
    sid = session(app_conn, tenant, agents[0], U)
    store.append_working(scope, sid, {"m": 1}, 1)
    store.append_reflective(scope, "r", "test/scripted", "agent-turn/1", 1)
    assert store.close_session(scope, sid) == 1
    assert [i["tier"] for i in store.recall(scope, 1)[0]] == ["reflective"]
    swept = store.sweep_retention(scope, registry.DEFAULT_MEMORY, datetime.now(UTC) + timedelta(days=31))
    assert swept == 1 and store.recall(scope, 1)[0] == []
