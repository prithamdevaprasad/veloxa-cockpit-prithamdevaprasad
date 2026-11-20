"""Versioned agent registry (ARCHITECTURE.md §14.1: agents are data on one shared runtime; migration 0013).

A configuration is validated against code-declared limits before it is published, so configuration can never
escalate permissions:
- capabilities must be in the CATEGORY's allowlist (a finance agent cannot be configured with people tools);
- every tool must be declared in the agent tool registry and need a capability the agent has;
- policy references must name known policies; memory tiers must be the five approved tiers;
- a configuration grants nothing by itself: each tool call still passes the agent-tool PDP, and each evidence item
  still passes KB ∩ AZ ∩ POL at execution time.

Activation (§14.1, Sprint 6 owner rule): `blocked` until the owner approves the agent's mapping and configuration.
`synthetic_test` exists only for the reserved `syn-` namespace (synthetic agents representing synthetic people).
`active` requires a represented person, an approver and a decision record (enforced again by the database).

The 14 slots follow the PROPOSED distribution (engineering 5, finance 3, people_ops 3, marketing 3). They are
unassigned: no person, persona, manager or identity is invented.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field

import psycopg

from .. import audit
from ..ask import policy as ask_policy
from ..db import tenant_tx
from . import tools as agent_tools

RUNTIME_VERSION = "agent-runtime/1"
TIERS = ("working", "episodic", "semantic", "interview", "reflective")
ACTIVATIONS = ("blocked", "synthetic_test", "active")

# Capabilities each category may be configured with. Only capabilities the role documentation supports
# (department-level work: delivery, ledger, people directory, communications of the company) are listed.
# `self_profile` (owner decision 2, 19 Nov 2026): every agent may read ITS OWN represented person's HR work facts.
# It is not directory access: `people_directory` stays with People Ops.
CATEGORY_CAPABILITIES: dict[str, frozenset[str]] = {
    "engineering": frozenset({"delivery", "conflicts", "briefing", "evidence_search", "coverage", "self_profile"}),
    "finance": frozenset({"finance_ledger", "delivery", "conflicts", "briefing", "evidence_search", "self_profile"}),
    "people_ops": frozenset({"people_directory", "briefing", "evidence_search", "self_profile"}),
    "marketing": frozenset({"delivery", "conflicts", "briefing", "evidence_search", "self_profile"}),
}
PROPOSED_DISTRIBUTION = {"engineering": 5, "finance": 3, "people_ops": 3, "marketing": 3}
KNOWLEDGE_SCOPES = frozenset({"company_records", "own_participation", "role_plausible"})
DEFAULT_MEMORY = {"working": {"retention": "session"}, "episodic": {"retention_days": 365},
                  "semantic": {"retention_days": 365}, "interview": {"retention_days": 180},
                  "reflective": {"retention_days": 30}}


class InvalidConfig(ValueError):
    pass


@dataclass(frozen=True)
class AgentConfig:
    agent_id: str
    category: str
    capabilities: tuple[str, ...]
    tools: tuple[str, ...]
    knowledge_scopes: tuple[str, ...] = ("company_records",)
    policy_refs: tuple[str, ...] = tuple(sorted(ask_policy.POLICIES))
    memory_config: dict = field(default_factory=lambda: dict(DEFAULT_MEMORY))
    represented_person: str | None = None
    activation: str = "blocked"
    approved_by: str | None = None
    decision_record: str | None = None
    runtime_version: str = RUNTIME_VERSION
    config_version: int | None = None       # set when read from the registry


def validate(c: AgentConfig) -> None:
    """Refuse any configuration outside the code-declared limits (never trust configuration for permissions)."""
    if c.category not in CATEGORY_CAPABILITIES:
        raise InvalidConfig(f"unknown category {c.category!r}")
    extra = set(c.capabilities) - CATEGORY_CAPABILITIES[c.category]
    if extra:
        raise InvalidConfig(f"capabilities not allowed for {c.category}: {sorted(extra)}")
    for t in c.tools:
        spec = agent_tools.REGISTRY.get(t)
        if spec is None:
            raise InvalidConfig(f"undeclared tool {t!r}")
        if spec.capability not in c.capabilities:
            raise InvalidConfig(f"tool {t!r} needs capability {spec.capability!r}")
    if set(c.knowledge_scopes) - KNOWLEDGE_SCOPES or not c.knowledge_scopes:
        raise InvalidConfig("unknown or empty knowledge scope")
    unknown = set(c.policy_refs) - set(ask_policy.POLICIES)
    if unknown or not c.policy_refs:
        raise InvalidConfig(f"unknown policy references {sorted(unknown)}")
    if set(ask_policy.POLICIES) - set(c.policy_refs):
        raise InvalidConfig("every refusal policy must apply to every agent (policies cannot be configured away)")
    if set(c.memory_config) != set(TIERS):
        raise InvalidConfig("memory configuration must cover exactly the five approved tiers")
    if c.activation not in ACTIVATIONS:
        raise InvalidConfig(f"unknown activation {c.activation!r}")
    if c.activation == "synthetic_test" and not c.agent_id.startswith("syn-"):
        raise InvalidConfig("synthetic_test activation is only for the reserved syn- namespace")
    if c.activation == "active" and not (c.represented_person and c.approved_by and c.decision_record):
        raise InvalidConfig("active needs an owner-approved person mapping (approver and decision record)")
    if c.runtime_version != RUNTIME_VERSION:
        raise InvalidConfig(f"runtime version must be {RUNTIME_VERSION}")


def publish(conn: psycopg.Connection, tenant_id: uuid.UUID, c: AgentConfig) -> int:
    """Append the next configuration version (projection role). Audited."""
    validate(c)
    with tenant_tx(conn, tenant_id):
        latest = conn.execute("SELECT coalesce(max(config_version), 0) AS v FROM runtime.agent_config WHERE"
                              " tenant_id = %s AND agent_id = %s", (tenant_id, c.agent_id)).fetchone()["v"]
        version = latest + 1
        conn.execute(
            "INSERT INTO runtime.agent_config (tenant_id, agent_id, config_version, category, represented_person,"
            " capabilities, tools, knowledge_scopes, policy_refs, memory_config, runtime_version, activation,"
            " approved_by, decision_record) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (tenant_id, c.agent_id, version, c.category, c.represented_person, list(c.capabilities), list(c.tools),
             list(c.knowledge_scopes), list(c.policy_refs), json.dumps(c.memory_config), c.runtime_version,
             c.activation, c.approved_by, c.decision_record))
        audit.append(conn, tenant_id, "agent.config_published",
                     {"agent_id": c.agent_id, "config_version": version, "activation": c.activation})
    return version


def _row(r: dict) -> AgentConfig:
    return AgentConfig(r["agent_id"], r["category"], tuple(r["capabilities"]), tuple(r["tools"]),
                       tuple(r["knowledge_scopes"]), tuple(r["policy_refs"]), r["memory_config"],
                       r["represented_person"], r["activation"], r["approved_by"], r["decision_record"],
                       r["runtime_version"], r["config_version"])


def latest(conn: psycopg.Connection, tenant_id: uuid.UUID, agent_id: str) -> AgentConfig | None:
    with tenant_tx(conn, tenant_id):
        r = conn.execute("SELECT * FROM runtime.agent_config WHERE tenant_id = %s AND agent_id = %s ORDER BY"
                         " config_version DESC LIMIT 1", (tenant_id, agent_id)).fetchone()
    return _row(r) if r else None


def listing(conn: psycopg.Connection, tenant_id: uuid.UUID) -> list[AgentConfig]:
    with tenant_tx(conn, tenant_id):
        rows = conn.execute("SELECT DISTINCT ON (agent_id) * FROM runtime.agent_config WHERE tenant_id = %s"
                            " ORDER BY agent_id, config_version DESC", (tenant_id,)).fetchall()
    return [_row(r) for r in rows]


def history(conn: psycopg.Connection, tenant_id: uuid.UUID, agent_id: str) -> list[AgentConfig]:
    with tenant_tx(conn, tenant_id):
        rows = conn.execute("SELECT * FROM runtime.agent_config WHERE tenant_id = %s AND agent_id = %s ORDER BY"
                            " config_version", (tenant_id, agent_id)).fetchall()
    return [_row(r) for r in rows]


def category_default(category: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The full capability set of a category and every declared tool those capabilities reach."""
    caps = tuple(sorted(CATEGORY_CAPABILITIES[category]))
    tools = tuple(sorted(t for t, s in agent_tools.REGISTRY.items() if s.capability in caps))
    return caps, tools


def unassigned_slots() -> list[AgentConfig]:
    """The 14 proposed slots: no person, persona or identity; activation blocked."""
    out = []
    for category, n in PROPOSED_DISTRIBUTION.items():
        caps, tools = category_default(category)
        short = {"engineering": "eng", "finance": "fin", "people_ops": "ppl", "marketing": "mkt"}[category]
        out += [AgentConfig(f"agent-{short}-{i:02d}", category, caps, tools) for i in range(1, n + 1)]
    return out


def seed_slots(conn: psycopg.Connection, tenant_id: uuid.UUID) -> list[str]:
    """Publish the 14 unassigned slots once (idempotent: an existing agent is left as it is)."""
    created = []
    for c in unassigned_slots():
        if latest(conn, tenant_id, c.agent_id) is None:
            publish(conn, tenant_id, c)
            created.append(c.agent_id)
    return created


def as_json(c: AgentConfig) -> dict:
    return asdict(c)
