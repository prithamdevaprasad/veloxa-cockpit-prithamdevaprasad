"""Authorization for agent executions (ARCHITECTURE.md §10.1 layers, §10.6, §14.2).

Three separate questions, never merged:
- **Agent-tool policy (layer 4, C22):** may this agent session invoke this tool for this purpose? The approved
  production engine is Cedar behind `PolicyDecisionPoint`; it is NOT deployed. The hackathon profile (§25) evaluates
  agent-tool rules in-process behind the same interface: `InProcessAgentToolPDP` (policy `agent-tools/1`).
  `CedarAgentToolPDP` is the production adapter slot and denies everything until Cedar exists (fail closed).
- **AZ (interviewer, now):** current authorization of the INTERVIEWER (C02 identity -> C20 ReBAC -> C21 ABAC) for
  each evidence item, purpose `interview`. Never computed from historical ACLs.
- **KB (represented person, at the read basis):** C32 historical eligibility of the represented person. Unassigned
  agents represent nobody, so their KB is empty. `unknown` is `denied`.
- **POL (tenant, interview):** the data classes interviews may use.
Evidence an agent may use is exactly KB ∩ AZ ∩ POL. An organisational role is never an authorization grant.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Protocol

import psycopg

from ..authz.current import Resource, authorize
from . import registry
from . import tools as agent_tools

AGENT_TOOL_POLICY_VERSION = "agent-tools/1"
INTERVIEW_POLICY_VERSION = "interview-pol/1"
# Classes interviews may use (assumption pending owner confirmation of POL(τ, interview), §14.2). Protected pay
# classes, suppressed material and assumption-class persona items are never evidence.
INTERVIEW_ALLOWED_CLASSES = frozenset({"internal", "confidential", "personal", "private_comms"})


@dataclass(frozen=True)
class ToolDecision:
    allowed: bool
    reason: str
    policy_version: str


@dataclass(frozen=True)
class ToolRequest:
    tenant_id: uuid.UUID
    agent: registry.AgentConfig
    agent_tenant_id: uuid.UUID
    interviewer_id: str | None
    tool_id: str
    purpose: str | None


class AgentToolPDP(Protocol):
    def decide(self, request: ToolRequest) -> ToolDecision: ...


class CedarAgentToolPDP:
    """Production slot for C22 (Cedar). Not deployed: every decision is a denial, never a simulation."""

    def decide(self, request: ToolRequest) -> ToolDecision:
        return ToolDecision(False, "cedar_not_deployed", "cedar/unavailable")


class InProcessAgentToolPDP:
    """Hackathon-profile agent-tool policy (§25: in-process rules behind PolicyDecisionPoint). Deny overrides;
    every rule needs its attributes; anything missing denies."""

    def decide(self, r: ToolRequest) -> ToolDecision:
        def deny(reason: str) -> ToolDecision:
            return ToolDecision(False, reason, AGENT_TOOL_POLICY_VERSION)

        if r.tenant_id is None or r.agent_tenant_id != r.tenant_id:
            return deny("tenant_mismatch")
        if not r.interviewer_id or not r.interviewer_id.startswith("person:"):
            return deny("interviewer_unresolved")
        if r.purpose != "interview":
            return deny("purpose_missing_or_undeclared")
        if r.agent.activation not in ("synthetic_test", "active"):
            return deny("agent_not_activated")
        spec = agent_tools.REGISTRY.get(r.tool_id)
        if spec is None:
            return deny("undeclared_tool")
        if r.tool_id not in r.agent.tools:
            return deny("tool_not_configured_for_agent")
        if spec.capability not in r.agent.capabilities:
            return deny("capability_missing")
        if spec.capability not in registry.CATEGORY_CAPABILITIES.get(r.agent.category, frozenset()):
            return deny("capability_outside_category")             # defence in depth against a bad config row
        if not spec.read_only:
            return deny("action_tools_forbidden")
        if spec.availability != "available":
            return deny(spec.availability)
        if spec.purpose != r.purpose:
            return deny("purpose_not_permitted_for_tool")
        return ToolDecision(True, "allowed", AGENT_TOOL_POLICY_VERSION)


class EvidenceAuthorizer(Protocol):
    def allowed(self, tenant_id: uuid.UUID, interviewer_id: str, system: str, data_class: str) -> tuple[bool, str]:
        ...


@dataclass
class CurrentAuthorization:
    """AZ: the interviewer's CURRENT authorization (ReBAC relation `read` on `evidence_source:<system>`, then ABAC
    `abac/1`, purpose `interview`). Memoised per request; fails closed."""
    conn: psycopg.Connection
    _memo: dict = field(default_factory=dict)

    def allowed(self, tenant_id, interviewer_id, system, data_class):
        key = (tenant_id, interviewer_id, system, data_class)
        if key not in self._memo:
            d = authorize(self.conn, interviewer_id, tenant_id, "read",
                          Resource("evidence_source", system, tenant_id, data_class), "interview")
            self._memo[key] = (d.allowed, d.reason)
        return self._memo[key]


class KnowledgeBoundary(Protocol):
    def labels(self, agent: registry.AgentConfig, record_ids: list[str], basis) -> dict[str, str | None]: ...


@dataclass
class C32KnowledgeBoundary:
    """KB via the C32 historical ACL evaluator for the represented person (ELIGIBILITY_DURING_WINDOW). An agent
    that represents nobody has an empty KB. Every non-`allowed` outcome excludes the item."""
    conn: psycopg.Connection

    def labels(self, agent, record_ids, basis):
        if not agent.represented_person or not record_ids:
            return {r: None for r in record_ids}
        from ..acl.historical import HistoricalAclEvaluator, knowledge_basis

        try:
            results = HistoricalAclEvaluator(self.conn).evaluate_many(
                agent.represented_person, "read", record_ids, basis, "ELIGIBILITY_DURING_WINDOW")
        except Exception:  # noqa: BLE001 - evaluator unavailable: no historical grants (§5.2 C32 failure)
            return {r: None for r in record_ids}
        return {r: knowledge_basis(res) for r, res in results.items()}


def policy_allows(data_class: str) -> bool:
    return data_class in INTERVIEW_ALLOWED_CLASSES
