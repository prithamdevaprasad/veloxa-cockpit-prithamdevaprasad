"""Tenants, settings, issuers and the workspace directory as DATA (migration 0017; C03, C02, C20).

**Operator functions** (privileged connection, e.g. the migrator; never the API):
- create a tenant;
- record settings versions;
- register a tenant's own OIDC issuer;
- create the FIRST administrator invitation.

**Workspace functions** (application role, through the guarded database functions):
- departments, invitations, role grants and revocations, status changes.
- The database itself checks that the caller is an active workspace administrator and refuses self-escalation.

**Synthetic-only bootstrap.** `bootstrap_synthetic_admin` activates a first administrator WITHOUT an identity
provider. It is refused for any tenant whose `directory.tenant.synthetic` is false. It exists for the local
development fixture and the Demo Lab only.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

import psycopg

from . import audit, config
from .db import tenant_tx

SLUG = re.compile(r"^[a-z0-9-]{2,40}$")
ADMISSION_MODES = ("release_schedule", "submission")
ACCESS_MODES = ("tenant_wide", "rebac")


class TenancyError(Exception):
    pass


@dataclass(frozen=True)
class TenantSettings:
    tenant_id: uuid.UUID
    slug: str
    time_zone: str
    synthetic: bool
    display_name: str
    admission_mode: str
    evidence_access: str
    status: str
    version: int


def _valid_tz(tz: str) -> str:
    from zoneinfo import ZoneInfo

    try:
        ZoneInfo(tz)
    except Exception:  # noqa: BLE001
        raise TenancyError(f"unknown time zone {tz!r}") from None
    return tz


def create_tenant(conn: psycopg.Connection, slug: str, tz: str, display_name: str, synthetic: bool = False,
                  admission_mode: str = "submission", evidence_access: str = "rebac") -> uuid.UUID:
    """Operator: a new tenant (fail-closed defaults: authorised submissions and per-source ReBAC)."""
    if not SLUG.match(slug):
        raise TenancyError("slug must be 2-40 characters of a-z, 0-9 and '-'")
    if admission_mode not in ADMISSION_MODES or evidence_access not in ACCESS_MODES:
        raise TenancyError("unknown admission or evidence-access mode")
    tid = config.tenant_uuid(slug)
    with conn.transaction():
        if conn.execute("SELECT 1 FROM directory.tenant WHERE slug = %s OR tenant_id = %s", (slug, tid)).fetchone():
            raise TenancyError(f"tenant {slug!r} already exists")
        conn.execute("INSERT INTO directory.tenant (tenant_id, slug, synthetic, time_zone) VALUES (%s, %s, %s, %s)",
                     (tid, slug, synthetic, _valid_tz(tz)))
    record_settings(conn, tid, display_name=display_name, admission_mode=admission_mode,
                    evidence_access=evidence_access, status="active")
    with tenant_tx(conn, tid, "operator"):
        audit.append(conn, tid, "tenant.created", {"slug": slug, "synthetic": synthetic,
                                                   "admission_mode": admission_mode,
                                                   "evidence_access": evidence_access})
    return tid


def record_settings(conn: psycopg.Connection, tenant_id: uuid.UUID, **changes) -> int:
    """Operator: append a new settings version (the latest version is in force)."""
    with tenant_tx(conn, tenant_id, "operator"):
        cur = conn.execute("SELECT * FROM directory.tenant_setting_current WHERE tenant_id = %s",
                           (tenant_id,)).fetchone()
        base = dict(cur) if cur else {"display_name": None, "admission_mode": "submission",
                                      "evidence_access": "rebac", "status": "active", "version": 0}
        new = {k: changes.get(k, base[k]) for k in ("display_name", "admission_mode", "evidence_access", "status")}
        if not new["display_name"]:
            raise TenancyError("a display name is required")
        v = base["version"] + 1
        conn.execute("INSERT INTO directory.tenant_setting (tenant_id, version, display_name, admission_mode,"
                     " evidence_access, status) VALUES (%s, %s, %s, %s, %s, %s)",
                     (tenant_id, v, new["display_name"], new["admission_mode"], new["evidence_access"],
                      new["status"]))
        audit.append(conn, tenant_id, "tenant.settings", {"version": v, **new})
    return v


def settings(conn: psycopg.Connection, tenant_id: uuid.UUID) -> TenantSettings | None:
    """The tenant's settings in force. A tenant created before 0017 has no settings row: it keeps the pre-0017
    behaviour (release schedule, tenant-wide evidence access) until the owner records settings for it."""
    t = conn.execute("SELECT * FROM directory.tenant WHERE tenant_id = %s", (tenant_id,)).fetchone()
    if t is None:
        return None
    with tenant_tx(conn, tenant_id):
        s = conn.execute("SELECT * FROM directory.tenant_setting_current WHERE tenant_id = %s",
                         (tenant_id,)).fetchone()
    if s is None:
        return TenantSettings(tenant_id, t["slug"], t["time_zone"], t["synthetic"], t["slug"], "release_schedule",
                              "tenant_wide", "active", 0)
    return TenantSettings(tenant_id, t["slug"], t["time_zone"], t["synthetic"], s["display_name"],
                          s["admission_mode"], s["evidence_access"], s["status"], s["version"])


def tenant_by_slug(conn: psycopg.Connection, slug: str) -> uuid.UUID:
    row = conn.execute("SELECT tenant_id FROM directory.tenant WHERE slug = %s", (slug,)).fetchone()
    if row is None:
        raise TenancyError(f"no tenant {slug!r}")
    return row["tenant_id"]


def add_issuer(conn: psycopg.Connection, tenant_id: uuid.UUID, issuer: str, audience: str, jwks_url: str,
               algorithms: tuple[str, ...] = ("RS256",), active: bool = True) -> int:
    """Operator: register (or retire, active=False) a tenant's OWN trusted OIDC issuer. No secrets are stored."""
    for name, url in (("issuer", issuer), ("jwks_url", jwks_url)):
        if not url.startswith("https://"):
            raise TenancyError(f"{name} must be an https URL")
    with conn.transaction():
        owner = conn.execute("SELECT tenant_id FROM directory.tenant_issuer WHERE issuer = %s LIMIT 1",
                             (issuer,)).fetchone()
        if owner and owner["tenant_id"] != tenant_id:
            raise TenancyError("this issuer is already trusted by another tenant")
        v = conn.execute("SELECT coalesce(max(version), 0) + 1 AS v FROM directory.tenant_issuer WHERE issuer = %s",
                         (issuer,)).fetchone()["v"]
        conn.execute("INSERT INTO directory.tenant_issuer (tenant_id, issuer, version, audience, jwks_url, algorithms,"
                     " active) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                     (tenant_id, issuer, v, audience, jwks_url, list(algorithms), active))
    with tenant_tx(conn, tenant_id, "operator"):
        audit.append(conn, tenant_id, "tenant.issuer", {"issuer": issuer, "version": v, "active": active})
    return v


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_principal_id() -> str:
    return f"person:u-{uuid.uuid4().hex[:16]}"


def operator_invite_admin(conn: psycopg.Connection, tenant_id: uuid.UUID, email: str, display_name: str,
                          valid_for: timedelta = timedelta(days=3)) -> tuple[str, str]:
    """Operator: the FIRST administrator's invitation. Returns (principal_id, one-time token). Only the token's
    hash is stored; the token is shown once to the operator."""
    pid, token = new_principal_id(), secrets.token_urlsafe(32)
    iid, th = uuid.uuid4(), token_hash(token)
    with tenant_tx(conn, tenant_id, "operator"):
        conn.execute("INSERT INTO directory.principal (tenant_id, principal_id, display_name, email)"
                     " VALUES (%s, %s, %s, %s)", (tenant_id, pid, display_name, email.strip().lower()))
        conn.execute("INSERT INTO directory.principal_event (tenant_id, principal_id, event, actor)"
                     " VALUES (%s, %s, 'invited', 'operator')", (tenant_id, pid))
        conn.execute("INSERT INTO directory.invitation (tenant_id, invitation_id, principal_id, email, token_sha256,"
                     " invited_by, expires_at) VALUES (%s, %s, %s, %s, %s, 'operator', now() + %s)",
                     (tenant_id, iid, pid, email.strip().lower(), th, valid_for))
        conn.execute("INSERT INTO directory.invitation_lookup (token_sha256, tenant_id, invitation_id)"
                     " VALUES (%s, %s, %s)", (th, tenant_id, iid))
        conn.execute("INSERT INTO directory.invitation_role (tenant_id, invitation_id, resource_type, resource_id,"
                     " relation) VALUES (%s, %s, 'tenant', %s, 'admin')", (tenant_id, iid, str(tenant_id)))
        audit.append(conn, tenant_id, "workspace.invited", {"principal": pid, "roles": ["tenant#admin"],
                                                            "by": "operator"})
    return pid, token


def bootstrap_synthetic_admin(conn: psycopg.Connection, tenant_id: uuid.UUID, principal_id: str,
                              display_name: str) -> None:
    """Operator, SYNTHETIC tenants only: an active administrator without an identity provider (local fixture)."""
    t = conn.execute("SELECT synthetic FROM directory.tenant WHERE tenant_id = %s", (tenant_id,)).fetchone()
    if t is None or not t["synthetic"]:
        raise TenancyError("the synthetic bootstrap is refused for a non-synthetic tenant")
    with tenant_tx(conn, tenant_id, "operator"):
        conn.execute("INSERT INTO directory.principal (tenant_id, principal_id, display_name) VALUES (%s, %s, %s)"
                     " ON CONFLICT DO NOTHING", (tenant_id, principal_id, display_name))
        conn.execute("INSERT INTO directory.principal_event (tenant_id, principal_id, event, actor, reason)"
                     " VALUES (%s, %s, 'activated', 'operator', 'synthetic bootstrap')", (tenant_id, principal_id))
        grant_as_operator(conn, tenant_id, "tenant", str(tenant_id), "admin", principal_id,
                          "synthetic bootstrap admin")
        audit.append(conn, tenant_id, "workspace.bootstrap_admin", {"principal": principal_id, "synthetic": True})


def add_synthetic_member(conn: psycopg.Connection, tenant_id: uuid.UUID, principal_id: str, display_name: str,
                         email: str | None = None) -> None:
    """Operator, SYNTHETIC tenants only: an active member without an identity provider (Demo Lab, tests).
    Grants nothing: roles are granted separately through the guarded functions."""
    t = conn.execute("SELECT synthetic FROM directory.tenant WHERE tenant_id = %s", (tenant_id,)).fetchone()
    if t is None or not t["synthetic"]:
        raise TenancyError("synthetic members are refused for a non-synthetic tenant")
    with tenant_tx(conn, tenant_id, "operator"):
        conn.execute("INSERT INTO directory.principal (tenant_id, principal_id, display_name, email)"
                     " VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                     (tenant_id, principal_id, display_name, email.lower() if email else None))
        conn.execute("INSERT INTO directory.principal_event (tenant_id, principal_id, event, actor, reason)"
                     " VALUES (%s, %s, 'activated', 'operator', 'synthetic member')", (tenant_id, principal_id))
        audit.append(conn, tenant_id, "workspace.synthetic_member", {"principal": principal_id})


def grant_as_operator(conn: psycopg.Connection, tenant_id: uuid.UUID, rtype: str, rid: str, relation: str,
                      principal_id: str, reason: str, subject_relation: str | None = None) -> uuid.UUID:
    """Operator-only tuple write (privileged connection). The API uses the guarded functions instead."""
    stype, sid = principal_id.split(":", 1)
    tid = uuid.uuid4()
    conn.execute("INSERT INTO policy.relationship_tuple (tenant_id, tuple_id, resource_type, resource_id, relation,"
                 " subject_type, subject_id, subject_relation, source) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                 (tenant_id, tid, rtype, rid, relation, stype, sid, subject_relation, f"operator: {reason}"))
    return tid


# ---------------------------------------------------------------------------------------------------------------
# Workspace administration through the guarded database functions (application role).
# ---------------------------------------------------------------------------------------------------------------
class NotPermitted(Exception):
    pass


def _guarded(conn, tenant_id, actor, sql, params, kind, detail):
    try:
        with tenant_tx(conn, tenant_id, actor):
            row = conn.execute(sql, params).fetchone()
            audit.append(conn, tenant_id, kind, {"by": actor, **detail})
            return row
    except psycopg.errors.InsufficientPrivilege as e:
        raise NotPermitted(e.diag.message_primary or "not permitted") from None
    except (psycopg.errors.CheckViolation, psycopg.errors.NoDataFound, psycopg.errors.UniqueViolation) as e:
        raise TenancyError(e.diag.message_primary or "refused") from None


def create_department(conn, tenant_id, actor, department_id: str, name: str, proposed_from: str | None = None):
    _guarded(conn, tenant_id, actor, "SELECT directory.admin_create_department(%s, %s, %s)",
             (department_id, name, proposed_from), "workspace.department_created",
             {"department": department_id})


def grant(conn, tenant_id, actor, rtype: str, rid: str, relation: str, subject: str, reason: str,
          subject_relation: str | None = None) -> uuid.UUID:
    stype, sid = subject.split(":", 1)
    row = _guarded(conn, tenant_id, actor, "SELECT policy.admin_grant(%s,%s,%s,%s,%s,%s,%s) AS id",
                   (rtype, rid, relation, stype, sid, subject_relation, reason), "workspace.granted",
                   {"resource": f"{rtype}:{rid}", "relation": relation, "subject": subject})
    return row["id"]


def revoke(conn, tenant_id, actor, tuple_id: uuid.UUID) -> None:
    _guarded(conn, tenant_id, actor, "SELECT policy.admin_revoke(%s)", (tuple_id,), "workspace.revoked",
             {"tuple": str(tuple_id)})


def invite(conn, tenant_id, actor, email: str, display_name: str, roles: list[dict],
           valid_for: timedelta = timedelta(days=3)) -> tuple[str, str]:
    """Returns (principal_id, one-time token). The token is returned once and never stored."""
    pid, token = new_principal_id(), secrets.token_urlsafe(32)
    _guarded(conn, tenant_id, actor, "SELECT directory.admin_invite(%s,%s,%s,%s,%s,%s::jsonb) AS id",
             (pid, display_name, email, token_hash(token), valid_for, json.dumps(roles)), "workspace.invited",
             {"principal": pid, "roles": [f"{r['resource_type']}:{r['resource_id']}#{r['relation']}" for r in roles]})
    return pid, token


def set_status(conn, tenant_id, actor, principal_id: str, event: str, reason: str) -> None:
    _guarded(conn, tenant_id, actor, "SELECT directory.admin_set_status(%s, %s, %s)", (principal_id, event, reason),
             "workspace.status", {"principal": principal_id, "event": event})


def accept_invitation(conn, token: str, issuer: str, subject: str) -> tuple[uuid.UUID, str]:
    """Bind a VERIFIED (issuer, subject) to the invited principal. Returns (tenant_id, principal_id)."""
    try:
        with conn.transaction():
            row = conn.execute("SELECT * FROM directory.accept_invitation(%s, %s, %s)",
                               (token_hash(token), issuer, subject)).fetchone()
            audit.append(conn, row["tenant_id"], "workspace.invitation_accepted", {"principal": row["principal_id"]})
    except psycopg.errors.InsufficientPrivilege as e:
        raise NotPermitted(e.diag.message_primary) from None
    except psycopg.errors.UniqueViolation as e:
        raise NotPermitted(e.diag.message_primary) from None
    return row["tenant_id"], row["principal_id"]


def member(conn, tenant_id: uuid.UUID, principal_id: str) -> dict | None:
    """The principal's directory record and status, or None."""
    with tenant_tx(conn, tenant_id, principal_id):
        return conn.execute("SELECT * FROM directory.principal_status WHERE tenant_id = %s AND principal_id = %s",
                            (tenant_id, principal_id)).fetchone()


def relations(conn, tenant_id: uuid.UUID, principal_id: str) -> list[dict]:
    """The principal's direct, unrevoked relations (for /v1/me)."""
    sid = principal_id.split(":", 1)[1]
    with tenant_tx(conn, tenant_id, principal_id):
        return [dict(r) for r in conn.execute(
            "SELECT t.tuple_id, t.resource_type, t.resource_id, t.relation FROM policy.relationship_tuple t"
            " WHERE t.tenant_id = %s AND t.subject_type = 'person' AND t.subject_id = %s AND t.subject_relation IS NULL"
            " AND NOT EXISTS (SELECT 1 FROM policy.relationship_revocation r WHERE r.tenant_id = t.tenant_id"
            " AND r.tuple_id = t.tuple_id) ORDER BY 2, 3, 4", (tenant_id, sid)).fetchall()]
