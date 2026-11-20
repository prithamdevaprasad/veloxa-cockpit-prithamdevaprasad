"""Per-document evidence authorisation for Ask and the other requester surfaces (C20 ReBAC + C21 ABAC).

This is NOT a new permission model. It evaluates the existing C20 relationship tuples and the existing ABAC rules
for each cited record, using the record's source system and data class.

**Modes** (tenant setting `evidence_access`, migration 0017):
- `tenant_wide`: the pre-0017 behaviour, kept for tenants created earlier (the hackathon tenant).
  - Underneath: tenant RLS, sealed-K visibility, the protected-pay RLS policies and e-mail party checks.
  - This mode is never chosen for a new tenant.
- `rebac`: the default for every new tenant. A record is visible to a principal only if:
  1. the principal is ACTIVE in the workspace directory;
  2. its data class is not protected (payroll and sensitive personal records are never readable here; C19 is
     the only path for protected aggregates);
  3. the principal holds `tenant:<t>#company_reader` (an explicit, admin-granted, company-wide read; e.g. the
     CEO), or `evidence_source:<system>#read`, either directly or through `department:<d>#member`;
  4. the department relation is satisfied by head, contributor, reviewer or member of that department.

**Gate.** `gate()` applies these rules to a tool result:
- A fact whose inputs include ANY record the principal may not read is withheld whole. Aggregates built from
  unreadable records therefore never leak.
- The withheld records are reported as a count in an unknowns line. Their content is never disclosed.

**History.** Current authorisation is always required. An as-of question never gains access from history, and
historical eligibility (C32) never substitutes for current permission.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace

import psycopg

from ..db import tenant_tx
from .current import PROTECTED

DEPARTMENT_MEMBER_RELATIONS = ("member", "head", "contributor", "reviewer")


@dataclass(frozen=True)
class EvidenceAccess:
    mode: str                                   # tenant_wide | rebac
    company: bool = False
    systems: frozenset[str] = frozenset()
    active: bool = True
    departments: frozenset[str] = field(default_factory=frozenset)

    def allows(self, system: str, data_class: str) -> bool:
        if data_class in PROTECTED:
            return False                        # never through this path (C19 only), in either mode
        if self.mode == "tenant_wide":
            return True
        if not self.active:
            return False
        return self.company or system in self.systems


TENANT_WIDE = EvidenceAccess("tenant_wide")
NONE = EvidenceAccess("rebac", active=False)


def access_for(conn: psycopg.Connection, tenant_id: uuid.UUID, principal_id: str, mode: str) -> EvidenceAccess:
    """The principal's CURRENT evidence access, from unrevoked C20 tuples. Fails closed (no access) on error."""
    if mode == "tenant_wide":
        return TENANT_WIDE
    if not principal_id.startswith("person:"):
        return NONE
    sid = principal_id.split(":", 1)[1]
    try:
        with tenant_tx(conn, tenant_id, principal_id):
            status = conn.execute("SELECT status FROM directory.principal_status WHERE tenant_id = %s"
                                  " AND principal_id = %s", (tenant_id, principal_id)).fetchone()
            if not status or status["status"] != "active":
                return NONE
            company = conn.execute(
                "SELECT 1 FROM policy.live_tuple t WHERE t.tenant_id = %s AND t.resource_type = 'tenant'"
                " AND t.resource_id = %s AND t.relation = 'company_reader' AND t.subject_type = 'person'"
                " AND t.subject_id = %s AND t.subject_relation IS NULL",
                (tenant_id, str(tenant_id), sid)).fetchone() is not None
            depts = frozenset(r["resource_id"] for r in conn.execute(
                "SELECT t.resource_id FROM policy.live_tuple t WHERE t.tenant_id = %s"
                " AND t.resource_type = 'department' AND t.relation = ANY(%s) AND t.subject_type = 'person'"
                " AND t.subject_id = %s AND t.subject_relation IS NULL",
                (tenant_id, list(DEPARTMENT_MEMBER_RELATIONS), sid)).fetchall())
            systems = frozenset(r["resource_id"] for r in conn.execute(
                "SELECT t.resource_id FROM policy.live_tuple t WHERE t.tenant_id = %s"
                " AND t.resource_type = 'evidence_source' AND t.relation = 'read' AND ("
                " (t.subject_type = 'person' AND t.subject_id = %s AND t.subject_relation IS NULL)"
                " OR (t.subject_type = 'department' AND t.subject_relation = 'member' AND t.subject_id = ANY(%s)))",
                (tenant_id, sid, list(depts))).fetchall())
    except psycopg.Error:
        return NONE
    return EvidenceAccess("rebac", company, systems, True, depts)


def gate(result, access: EvidenceAccess):
    """(filtered ToolResult, withheld record count). Only for `rebac` access; `tenant_wide` passes through."""
    if access.mode == "tenant_wide":
        return result, 0
    readable = {e.record_id for e in result.evidence if access.allows(e.system, e.data_class)}
    denied = {e.record_id for e in result.evidence} - readable
    facts = [f for f in result.facts if set(f.inputs) <= readable]
    hidden_inputs = {i for f in result.facts for i in f.inputs} - readable
    withheld = len(denied | hidden_inputs)
    kept_ids = {i for f in facts for i in f.inputs} | (readable if not result.facts else set())
    evidence = [e for e in result.evidence if e.record_id in readable and (e.record_id in kept_ids or not facts)]
    if not withheld:
        return result, 0
    gaps = list(result.gaps) + [f"{withheld} record(s) relevant to this step are outside your authorised sources; "
                                "their content is not shown"]
    status = result.status if facts or (evidence and not result.facts) else "no_data"
    return replace(result, status=status, facts=facts, evidence=evidence, gaps=gaps), withheld
