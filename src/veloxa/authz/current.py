"""Current authorization: C20 ReBAC and C21 ABAC (ARCHITECTURE.md §10.1 layers 2-3, §10.3, §10.7, §25.1).

Hackathon profile (§25.1): ReBAC is relationship tuples in PostgreSQL with a SpiceDB-compatible shape behind
`RelationshipStore`; ABAC is in-process rules behind `PolicyDecisionPoint`. SpiceDB and OPA servers are not run;
their production deployment is OUTSTANDING. RLS (layer 1) is always underneath.

`authorize` answers ONE question: may this REQUESTER access this resource NOW? It never consults historical ACL
evaluation (C32), which can only narrow, never grant. Unknown or missing anything is a denial.

ReBAC: a subject holds `relation` on a resource directly, or through `group:<id>#member` tuples, expanded at
query time over CURRENT (unrevoked) tuples only, cycle-safe and depth-bounded.
ABAC (policy `abac/1`): deny overrides; every rule needs its attributes present (missing -> deny):
- the resource's data class must be known; protected pay classes are never readable through this layer except
  `read_value` on a `pay` resource with an explicit ReBAC grant (an individually authorised value, §15.3);
- the purpose must be a declared purpose; tenant of principal and resource must match.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import psycopg

from ..db import tenant_tx

ABAC_POLICY_VERSION = "abac/1"
PURPOSES = frozenset({"answer", "briefing", "evidence_view", "disclosure_background", "export", "interview"})
DATA_CLASSES = frozenset({"internal", "confidential", "personal", "sensitive_personal", "payroll_aggregate",
                          "private_comms", "suppressed", "assumption"})
PROTECTED = frozenset({"sensitive_personal", "payroll_aggregate"})
MAX_DEPTH = 8


@dataclass(frozen=True)
class Resource:
    resource_type: str
    resource_id: str
    tenant_id: uuid.UUID
    data_class: str | None


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    relation_path: tuple[str, ...] = ()
    policy_version: str = ABAC_POLICY_VERSION
    layers: dict = field(default_factory=dict)


class RelationshipStore:
    """C20 behind the §5.3 RelationshipStore contract; PostgreSQL tuples (hackathon profile)."""

    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def _tuples(self, tenant_id, resource_type, resource_id, relation):
        from contextlib import nullcontext

        from psycopg import pq

        # Inside a caller's transaction (e.g. the C19 gate), reuse its tenant and principal context: opening a new
        # tenant_tx there would reset app.principal_id for the rest of that transaction.
        idle = self.conn.info.transaction_status == pq.TransactionStatus.IDLE
        with tenant_tx(self.conn, tenant_id) if idle else nullcontext():
            return self.conn.execute(
                "SELECT t.subject_type, t.subject_id, t.subject_relation, t.tuple_id FROM policy.relationship_tuple t"
                " WHERE t.tenant_id = %s AND t.resource_type = %s AND t.resource_id = %s AND t.relation = %s"
                " AND NOT EXISTS (SELECT 1 FROM policy.relationship_revocation r WHERE r.tenant_id = t.tenant_id"
                " AND r.tuple_id = t.tuple_id)", (tenant_id, resource_type, resource_id, relation)).fetchall()

    def check(self, tenant_id, subject: str, relation: str, resource_type: str, resource_id: str,
              _depth: int = 0, _seen=None) -> tuple[bool, tuple[str, ...]]:
        """(allowed, path). `subject` is a typed principal, e.g. 'person:T001'."""
        if _depth > MAX_DEPTH:
            return False, ()
        seen = _seen or set()
        key = (resource_type, resource_id, relation)
        if key in seen:
            return False, ()
        seen = seen | {key}
        for t in self._tuples(tenant_id, resource_type, resource_id, relation):
            ref = f"{t['subject_type']}:{t['subject_id']}"
            if t["subject_relation"] is None and ref == subject:
                return True, (f"{resource_type}:{resource_id}#{relation}@{ref}",)
            if t["subject_relation"] == "member" and t["subject_type"] == "group":
                ok, path = self.check(tenant_id, subject, "member", "group", t["subject_id"], _depth + 1, seen)
                if ok:
                    return True, (f"{resource_type}:{resource_id}#{relation}@{ref}#member",) + path
        return False, ()


def abac(principal_tenant: uuid.UUID, action: str, resource: Resource, purpose: str | None,
         rebac_granted: bool) -> Decision:
    """C21 behind PolicyDecisionPoint: deterministic, versioned, deny-overrides, missing attribute -> deny."""
    if purpose is None or purpose not in PURPOSES:
        return Decision(False, "purpose_missing_or_undeclared")
    if resource.data_class is None or resource.data_class not in DATA_CLASSES:
        return Decision(False, "data_class_unknown")
    if resource.tenant_id != principal_tenant:
        return Decision(False, "tenant_mismatch")
    if resource.data_class in PROTECTED and not (action == "read_value"
                                                 and resource.resource_type in ("pay", "pay_document")):
        return Decision(False, "protected_class")
    if resource.data_class in PROTECTED and purpose != "disclosure_background":
        return Decision(False, "protected_value_only_as_disclosure_background")
    if not rebac_granted:
        return Decision(False, "no_relationship")
    return Decision(True, "allowed")


def authorize(conn: psycopg.Connection, principal_id: str | None, tenant_id: uuid.UUID, action: str,
              resource: Resource, purpose: str | None) -> Decision:
    """Current authorization of the requester: ReBAC AND ABAC (RLS underneath). Fails closed."""
    if not principal_id or not principal_id.startswith("person:"):
        return Decision(False, "principal_unresolved")
    try:
        granted, path = RelationshipStore(conn).check(tenant_id, principal_id, action, resource.resource_type,
                                                      resource.resource_id)
    except Exception:  # noqa: BLE001 - an unavailable relationship store denies
        return Decision(False, "rebac_unavailable")
    d = abac(tenant_id, action, resource, purpose, granted)
    return Decision(d.allowed, d.reason, path if d.allowed else (), d.policy_version,
                    {"rebac": granted, "abac": d.reason})
