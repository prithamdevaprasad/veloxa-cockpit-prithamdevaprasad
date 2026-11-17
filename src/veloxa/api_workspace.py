"""Workspace API (migration 0017): who am I, departments, and workspace administration.

Every write goes through the guarded database functions (`tenancy.*`). The database, not this module, enforces:
- the caller is an ACTIVE workspace administrator of the tenant in the request's verified principal;
- self-escalation is refused;
- invitations are one-time and expire;
- only declared relations can be granted.

This module maps those refusals to 403 and 422.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Literal

from fastapi import Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import tenancy
from .authz.identity import Rejected
from .db import connect, tenant_tx


class DepartmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    department_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    name: str = Field(min_length=1, max_length=80)


class RoleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource_type: Literal["tenant", "department"]
    resource_id: str = Field(min_length=1, max_length=80)
    relation: Literal["admin", "admission_authoriser", "company_reader", "member", "head", "contributor", "reviewer"]


class InvitationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+$", max_length=200)
    display_name: str = Field(min_length=1, max_length=120)
    roles: list[RoleIn] = Field(default_factory=list, max_length=20)
    valid_days: int = Field(default=3, ge=1, le=14)


class GrantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource_type: Literal["tenant", "department", "evidence_source"]
    resource_id: str = Field(min_length=1, max_length=120)
    relation: Literal["admin", "admission_authoriser", "company_reader", "member", "head", "contributor",
                      "reviewer", "read"]
    subject: str = Field(pattern=r"^(person|department):[A-Za-z0-9_.-]{1,80}$")
    subject_relation: Literal["member"] | None = None
    reason: str = Field(min_length=1, max_length=500)


class StatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event: Literal["suspended", "reactivated", "removed"]
    reason: str = Field(min_length=1, max_length=500)


class AcceptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invitation_token: str = Field(min_length=20, max_length=200)


def _refusal(e: Exception):
    if isinstance(e, tenancy.NotPermitted):
        return JSONResponse(status_code=403, content={"error": "not_permitted", "detail": str(e)})
    return JSONResponse(status_code=422, content={"error": "invalid_request", "detail": str(e)})


def register(app, *, state, principal_of, now_fn, oidc_auth) -> None:
    def _me(conn, authorization):
        p = principal_of(conn, authorization, now_fn())
        return p

    @app.get("/v1/me")
    def me(authorization: str | None = Header(default=None)):
        with connect(state["dsn"]) as conn:
            p = _me(conn, authorization)
            if isinstance(p, JSONResponse):
                return p
            s = tenancy.settings(conn, p.tenant_id)
            m = tenancy.member(conn, p.tenant_id, p.principal_id)
            rel = tenancy.relations(conn, p.tenant_id, p.principal_id) if m else []
        acc = p.access
        return {
            "tenant": {"tenant_id": str(p.tenant_id), "slug": s.slug, "display_name": s.display_name,
                       "time_zone": s.time_zone, "synthetic": s.synthetic, "admission_mode": s.admission_mode,
                       "evidence_access": s.evidence_access, "status": s.status},
            "principal": {"principal_id": p.principal_id, "display_name": (m or {}).get("display_name"),
                          "email": p.email, "status": (m or {}).get("status", "hr_principal")},
            "relations": [{"tuple_id": str(r["tuple_id"]), "resource": f"{r['resource_type']}:{r['resource_id']}",
                           "relation": r["relation"]} for r in rel],
            "access": ({"mode": acc.mode, "company": acc.company, "sources": sorted(acc.systems),
                        "departments": sorted(acc.departments)} if acc is not None
                       else {"mode": "tenant_wide", "company": True, "sources": [], "departments": []}),
        }

    @app.get("/v1/departments")
    def departments(authorization: str | None = Header(default=None)):
        with connect(state["dsn"]) as conn:
            p = _me(conn, authorization)
            if isinstance(p, JSONResponse):
                return p
            with tenant_tx(conn, p.tenant_id, p.principal_id):
                rows = conn.execute(
                    "SELECT d.department_id, d.name, d.proposed_from,"
                    " (SELECT count(*) FROM policy.live_tuple t WHERE t.tenant_id = d.tenant_id"
                    "   AND t.resource_type = 'department' AND t.resource_id = d.department_id) AS role_count"
                    " FROM directory.department d WHERE d.tenant_id = %s ORDER BY d.name", (p.tenant_id,)).fetchall()
        return {"departments": [dict(r) for r in rows]}

    def _admin(conn, authorization):
        p = _me(conn, authorization)
        if isinstance(p, JSONResponse):
            return p
        with tenant_tx(conn, p.tenant_id, p.principal_id):
            ok = conn.execute("SELECT policy.holds(%s, %s, 'tenant', %s, 'admin') AS ok",
                              (p.tenant_id, p.principal_id, str(p.tenant_id))).fetchone()["ok"]
        if not ok:
            return JSONResponse(status_code=403, content={"error": "not_permitted",
                                                          "detail": "workspace administration is required"})
        return p

    @app.get("/v1/admin/members")
    def members(authorization: str | None = Header(default=None)):
        with connect(state["dsn"]) as conn:
            p = _admin(conn, authorization)
            if isinstance(p, JSONResponse):
                return p
            with tenant_tx(conn, p.tenant_id, p.principal_id):
                people = conn.execute("SELECT principal_id, display_name, email, status FROM directory.principal_status"
                                      " WHERE tenant_id = %s ORDER BY display_name", (p.tenant_id,)).fetchall()
                tuples = conn.execute("SELECT tuple_id, resource_type, resource_id, relation, subject_type,"
                                      " subject_id, subject_relation FROM policy.live_tuple WHERE tenant_id = %s",
                                      (p.tenant_id,)).fetchall()
        by_subject: dict[str, list] = {}
        for t in tuples:
            by_subject.setdefault(f"{t['subject_type']}:{t['subject_id']}", []).append(
                {"tuple_id": str(t["tuple_id"]), "resource": f"{t['resource_type']}:{t['resource_id']}",
                 "relation": t["relation"], "subject_relation": t["subject_relation"]})
        return {"members": [{**dict(m), "relations": by_subject.get(m["principal_id"], [])} for m in people],
                "source_grants": [g for k, v in by_subject.items() if k.startswith("department:") for g in
                                  [dict(x, subject=k) for x in v]]}

    @app.post("/v1/admin/departments")
    def create_department(body: DepartmentIn, authorization: str | None = Header(default=None)):
        with connect(state["dsn"]) as conn:
            p = _me(conn, authorization)
            if isinstance(p, JSONResponse):
                return p
            try:
                tenancy.create_department(conn, p.tenant_id, p.principal_id, body.department_id, body.name)
            except (tenancy.NotPermitted, tenancy.TenancyError) as e:
                return _refusal(e)
        return {"department_id": body.department_id, "name": body.name}

    @app.post("/v1/admin/invitations")
    def invite(body: InvitationIn, authorization: str | None = Header(default=None)):
        """The one-time token is returned ONCE, to be passed to the invitee over a trusted channel. Veloxa sends
        no e-mail and contacts nobody."""
        with connect(state["dsn"]) as conn:
            p = _me(conn, authorization)
            if isinstance(p, JSONResponse):
                return p
            try:
                pid, token = tenancy.invite(conn, p.tenant_id, p.principal_id, body.email, body.display_name,
                                            [r.model_dump() for r in body.roles], timedelta(days=body.valid_days))
            except (tenancy.NotPermitted, tenancy.TenancyError) as e:
                return _refusal(e)
        return {"principal_id": pid, "invitation_token": token, "expires_in_days": body.valid_days,
                "delivery": "not sent: give the token to the invitee yourself"}

    @app.post("/v1/admin/grants")
    def grant(body: GrantIn, authorization: str | None = Header(default=None)):
        with connect(state["dsn"]) as conn:
            p = _me(conn, authorization)
            if isinstance(p, JSONResponse):
                return p
            try:
                tid = tenancy.grant(conn, p.tenant_id, p.principal_id, body.resource_type, body.resource_id,
                                    body.relation, body.subject, body.reason, body.subject_relation)
            except (tenancy.NotPermitted, tenancy.TenancyError) as e:
                return _refusal(e)
        return {"tuple_id": str(tid)}

    @app.post("/v1/admin/grants/{tuple_id}/revoke")
    def revoke(tuple_id: str, authorization: str | None = Header(default=None)):
        import uuid

        with connect(state["dsn"]) as conn:
            p = _me(conn, authorization)
            if isinstance(p, JSONResponse):
                return p
            try:
                tenancy.revoke(conn, p.tenant_id, p.principal_id, uuid.UUID(tuple_id))
            except ValueError:
                return JSONResponse(status_code=422, content={"error": "invalid_request", "detail": "bad tuple id"})
            except (tenancy.NotPermitted, tenancy.TenancyError) as e:
                return _refusal(e)
        return {"revoked": tuple_id}

    @app.post("/v1/admin/members/{principal_id}/status")
    def set_status(principal_id: str, body: StatusIn, authorization: str | None = Header(default=None)):
        with connect(state["dsn"]) as conn:
            p = _me(conn, authorization)
            if isinstance(p, JSONResponse):
                return p
            try:
                tenancy.set_status(conn, p.tenant_id, p.principal_id, principal_id, body.event, body.reason)
            except (tenancy.NotPermitted, tenancy.TenancyError) as e:
                return _refusal(e)
        return {"principal_id": principal_id, "event": body.event}

    @app.post("/v1/invitations/accept")
    def accept(body: AcceptIn, authorization: str | None = Header(default=None)):
        """Bind the signed-in user's VERIFIED (issuer, subject) to an invitation. Needs per-tenant OIDC."""
        if oidc_auth is None:
            return JSONResponse(status_code=404, content={"error": "not_available",
                                                          "detail": "OIDC sign-in is not enabled (VELOXA_OIDC)"})
        presented = (authorization or "").partition(" ")[2].strip()
        with connect(state["dsn"]) as conn:
            v = oidc_auth.verify_subject(conn, presented, now_fn())
            if isinstance(v, Rejected):
                return JSONResponse(status_code=401, content={"error": "not_authenticated",
                                                              "detail": f"sign-in rejected ({v.reason})"})
            try:
                tid, pid = tenancy.accept_invitation(conn, body.invitation_token, v.issuer, v.subject)
            except tenancy.NotPermitted as e:
                return _refusal(e)
        if tid != v.tenant_id:
            return JSONResponse(status_code=403, content={"error": "not_permitted", "detail": "wrong workspace"})
        return {"tenant_id": str(tid), "principal_id": pid}
