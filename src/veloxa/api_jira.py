"""Admin > Integrations > Jira (OAuth 2.0 3LO). Organisation-level permissions:
- connecting, choosing the site and disconnecting require workspace ADMINISTRATION;
- reading and previewing/confirming writes require administration OR the explicit, audited and revocable
  company_reader grant (e.g. the CEO);
every connect, disconnect, read and write is an audit event (structure and keys only, never content or tokens).
No delete route, no transition route. See `jira_oauth` for the security model."""

from __future__ import annotations

import uuid

from fastapi import Header
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from . import audit, jira_oauth
from .db import connect, tenant_tx


class SiteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cloud_id: str = Field(min_length=1, max_length=100)


class CreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,19}$")
    issue_type_id: str = Field(pattern=r"^\d{1,12}$")
    summary: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=20000)
    fields: dict = Field(default_factory=dict)


class UpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,19}-\d{1,9}$")
    fields: dict = Field(default_factory=dict)
    comment: str = Field(default="", max_length=20000)


class CommitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str = Field(min_length=10, max_length=100)
    confirm: bool


def register(app, *, state, principal_of, now_fn, jira_factory=None) -> None:
    jira_oauth.load_env_file()

    def jira() -> jira_oauth.Jira | None:
        if "jira" not in state:
            c = jira_oauth.Client.from_env()
            if c is None:
                return None
            state["jira"] = (jira_factory or jira_oauth.Jira)(c, jira_oauth.Vault())
        return state["jira"]

    state["jira_get"] = jira

    def admin(conn, authorization, relations=("admin",)):
        p = principal_of(conn, authorization, now_fn())
        if isinstance(p, JSONResponse):
            return p
        with tenant_tx(conn, p.tenant_id, p.principal_id):
            ok = any(conn.execute("SELECT policy.holds(%s, %s, 'tenant', %s, %s) AS ok",
                                  (p.tenant_id, p.principal_id, str(p.tenant_id), rel)).fetchone()["ok"]
                     for rel in relations)
        if not ok:
            need = ("workspace administration" if relations == ("admin",)
                    else "workspace administration or the company-reader grant")
            return JSONResponse(status_code=403, content={"error": "not_permitted", "detail": f"{need} is required"})
        return p

    USE = ("admin", "company_reader")
    state["jira_permission"] = lambda conn, authorization: admin(conn, authorization, USE)

    def record(conn, tenant_id, principal_id, kind, detail):
        with tenant_tx(conn, tenant_id, principal_id):
            audit.append(conn, tenant_id, kind, detail)

    def run(authorization, fn, kind=None, detail=None, relations=USE):
        """Permission gate + Jira availability + safe errors + audit."""
        with connect(state["dsn"]) as conn:
            p = admin(conn, authorization, relations)
            if isinstance(p, JSONResponse):
                return p
            j = jira()
            if j is None:
                return JSONResponse(status_code=409, content={
                    "error": "not_configured", "detail": "Jira OAuth is not configured on the server.",
                    "env": jira_oauth.env_presence()})
            try:
                out = fn(j, str(p.tenant_id), p.principal_id)
            except jira_oauth.JiraError as e:
                if kind:
                    base = detail if isinstance(detail, dict) else {}
                    record(conn, p.tenant_id, p.principal_id, kind, {**base, "outcome": e.code})
                return JSONResponse(status_code=e.status, content={"error": e.code, "detail": str(e)})
            if kind:
                d = detail(out) if callable(detail) else (detail or {})
                record(conn, p.tenant_id, p.principal_id, kind, {**d, "outcome": "ok"})
            return out

    @app.get("/v1/integrations/jira/status")
    def status(authorization: str | None = Header(default=None)):
        with connect(state["dsn"]) as conn:
            p = admin(conn, authorization, USE)
            if isinstance(p, JSONResponse):
                return p
        j = jira()
        if j is None:
            return {"client_configured": False, "connected": False, "env": jira_oauth.env_presence(),
                    "callback_path": jira_oauth.CALLBACK_PATH, "requested_scopes": list(jira_oauth.SCOPES)}
        return j.status(str(p.tenant_id)) | {"env": jira_oauth.env_presence()}

    @app.post("/v1/integrations/jira/connect")
    def connect_start(authorization: str | None = Header(default=None)):
        return run(authorization, lambda j, t, pr: {"authorize_url": j.authorize_url(t, pr)},
                   "jira.connect_started", {}, ("admin",))

    @app.get("/v1/integrations/jira/callback")
    def callback(code: str | None = None, error: str | None = None,
                 state: str | None = None):  # Atlassian names the parameter "state" (shadows only here)
        j = jira()
        st = state
        if j is None:
            return RedirectResponse("/#/admin?jira=not_configured", status_code=303)
        if error:
            j.states.take(st)
            return RedirectResponse("/#/admin?jira=denied", status_code=303)
        try:
            tenant, principal = j.callback(code, st)
        except jira_oauth.JiraError as e:
            return RedirectResponse(f"/#/admin?jira={e.code}", status_code=303)
        g = j.vault.get(tenant)
        with connect(state_dsn()) as conn:
            record(conn, uuid.UUID(tenant), principal, "jira.connected",
                   {"sites": len(g.sites) if g else 0, "scopes": g.scopes if g else []})
        return RedirectResponse("/#/admin?jira=connected", status_code=303)

    def state_dsn():
        return state["dsn"]

    @app.post("/v1/integrations/jira/site")
    def site(body: SiteIn, authorization: str | None = Header(default=None)):
        return run(authorization, lambda j, t, pr: j.choose_site(t, body.cloud_id), "jira.site_selected",
                   {"cloud_id": body.cloud_id}, ("admin",))

    @app.post("/v1/integrations/jira/disconnect")
    def disconnect(authorization: str | None = Header(default=None)):
        return run(authorization, lambda j, t, pr: (j.disconnect(t), {"connected": False})[1], "jira.disconnected", {},
                   ("admin",))

    @app.get("/v1/integrations/jira/overdue")
    def overdue(project: str | None = None, authorization: str | None = Header(default=None)):
        return run(authorization, lambda j, t, pr: {"issues": j.overdue(t, project)}, "jira.read",
                   lambda out: {"op": "overdue", "keys": [i["key"] for i in out["issues"]]})

    @app.get("/v1/integrations/jira/issue/{key}/assignable")
    def assignable(key: str, query: str = "", authorization: str | None = Header(default=None)):
        return run(authorization, lambda j, t, pr: {"users": j.assignable(t, key, query)}, "jira.read",
                   {"op": "assignable", "keys": [key]})

    @app.get("/v1/integrations/jira/search")
    def search(jql: str, limit: int = 20, authorization: str | None = Header(default=None)):
        return run(authorization, lambda j, t, pr: {"issues": j.search(t, jql[:2000], limit)}, "jira.read",
                   lambda out: {"op": "search", "keys": [i["key"] for i in out["issues"]]})

    @app.get("/v1/integrations/jira/issue/{key}")
    def issue(key: str, authorization: str | None = Header(default=None)):
        return run(authorization, lambda j, t, pr: j.issue(t, key), "jira.read", {"op": "issue", "keys": [key]})

    @app.get("/v1/integrations/jira/projects")
    def projects(authorization: str | None = Header(default=None)):
        return run(authorization, lambda j, t, pr: {"projects": j.projects(t)})

    @app.get("/v1/integrations/jira/projects/{project}/issue-types")
    def issue_types(project: str, authorization: str | None = Header(default=None)):
        return run(authorization, lambda j, t, pr: {"issue_types": j.issue_types(t, project)})

    @app.post("/v1/integrations/jira/preview/create")
    def preview_create(body: CreateIn, authorization: str | None = Header(default=None)):
        return run(authorization, lambda j, t, pr: j.preview_create(t, pr, body.project, body.issue_type_id,
                                                                   body.summary, body.description, body.fields),
                   "jira.write_previewed", {"op": "create", "project": body.project})

    @app.post("/v1/integrations/jira/preview/update")
    def preview_update(body: UpdateIn, authorization: str | None = Header(default=None)):
        return run(authorization, lambda j, t, pr: j.preview_update(t, pr, body.key, body.fields, body.comment),
                   "jira.write_previewed", {"op": "update", "key": body.key, "fields": sorted(body.fields)})

    @app.post("/v1/integrations/jira/commit")
    def commit(body: CommitIn, authorization: str | None = Header(default=None)):
        return run(authorization, lambda j, t, pr: j.commit(t, pr, body.preview_id, body.confirm), "jira.write",
                   lambda out: {"op": out["operation"], "key": out["key"]})
