"""Agent registry (Sprint 6): validation, versioning, 14 unassigned slots, activation guards. SYNTHETIC only."""

from dataclasses import replace

import psycopg
import pytest
from agent_fixtures import admin, syn_agent

from veloxa.agents import registry
from veloxa.agents import tools as agent_tools
from veloxa.db import connect


@pytest.mark.parametrize("change, message", [
    ({"category": "legal"}, "unknown category"),
    ({"capabilities": ("people_directory", "finance_ledger")}, "not allowed for people_ops"),
    ({"tools": ("people.headcount", "shell.exec")}, "undeclared tool"),
    ({"tools": ("finance.ledger_spend",)}, "needs capability"),
    ({"policy_refs": ("POL-PAY-01",)}, "cannot be configured away"),
    ({"policy_refs": ("POL-NOPE",) + tuple(registry.ask_policy.POLICIES)}, "unknown policy"),
    ({"memory_config": {"working": {}}}, "five approved tiers"),
    ({"knowledge_scopes": ("everything",)}, "knowledge scope"),
    ({"activation": "active"}, "owner-approved person mapping"),
    ({"agent_id": "agent-x1", "activation": "synthetic_test"}, "syn- namespace"),
    ({"runtime_version": "agent-runtime/0"}, "runtime version"),
])
def test_configuration_cannot_escalate_or_bypass_policy(change, message):
    with pytest.raises(registry.InvalidConfig, match=message):
        registry.validate(replace(syn_agent(), **change))


def test_the_fourteen_slots_follow_the_proposed_distribution_and_assign_no_one():
    slots = registry.unassigned_slots()
    assert len(slots) == 14 and len({s.agent_id for s in slots}) == 14
    by = {c: sum(s.category == c for s in slots) for c in registry.PROPOSED_DISTRIBUTION}
    assert by == {"engineering": 5, "finance": 3, "people_ops": 3, "marketing": 3}
    assert all(s.activation == "blocked" and s.represented_person is None for s in slots)
    for s in slots:
        registry.validate(s)
        assert all(agent_tools.REGISTRY[t].capability in s.capabilities for t in s.tools)
    fin = next(s for s in slots if s.category == "finance")
    assert "people.headcount" not in fin.tools and "finance.ledger_spend" in fin.tools


@pytest.mark.db
def test_publication_is_versioned_audited_and_only_by_the_configuration_role(db, tenant, app_conn):
    with connect(db.dsn("projection")) as proj:
        assert len(registry.seed_slots(proj, tenant)) == 14
        assert registry.seed_slots(proj, tenant) == []                    # idempotent
        c = syn_agent()
        assert registry.publish(proj, tenant, c) == 1
        assert registry.publish(proj, tenant, replace(c, activation="blocked")) == 2
    hist = registry.history(app_conn, tenant, c.agent_id)
    assert [(h.config_version, h.activation) for h in hist] == [(1, "synthetic_test"), (2, "blocked")]
    assert registry.latest(app_conn, tenant, c.agent_id).activation == "blocked"
    assert len(registry.listing(app_conn, tenant)) == 15
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        registry.publish(app_conn, tenant, syn_agent())                   # the API role can never configure agents
    events = admin(db, tenant, "SELECT count(*) FROM audit.event WHERE tenant_id = %s AND kind ="
                   " 'agent.config_published'", (tenant,)).fetchone()[0]
    assert events == 16


@pytest.mark.db
@pytest.mark.parametrize("agent_id, activation, person, approved", [
    ("agent-eng-99", "synthetic_test", None, None),            # synthetic activation outside the namespace
    ("syn-eng-99", "active", "person:syn-1", "owner"),         # a synthetic agent can never be active
    ("agent-eng-98", "active", None, "owner"),                 # active needs a person and a decision
    ("syn-eng-97", "synthetic_test", "person:T001", None),     # a synthetic agent never represents a real person
])
def test_the_database_enforces_activation_rules(db, tenant, agent_id, activation, person, approved):
    with pytest.raises(psycopg.errors.CheckViolation):
        admin(db, tenant, "INSERT INTO runtime.agent_config (tenant_id, agent_id, config_version, category,"
              " represented_person, capabilities, tools, knowledge_scopes, policy_refs, memory_config,"
              " runtime_version, activation, approved_by, decision_record) VALUES (%s,%s,1,'engineering',%s,"
              " '{}','{}','{}','{}','{}','agent-runtime/1',%s,%s,%s)",
              (tenant, agent_id, person, activation, approved, "DEC-1" if approved else None))
