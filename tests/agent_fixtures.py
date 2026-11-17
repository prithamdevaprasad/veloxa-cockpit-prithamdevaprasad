"""TEST-ONLY helpers for the agent runtime. SYNTHETIC identities and records only.

`SyntheticTestKnowledgeBoundary` is an explicit test adapter for KB (the represented person's historical
eligibility). It answers only for synthetic agents (`syn-` namespace) and returns `None` (= not in KB) for anything
else, so it can never widen a real agent's knowledge. Production uses `C32KnowledgeBoundary`.
"""

from __future__ import annotations

import uuid

import psycopg

from veloxa.agents import registry


class SyntheticTestKnowledgeBoundary:
    def __init__(self, default: str | None = "participant", overrides: dict[str, str | None] | None = None):
        self.default, self.overrides = default, overrides or {}

    def labels(self, agent, record_ids, basis):
        if not agent.agent_id.startswith("syn-"):
            return {r: None for r in record_ids}
        return {r: self.overrides.get(r, self.default) for r in record_ids}


def admin(db, tenant, sql, params=()):
    with psycopg.connect(db.admin_dsn, autocommit=True) as a:
        a.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant),))
        return a.execute(sql, params)


def grant_source(db, tenant, system, person="T001") -> uuid.UUID:
    """A current ReBAC grant: person may read evidence from `system` (C20 tuple, synthetic)."""
    tid = uuid.uuid4()
    admin(db, tenant, "INSERT INTO policy.relationship_tuple (tenant_id, tuple_id, resource_type, resource_id,"
          " relation, subject_type, subject_id, source) VALUES (%s,%s,'evidence_source',%s,'read','person',%s,"
          "'synthetic test')", (tenant, tid, system, person))
    return tid


def revoke(db, tenant, tuple_id):
    admin(db, tenant, "INSERT INTO policy.relationship_revocation (tenant_id, tuple_id) VALUES (%s,%s)",
          (tenant, tuple_id))


def syn_agent(category="people_ops", activation="synthetic_test", tools=None, caps=None) -> registry.AgentConfig:
    dcaps, dtools = registry.category_default(category)
    return registry.AgentConfig(f"syn-{category.replace('_', '')}-{uuid.uuid4().hex[:6]}", category,
                                caps if caps is not None else dcaps, tools if tools is not None else dtools,
                                represented_person=None, activation=activation)
