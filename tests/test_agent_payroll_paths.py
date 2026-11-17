"""Payroll stays out of the agent channel on EVERY path, not just the missing payroll tool (Sprint 6 review of
6c49e2e). The synthetic pack carries distinctive pay values (gross 1000/2000, net 900/1800, a 3000 payroll ledger
line on CC-P); none may reach a tool output, an agent turn, a stored execution, a replay or agent memory.
SYNTHETIC data only."""

import json
import re

import psycopg
import pytest
from agent_fixtures import SyntheticTestKnowledgeBoundary, grant_source, syn_agent
from conftest import ensure_configured_d1

from veloxa import ledger
from veloxa import tools as vt
from veloxa.agents import registry
from veloxa.agents.runtime import AgentRequest, AgentRuntime, ToolCall
from veloxa.db import connect, tenant_tx
from veloxa.temporal import Mode, resolve_basis

pytestmark = pytest.mark.db
ALPHA = vt.Principal("person:T001", "authenticated", "alpha@example.test")
PAY_VALUES = re.compile(r"(?<![\d,.])(1,?000|2,?000|3,?000|900|1,?800|100|200)(?![\d,])")
PAY_IDS = ("payroll_register:", "Payroll_Register", "V-2", "CC-P")
PROBES = (
    ("finance.ledger_spend", {"cost_centre": "CC-P"}),
    ("finance.ledger_spend", {"by": ["cost_centre", "vendor", "month"]}),
    ("finance.ledger_spend", {}),
    ("evidence.search", {"query": "payroll salaries gross net"}),
    ("evidence.search", {"query": "Payroll Register"}),
    ("delivery.timeline", {}),
    ("delivery.board_statements", {}),
    ("conflicts.register", {}),
    ("briefing.today", {}),
)


def _leaks(text: str) -> list[str]:
    return [m for m in PAY_IDS if m in text] + PAY_VALUES.findall(text)


@pytest.fixture
def setup(db, tmp_path, app_conn):
    tenant = ensure_configured_d1(db, tmp_path)
    from veloxa.releases import current_time

    basis = resolve_basis(tenant, ledger.sealed_releases(app_conn, tenant), Mode.CURRENT, None, "Asia/Kolkata",
                          current_time())
    caps = tuple(sorted(registry.CATEGORY_CAPABILITIES["finance"]))
    tools = tuple(sorted(t for t, s in __import__("veloxa.agents.tools", fromlist=["REGISTRY"]).REGISTRY.items()
                         if s.capability in caps))
    cfg = syn_agent("finance", caps=caps, tools=tools)
    with connect(db.dsn("projection")) as proj:
        registry.publish(proj, tenant, cfg)
    for system in ("zoho_books", "payroll_register", "documents", "keka_hrms", "meeting_summaries", "email",
                   "challenge_brief", "data_room"):
        grant_source(db, tenant, system)                    # even an over-broad current grant must not leak pay
    return tenant, basis, cfg


def test_no_agent_path_returns_payroll(db, app_conn, setup):
    tenant, basis, cfg = setup
    rt = AgentRuntime(app_conn, "Asia/Kolkata", kb=SyntheticTestKnowledgeBoundary())
    sid = rt.open_session(tenant, ALPHA, cfg.agent_id, basis)["session_id"]
    turns = []
    for i, (tool, args) in enumerate(PROBES):
        turns.append(rt.execute(AgentRequest(tenant, ALPHA, sid, "Look this up", f"syn-pay-probe-{i:03d}",
                                             (ToolCall(tool, args),))))
    for q in ("What is the total payroll?", "What did we spend on salaries?", "Show the payroll register"):
        turns.append(rt.execute(AgentRequest(tenant, ALPHA, sid, q, f"syn-pay-q-{len(turns):03d}")))
    turns.append(rt.execute(AgentRequest(tenant, ALPHA, sid, "Look this up", "syn-pay-probe-000",
                                         (ToolCall(*PROBES[0]),))))          # replay returns the stored turn
    assert turns[-1]["replayed"] is True
    for t in turns:
        assert _leaks(json.dumps(t, default=str)) == [], t["tools"]
        assert all(e["data_class"] not in ("payroll_aggregate", "sensitive_personal") for e in t["evidence"])
    # the ledger tool still works on non-pay lines (the probe is meaningful, not vacuous)
    spend = next(t for t in turns if t["tools"] and t["tools"][0]["tool"] == "finance.ledger_spend"
                 and t["claims"])
    texts = " ".join(c["text"] for c in spend["claims"])
    assert "CC-A" in texts and "INR 10" in texts and "INR 20" in texts             # non-pay lines are served

    with psycopg.connect(db.admin_dsn, autocommit=True) as adm:              # everything stored by the channel
        stored = adm.execute("SELECT response::text FROM runtime.agent_execution WHERE session_id = %s",
                             (sid,)).fetchall()
        memory = adm.execute("SELECT content::text, source_records::text, data_classes::text FROM"
                             " runtime.agent_memory WHERE agent_id = %s", (cfg.agent_id,)).fetchall()
        events = adm.execute("SELECT detail::text FROM audit.event WHERE tenant_id = %s AND kind LIKE 'agent.%%'",
                             (tenant,)).fetchall()
    assert stored and all(_leaks(r[0]) == [] for r in stored)
    assert memory and all(_leaks(" ".join(r)) == [] for r in memory)
    assert all(_leaks(r[0]) == [] for r in events)


def test_the_database_hides_pay_from_the_role_agents_run_as(app_conn, setup):
    tenant, basis, _ = setup
    with tenant_tx(app_conn, tenant, ALPHA.principal_id, basis.batch_seq):
        assert app_conn.execute("SELECT count(*) AS n FROM projection.ledger_line WHERE cost_centre = 'CC-P'"
                                ).fetchone()["n"] == 0
        assert app_conn.execute("SELECT count(*) AS n FROM ledger.visible_source_record WHERE data_class IN"
                                " ('payroll_aggregate', 'sensitive_personal')").fetchone()["n"] == 0
    assert vt._evidence(app_conn, basis, ["payroll_register:data_room/finance/Payroll_Register_Synthetic.xlsx"
                                          "#Synthetic payroll!r2"]) == []
