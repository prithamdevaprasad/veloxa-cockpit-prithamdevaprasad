"""C27 governed LLMClient (§18) on real PostgreSQL: policy, redaction, hard budgets, retries, structured output,
record/replay and call records. SYNTHETIC requests; the provider is a test adapter (no provider is selected)."""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from veloxa.agents.model import ModelOutputInvalid, ModelRequest, ModelUnavailable
from veloxa.db import connect, tenant_tx
from veloxa.llm.client import (
    DENY_ALL,
    GovernedLLMClient,
    RecordingMissing,
    ReplayProvider,
    ScriptedProvider,
    TenantModelPolicy,
    _period,
)

pytestmark = pytest.mark.db
POLICY = TenantModelPolicy("test-scripted", "test-model", "local", frozenset({"agent"}),
                           frozenset({"internal", "personal"}), 500)
VALID = {"claims": [{"text": "syn fact", "cites": ["F1"]}]}
SECRET = "syn-prompt-marker-91c2"


def req(tenant, facts=None, stage="agent", estimate=100, classes=None, key="syn-llm-0001"):
    facts = facts if facts is not None else [{"fact_id": "F1", "display": "syn fact", "data_classes": ["personal"]}]
    cls = classes if classes is not None else {c for f in facts for c in f["data_classes"]}
    return ModelRequest(tenant, stage, frozenset(cls), {"type": "object"}, "agent-turn/1",
                        [{"role": "system", "content": "label"}, {"role": "user", "content": SECRET, "facts": facts}],
                        estimate, key)


def budget(db, tenant, limit=1000):
    with psycopg.connect(db.admin_dsn, autocommit=True) as a:          # owner-set, never by the API
        a.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant),))
        a.execute("INSERT INTO runtime.llm_budget (tenant_id, period, limit_tokens, set_by, decision_record)"
                  " VALUES (%s,%s,%s,'synthetic owner','SYN-BUDGET-1')", (tenant, _period(), limit))


def calls(db, tenant):
    with psycopg.connect(db.admin_dsn, autocommit=True, row_factory=psycopg.rows.dict_row) as a:
        return a.execute("SELECT * FROM runtime.llm_call WHERE tenant_id = %s ORDER BY created_at", (tenant,)
                         ).fetchall()


def usage(db, tenant):
    with psycopg.connect(db.admin_dsn, autocommit=True) as a:
        return a.execute("SELECT used_tokens, reserved_tokens FROM runtime.llm_budget WHERE tenant_id = %s",
                         (tenant,)).fetchone()


def test_deny_all_by_default_and_no_budget_means_no_call(db, app_conn, tenant):
    p = ScriptedProvider([VALID])
    with pytest.raises(ModelUnavailable, match="refused_policy"):
        GovernedLLMClient(app_conn, DENY_ALL, p).call(req(tenant))
    with pytest.raises(ModelUnavailable, match="refused_budget"):
        GovernedLLMClient(app_conn, POLICY, p).call(req(tenant))
    assert p.calls == 0                                                    # refused before any call
    assert [c["status"] for c in calls(db, tenant)] == ["refused_policy", "refused_budget"]


def test_a_permitted_call_is_reserved_reconciled_and_recorded_without_prompt_text(db, app_conn, tenant):
    budget(db, tenant)
    r = GovernedLLMClient(app_conn, POLICY, ScriptedProvider([VALID], tokens=42)).call(req(tenant))
    assert r.output == VALID and r.model == "test-scripted/test-model"
    assert usage(db, tenant) == (42, 0)                                    # reservation released, usage counted
    (c,) = calls(db, tenant)
    assert (c["status"], c["used_tokens"], c["reserved_tokens"], c["prompt_version"]) == ("ok", 42, 100,
                                                                                         "agent-turn/1")
    assert SECRET not in json.dumps(c, default=str) and "syn fact" not in json.dumps(c, default=str)


def test_budgets_are_hard_limits_even_under_concurrency(db, tenant):
    budget(db, tenant, limit=350)
    def one(i):
        with connect(db.dsn("app")) as conn:
            try:
                GovernedLLMClient(conn, POLICY, ScriptedProvider([VALID], tokens=100)).call(
                    req(tenant, key=f"syn-llm-c{i:03d}"))
                return "ok"
            except ModelUnavailable as e:
                return str(e).split(":")[0]
    with ThreadPoolExecutor(6) as ex:
        out = list(ex.map(one, range(6)))
    assert out.count("ok") == 3 and out.count("refused_budget") == 3     # 3 x 100 fits in 350; a 4th does not
    used, reserved = usage(db, tenant)
    assert used <= 350 and reserved == 0


def test_forbidden_classes_are_redacted_before_assembly_or_refused(db, app_conn, tenant):
    budget(db, tenant)
    p = ScriptedProvider([VALID])
    seen = []
    orig = p.complete
    p.complete = lambda model, r: (seen.append(r), orig(model, r))[1]
    facts = [{"fact_id": "F1", "display": "syn fact", "data_classes": ["personal"]},
             {"fact_id": "F2", "display": "syn private", "data_classes": ["private_comms"]}]
    GovernedLLMClient(app_conn, POLICY, p).call(req(tenant, facts))
    assert [f["fact_id"] for f in seen[0].messages[-1]["facts"]] == ["F1"]
    assert "private_comms" not in seen[0].data_classes and calls(db, tenant)[-1]["redacted_items"] == 1
    with pytest.raises(ModelUnavailable, match="forbids sending"):            # a class with nothing to redact
        GovernedLLMClient(app_conn, POLICY, p).call(req(tenant, [], classes={"private_comms"}, key="syn-llm-0002"))


@pytest.mark.parametrize("change, match", [
    ({"stage": "plan"}, "stage"),
    ({"estimate": 0}, "estimate"),
    ({"estimate": 10_000}, "estimate"),
])
def test_policy_refuses_unpermitted_stages_and_estimates(db, app_conn, tenant, change, match):
    budget(db, tenant)
    with pytest.raises(ModelUnavailable, match=match):
        GovernedLLMClient(app_conn, POLICY, ScriptedProvider([VALID])).call(req(tenant, **change))


def test_residency_and_provider_identity_are_enforced(db, app_conn, tenant):
    budget(db, tenant)
    with pytest.raises(ModelUnavailable, match="residency"):
        GovernedLLMClient(app_conn, POLICY, ScriptedProvider([VALID], region="elsewhere")).call(req(tenant))
    with pytest.raises(ModelUnavailable, match="no permitted"):
        GovernedLLMClient(app_conn, POLICY, ScriptedProvider([VALID], name="other")).call(req(tenant))


def test_retries_are_bounded_and_invalid_output_gets_exactly_one_retry(db, app_conn, tenant):
    budget(db, tenant)
    ok = ScriptedProvider(["error", VALID])
    assert GovernedLLMClient(app_conn, POLICY, ok).call(req(tenant)).output == VALID and ok.calls == 2
    down = ScriptedProvider(["error"])
    with pytest.raises(ModelUnavailable, match="provider_error"):
        GovernedLLMClient(app_conn, POLICY, down).call(req(tenant, key="syn-llm-0003"))
    fixed = ScriptedProvider([{"claims": "nope"}, VALID])
    assert GovernedLLMClient(app_conn, POLICY, fixed).call(req(tenant, key="syn-llm-0004")).output == VALID
    bad = ScriptedProvider([{"claims": "nope"}, {"memory_write": 1}])
    with pytest.raises(ModelOutputInvalid):
        GovernedLLMClient(app_conn, POLICY, bad).call(req(tenant, key="syn-llm-0005"))
    assert calls(db, tenant)[-1]["status"] == "invalid_output" and usage(db, tenant)[1] == 0


def test_replay_serves_recordings_counts_no_usage_and_fails_loudly_when_missing(db, app_conn, tenant, tmp_path):
    budget(db, tenant)
    policy = TenantModelPolicy("replay", "test-model", "local", frozenset({"agent"}), frozenset({"personal"}), 500)
    r = req(tenant)
    (tmp_path / f"{r.request_hash()}.json").write_text(json.dumps({"output": VALID}), encoding="utf-8")
    out = GovernedLLMClient(app_conn, policy, ReplayProvider(tmp_path)).call(r)
    assert out.output == VALID and usage(db, tenant) == (0, 0)
    assert calls(db, tenant)[-1]["replayed"] is True
    with pytest.raises(RecordingMissing):
        other = [{"fact_id": "F9", "display": "unrecorded", "data_classes": ["personal"]}]
        GovernedLLMClient(app_conn, policy, ReplayProvider(tmp_path)).call(req(tenant, other, key="syn-llm-0006"))
    assert calls(db, tenant)[-1]["status"] == "replay_missing"


def test_the_api_role_cannot_set_budgets_or_reserve_for_another_tenant(db, app_conn, tenant):
    budget(db, tenant)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with tenant_tx(app_conn, tenant):
            app_conn.execute("UPDATE runtime.llm_budget SET limit_tokens = 1000000000")
    with tenant_tx(app_conn, tenant):
        assert app_conn.execute("SELECT runtime.llm_reserve(%s, %s, 1) AS ok",
                                (uuid.uuid4(), _period())).fetchone()["ok"] is False
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with tenant_tx(app_conn, tenant):
            app_conn.execute("DELETE FROM runtime.llm_call")


def test_the_agent_runtime_runs_through_the_governed_client(db, app_conn, tmp_path):
    from agent_fixtures import SyntheticTestKnowledgeBoundary, grant_source, syn_agent
    from conftest import ensure_configured_d1

    from veloxa import ledger
    from veloxa import tools as vt
    from veloxa.agents import registry
    from veloxa.agents.runtime import AgentRequest, AgentRuntime
    from veloxa.releases import current_time
    from veloxa.temporal import Mode, resolve_basis

    tenant = ensure_configured_d1(db, tmp_path)
    with connect(db.dsn("projection")) as proj:
        cfg = syn_agent("people_ops")
        registry.publish(proj, tenant, cfg)
    grant_source(db, tenant, "keka_hrms")
    alpha = vt.Principal("person:T001", "authenticated", "alpha@example.test")
    basis = resolve_basis(tenant, ledger.sealed_releases(app_conn, tenant), Mode.CURRENT, None, "Asia/Kolkata",
                          current_time())
    denied = AgentRuntime(app_conn, "Asia/Kolkata", kb=SyntheticTestKnowledgeBoundary(),
                          llm=GovernedLLMClient(app_conn))                 # production default: DENY_ALL
    sid = denied.open_session(tenant, alpha, cfg.agent_id, basis)["session_id"]
    t = denied.execute(AgentRequest(tenant, alpha, sid, "What is our headcount?", "syn-llm-turn-1"))
    assert t["status"] == "unavailable" and t["claims"] == []
