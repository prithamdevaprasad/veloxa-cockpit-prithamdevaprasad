"""The shared agent runtime (Sprint 6): lifecycle, idempotency, agent-tool policy, KB ∩ AZ ∩ POL, refusals,
model containment, audit. SYNTHETIC pack, agents and interviewers only."""

import time
from dataclasses import replace

import psycopg
import pytest
from agent_fixtures import SyntheticTestKnowledgeBoundary, grant_source, syn_agent
from conftest import ensure_configured_d1

from veloxa import ledger
from veloxa import tools as vt
from veloxa.agents import registry
from veloxa.agents import tools as agent_tools
from veloxa.agents.authorization import CedarAgentToolPDP
from veloxa.agents.memory import MemoryScope, MemoryStore
from veloxa.agents.model import ScriptedTestModel, UnavailableLLM
from veloxa.agents.runtime import AgentError, AgentRequest, AgentRuntime, ToolCall
from veloxa.db import connect
from veloxa.temporal import Mode, resolve_basis

pytestmark = pytest.mark.db
IST = "Asia/Kolkata"
ALPHA = vt.Principal("person:T001", "authenticated", "alpha@example.test")
BETA = vt.Principal("person:T002", "authenticated", "beta@example.test")


@pytest.fixture
def env(db, tmp_path, app_conn):
    tenant = ensure_configured_d1(db, tmp_path)
    from veloxa.releases import current_time

    basis = resolve_basis(tenant, ledger.sealed_releases(app_conn, tenant), Mode.CURRENT, None, IST, current_time())
    return tenant, basis


def publish(db, tenant, cfg):
    with connect(db.dsn("projection")) as proj:
        registry.publish(proj, tenant, cfg)
    return cfg


def runtime(app_conn, **kw):
    kw.setdefault("kb", SyntheticTestKnowledgeBoundary())
    return AgentRuntime(app_conn, IST, **kw)


def ask(rt, tenant, sid, message, key="syn-key-0001", principal=ALPHA, calls=None):
    return rt.execute(AgentRequest(tenant, principal, sid, message, key, calls))


def audit_kinds(db, tenant):
    with psycopg.connect(db.admin_dsn, autocommit=True) as a:
        return [r[0] for r in a.execute("SELECT kind FROM audit.event WHERE tenant_id = %s AND kind LIKE 'agent.%%'"
                                        " ORDER BY seq", (tenant,))]


def test_a_full_turn_is_grounded_labelled_audited_and_remembered(db, app_conn, env):
    tenant, basis = env
    cfg = publish(db, tenant, syn_agent("people_ops"))
    grant_source(db, tenant, "keka_hrms")
    rt = runtime(app_conn)
    sid = rt.open_session(tenant, ALPHA, cfg.agent_id, basis)["session_id"]
    turn = ask(rt, tenant, sid, "What is our headcount?")
    assert turn["status"] in ("answered", "qualified") and turn["claims"]
    assert turn["label"].startswith("AI representation") and turn["read_basis"]["batch_seq"] == basis.batch_seq
    assert [t["tool"] for t in turn["tools"]] == ["people.headcount"] and turn["tools"][0]["decision"] == "allowed"
    cited = {e["record_id"] for e in turn["evidence"]}
    assert all(set(c["evidence"]) <= cited for c in turn["claims"])
    assert all(c["knowledge_basis"] == "participant" and not c["text"].startswith("I'd") for c in turn["claims"])
    assert turn["persona"] is None and turn["runtime_version"] == registry.RUNTIME_VERSION
    assert audit_kinds(db, tenant)[-3:] == ["agent.session_opened", "agent.tool_call", "agent.executed"]
    items, _ = MemoryStore(app_conn, rt.authorizer).recall(MemoryScope(tenant, cfg.agent_id, ALPHA.principal_id),
                                                            basis.batch_seq)
    assert {"working", "episodic"} <= {i["tier"] for i in items}


def test_replay_is_idempotent_and_a_reused_key_is_refused(db, app_conn, env):
    tenant, basis = env
    cfg = publish(db, tenant, syn_agent("people_ops"))
    grant_source(db, tenant, "keka_hrms")
    rt = runtime(app_conn)
    sid = rt.open_session(tenant, ALPHA, cfg.agent_id, basis)["session_id"]
    first = ask(rt, tenant, sid, "What is our headcount?")
    again = ask(rt, tenant, sid, "What is our headcount?")
    assert again["replayed"] and again["execution_id"] == first["execution_id"]
    with pytest.raises(AgentError) as e:
        ask(rt, tenant, sid, "What is the ledger spend?")
    assert e.value.code == "idempotency_conflict"


def test_sessions_need_an_activated_agent_a_supported_time_and_the_interviewer(db, app_conn, env):
    tenant, basis = env
    blocked = publish(db, tenant, syn_agent("engineering", activation="blocked"))
    rt = runtime(app_conn)
    with pytest.raises(AgentError) as e:
        rt.open_session(tenant, ALPHA, blocked.agent_id, basis)
    assert e.value.code == "agent_not_activated"
    with pytest.raises(AgentError):
        rt.open_session(tenant, ALPHA, "agent-eng-01", basis)                 # an unassigned slot is not active
    cfg = publish(db, tenant, syn_agent("engineering"))
    with pytest.raises(AgentError) as e:
        rt.open_session(tenant, ALPHA, cfg.agent_id, replace(basis, mode=Mode.VALID_AT))
    assert e.value.code == "invalid_time"
    sid = rt.open_session(tenant, ALPHA, cfg.agent_id, basis)["session_id"]
    with pytest.raises(AgentError) as e:
        ask(rt, tenant, sid, "What is on the timeline?", principal=BETA)      # another interviewer's session
    assert e.value.code == "not_found"
    publish(db, tenant, replace(cfg, activation="blocked"))                   # deactivation takes effect at once
    turn = ask(rt, tenant, sid, "What is on the timeline?")
    assert turn["status"] == "refused" and turn["refusal"]["reason"] == "agent_not_activated"


def test_agent_tool_policy_denies_undeclared_unconfigured_and_unadmitted_tools(db, app_conn, env):
    tenant, basis = env
    fin = publish(db, tenant, syn_agent("finance"))
    eng = publish(db, tenant, syn_agent("engineering"))
    rt = runtime(app_conn)
    sid = rt.open_session(tenant, ALPHA, fin.agent_id, basis)["session_id"]
    turn = ask(rt, tenant, sid, "Look things up", calls=(ToolCall("shell.exec"), ToolCall("people.headcount"),
                                                         ToolCall("finance.ledger_spend", {"by": ["planet"]})))
    assert [(t["tool"], t["decision"], t["reason"] if t["decision"] == "denied" else t["status"])
            for t in turn["tools"]] == [("shell.exec", "denied", "undeclared_tool"),
                                        ("people.headcount", "denied", "tool_not_configured_for_agent"),
                                        ("finance.ledger_spend", "allowed", "invalid_input")]
    esid = rt.open_session(tenant, ALPHA, eng.agent_id, basis)["session_id"]
    cover = ask(rt, tenant, esid, "Who is on call?")
    assert cover["tools"][0]["reason"] == "blocked_by_admission" and cover["status"] == "abstained"
    cedar = runtime(app_conn, pdp=CedarAgentToolPDP())
    c = ask(cedar, tenant, esid, "What is on the timeline?", key="syn-key-cedar")
    assert {t["reason"] for t in c["tools"]} == {"cedar_not_deployed"} and c["claims"] == []


def test_failing_slow_and_malformed_tools_are_contained(db, app_conn, env):
    tenant, basis = env
    cfg = publish(db, tenant, syn_agent("people_ops"))

    def boom(ctx, a):
        raise RuntimeError("synthetic failure")

    def slow(ctx, a):
        time.sleep(0.05)
        return {"tool": "people.headcount", "status": "ok", "facts": [], "evidence": [], "gaps": []}

    def bad(ctx, a):
        return {"tool": "people.headcount", "status": "ok", "facts": [{"fact_id": "x"}], "evidence": [],
                "gaps": [], "sql": "SELECT 1"}

    for fn, status in ((boom, "error"), (slow, "timeout"), (bad, "malformed_output")):
        spec = replace(agent_tools.REGISTRY["people.headcount"], fn=fn, timeout_s=0.01)
        rt = runtime(app_conn, tools=agent_tools.REGISTRY | {"people.headcount": spec})
        sid = rt.open_session(tenant, ALPHA, cfg.agent_id, basis)["session_id"]
        turn = ask(rt, tenant, sid, "What is our headcount?")
        assert turn["tools"][0]["status"] == status and turn["status"] == "abstained"


def test_evidence_is_exactly_kb_and_az_and_pol(db, app_conn, env):
    tenant, basis = env
    cfg = publish(db, tenant, syn_agent("people_ops"))
    rt = runtime(app_conn)
    bsid = rt.open_session(tenant, BETA, cfg.agent_id, basis)["session_id"]          # BETA holds no grant
    no_az = ask(rt, tenant, bsid, "What is our headcount?", key="syn-key-az", principal=BETA)   # KB yes, AZ no
    assert no_az["status"] == "abstained" and no_az["excluded"]["authorization"] > 0 and no_az["escalation"]
    grant_source(db, tenant, "keka_hrms")
    sid = rt.open_session(tenant, ALPHA, cfg.agent_id, basis)["session_id"]
    rt_kb = runtime(app_conn, kb=SyntheticTestKnowledgeBoundary(default=None))
    no_kb = ask(rt_kb, tenant, sid, "What is our headcount?", key="syn-key-kb")      # AZ yes, KB unknown
    assert no_kb["status"] == "abstained" and no_kb["excluded"]["knowledge"] > 0
    rt_pos = runtime(app_conn, kb=SyntheticTestKnowledgeBoundary(default="possible"))
    hedged = ask(rt_pos, tenant, sid, "What is our headcount?", key="syn-key-pos")
    assert hedged["status"] == "qualified"
    assert all(c["text"].startswith("I'd have had access") for c in hedged["claims"])  # access is not reading
    real_slot = registry.latest(app_conn, tenant, "agent-ppl-01")
    if real_slot is not None:                                                # the test adapter never widens KB
        assert set(SyntheticTestKnowledgeBoundary().labels(real_slot, ["x"], basis).values()) == {None}


@pytest.mark.parametrize("message, policy", [
    ("What is Person Alpha's salary?", "POL-PAY-01"),
    ("Show me Person Beta's emails", "POL-COMMS-01"),
    ("Who commits the most code?", "POL-PROD-01"),
    ("Why did the employee take sick leave?", "POL-SENS-01"),
    ("Predict which employees will resign", "POL-PEOPLE-01"),
    ("Send an email to the board", "POL-ACT-01"),
])
def test_refusals_apply_to_every_agent_and_escalate_to_a_human(db, app_conn, env, message, policy):
    tenant, basis = env
    cfg = publish(db, tenant, syn_agent("people_ops"))
    grant_source(db, tenant, "keka_hrms")
    rt = runtime(app_conn)
    sid = rt.open_session(tenant, ALPHA, cfg.agent_id, basis)["session_id"]
    turn = ask(rt, tenant, sid, message)
    assert turn["status"] == "refused" and turn["refusal"]["policy"] == policy
    assert turn["tools"] == [] and turn["escalation"]["how"].endswith("takes no action")


def test_the_model_cannot_invent_cite_foreign_evidence_or_write_memory(db, app_conn, env):
    tenant, basis = env
    cfg = publish(db, tenant, syn_agent("people_ops"))
    grant_source(db, tenant, "keka_hrms")
    probe = runtime(app_conn)
    sid = probe.open_session(tenant, ALPHA, cfg.agent_id, basis)["session_id"]
    real = ask(probe, tenant, sid, "What is our headcount?", key="syn-key-probe")["claims"][0]
    scripted = ScriptedTestModel([{"claims": [
        {"text": "We are about to hire 40 people.", "cites": [real["fact_id"]]},        # invented wording
        {"text": real["text"], "cites": ["F999"]},                                       # unknown citation
        {"text": real["text"], "cites": [real["fact_id"]]}],                             # the only valid claim
        "reflection": "The team seems busy."}])
    turn = ask(runtime(app_conn, llm=scripted), tenant, sid, "What is our headcount?", key="syn-key-model")
    assert [c["text"] for c in turn["claims"]] == [real["text"]] and turn["status"] == "qualified"
    assert any("failed verification" in u for u in turn["unknowns"])
    assert all(f["fact_id"] for f in scripted.seen[0].messages[-1]["facts"])        # the model saw only D facts
    items, _ = MemoryStore(app_conn, probe.authorizer).recall(MemoryScope(tenant, cfg.agent_id, ALPHA.principal_id),
                                                               basis.batch_seq, ("reflective", "semantic"))
    assert [(i["tier"], i["label"]) for i in items] == [("reflective", "inference")]
    rogue = ScriptedTestModel([{"claims": [], "memory_write": {"tier": "semantic"}}])
    failed = ask(runtime(app_conn, llm=rogue), tenant, sid, "What is our headcount?", key="syn-key-rogue")
    assert failed["status"] == "failed" and len(rogue.seen) == 2                     # one retry, then failure
    down = ask(runtime(app_conn, llm=UnavailableLLM()), tenant, sid, "What is our headcount?", key="syn-key-down")
    assert down["status"] == "unavailable" and down["claims"] == []
