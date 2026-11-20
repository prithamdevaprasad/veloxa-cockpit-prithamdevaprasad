"""Explicit company-wide read (`tenant:<t>#company_reader`): granted only by a recorded owner authorisation.

An HR job title never creates access. A grant needs:
- a VERIFIED principal: `person:<employee ID>` that resolves to exactly one person in the latest sealed HR export
  and is not recorded as having left;
- a named approver and a decision record, and a declared purpose (`interview`, `cockpit`);
- an audit event (`access.company_reader_granted`), with the tuple ID needed to revoke it.

Scope: every evidence source of the tenant EXCEPT what ABAC refuses anyway (protected classes such as payroll,
sensitive personal records; undeclared purposes). Revocation appends `policy.relationship_revocation` plus an audit
event (`access.company_reader_revoked`); the evidence gate and the agent runtime stop honouring the tuple at once.
With no grant, both deny by default.
"""

from __future__ import annotations

import uuid

import psycopg

from . import audit, ledger, tenancy
from .db import tenant_tx

PURPOSES = frozenset({"interview", "cockpit"})


class GrantRefused(ValueError):
    pass


def _verified_person(conn: psycopg.Connection, tenant_id: uuid.UUID, principal_id: str) -> dict:
    if not principal_id.startswith("person:") or len(principal_id) <= 7:
        raise GrantRefused("the principal must be person:<employee ID>")
    emp = principal_id.split(":", 1)[1]
    k = ledger.sealed_watermark(conn, tenant_id)
    if not k:
        raise GrantRefused("no sealed HR export to verify the principal against")
    with tenant_tx(conn, tenant_id):
        rows = conn.execute("SELECT emp_id, exit_date FROM projection.person WHERE batch_seq = (SELECT max(batch_seq)"
                            " FROM projection.person WHERE batch_seq <= %s) AND emp_id = %s", (k, emp)).fetchall()
    if len(rows) != 1:
        raise GrantRefused("the principal does not resolve to exactly one person in the latest sealed HR export")
    if rows[0]["exit_date"] is not None:
        raise GrantRefused("the principal is recorded as having left")
    return dict(rows[0])


def grant_company_reader(conn: psycopg.Connection, tenant_id: uuid.UUID, principal_id: str, *, approved_by: str,
                         decision_ref: str, purposes: list[str]) -> uuid.UUID:
    """Privileged (operator) connection. Idempotent: an existing live grant is returned, not duplicated."""
    if not approved_by.strip() or not decision_ref.strip():
        raise GrantRefused("an approver and a decision record are required")
    if not purposes or set(purposes) - PURPOSES:
        raise GrantRefused(f"purposes must be a non-empty subset of {sorted(PURPOSES)}")
    _verified_person(conn, tenant_id, principal_id)
    sid = principal_id.split(":", 1)[1]
    with tenant_tx(conn, tenant_id):
        live = conn.execute("SELECT tuple_id FROM policy.live_tuple WHERE resource_type = 'tenant' AND resource_id = %s"
                            " AND relation = 'company_reader' AND subject_type = 'person' AND subject_id = %s",
                            (str(tenant_id), sid)).fetchone()
    if live:
        return live["tuple_id"]
    reason = f"company-wide read; approved by {approved_by}; decision {decision_ref}; purposes {','.join(purposes)}"
    with tenant_tx(conn, tenant_id):
        tid = tenancy.grant_as_operator(conn, tenant_id, "tenant", str(tenant_id), "company_reader", principal_id,
                                        reason)
        audit.append(conn, tenant_id, "access.company_reader_granted",
                     {"principal": principal_id, "tuple": str(tid), "approved_by": approved_by,
                      "decision": decision_ref, "purposes": sorted(purposes)})
    return tid


def revoke(conn: psycopg.Connection, tenant_id: uuid.UUID, tuple_id: uuid.UUID, *, by: str, reason: str) -> None:
    if not by.strip() or not reason.strip():
        raise GrantRefused("who revoked it and why are required")
    with tenant_tx(conn, tenant_id):
        row = conn.execute("SELECT 1 FROM policy.live_tuple WHERE tuple_id = %s", (tuple_id,)).fetchone()
        if row is None:
            raise GrantRefused("no live grant with that tuple ID")
        conn.execute("INSERT INTO policy.relationship_revocation (tenant_id, tuple_id) VALUES (%s, %s)",
                     (tenant_id, tuple_id))
        audit.append(conn, tenant_id, "access.company_reader_revoked", {"tuple": str(tuple_id), "by": by,
                                                                       "reason": reason})


def live_grants(conn: psycopg.Connection, tenant_id: uuid.UUID) -> list[dict]:
    with tenant_tx(conn, tenant_id):
        return [dict(r) for r in conn.execute(
            "SELECT tuple_id, subject_id, source, created_at FROM policy.live_tuple WHERE resource_type = 'tenant'"
            " AND relation = 'company_reader' ORDER BY created_at").fetchall()]
