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


class SyncIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    projects: list[str] = Field(min_length=1, max_length=10)
    department_id: str | None = Field(default=None, max_length=64)
    source_type: str | None = Field(default=None, max_length=64)
    mode: str = Field(default="incremental", pattern="^(incremental|initial)$")
    dry_run: bool = True


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

    def permission_for(conn, p) -> bool:
        """The same organisation-level rule for a principal already resolved (Ask threads)."""
        with tenant_tx(conn, p.tenant_id, p.principal_id):
            return any(conn.execute("SELECT policy.holds(%s, %s, 'tenant', %s, %s) AS ok",
                                    (p.tenant_id, p.principal_id, str(p.tenant_id), rel)).fetchone()["ok"]
                       for rel in USE)

    state["jira_permission_for"] = permission_for
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

    def _scheduled_sync():
        """Optional pull schedule for the tenant VELOXA_JIRA_SYNC_TENANT (slug):
        VELOXA_JIRA_SYNC="PROJ[,PROJ]:department:source_type", every
        VELOXA_JIRA_SYNC_EVERY_MIN minutes (default 15), as VELOXA_JIRA_SYNC_ACTOR (a workspace administrator).
        Each run submits only changed issues to the data room (pending review); nothing is admitted here."""
        import os as _os
        import threading
        import time as _time

        spec, actor = _os.environ.get("VELOXA_JIRA_SYNC"), _os.environ.get("VELOXA_JIRA_SYNC_ACTOR")
        if not spec or not actor or spec.count(":") != 2:
            return
        projects, dept, stype = spec.split(":")
        every = max(5, int(_os.environ.get("VELOXA_JIRA_SYNC_EVERY_MIN", "15"))) * 60

        def loop():
            from . import connectors

            while True:
                _time.sleep(every)
                try:
                    j = jira()
                    if j is None:
                        continue
                    with connect(state["dsn"]) as conn:
                        tid = conn.execute("SELECT tenant_id FROM directory.tenant WHERE slug = %s",
                                           (_os.environ.get("VELOXA_JIRA_SYNC_TENANT", ""),)).fetchone()
                    if tid is None or not j.status(str(tid["tenant_id"])).get("connected"):
                        continue
                    tid = tid["tenant_id"]
                    con = connectors.oauth_connector(j, str(tid), projects.split(","))
                    with connect(state["dsn"]) as conn:
                        out = connectors.sync(conn, state["store"](), tid, con, actor=actor,
                                              department_id=dept, source_type=stype)
                        record(conn, tid, actor, "jira.sync", {"projects": projects.split(","), "scheduled": True,
                                                                **{k: (str(v) if k == "submission_id" and v else v)
                                                                   for k, v in out.items() if k != "error"}})
                except Exception:  # noqa: BLE001, S110 - a failed run is retried at the next interval
                    pass

        threading.Thread(target=loop, daemon=True, name="jira-sync").start()

    _scheduled_sync()

    @app.post("/v1/integrations/jira/sync")
    def sync(body: SyncIn, authorization: str | None = Header(default=None)):
        """Import Jira issues into the GOVERNED data room over the OAuth connection: changed issues (by key and
        update time) become one submission pending review; nothing is admitted here. dry_run (the default) fetches
        and reports what would be submitted without submitting. Administration only."""
        import re as _re

        from . import connectors

        if any(not _re.fullmatch(r"[A-Z][A-Z0-9_]{1,19}", k) for k in body.projects):
            return JSONResponse(status_code=422, content={"error": "invalid_request", "detail": "project keys only"})
        with connect(state["dsn"]) as conn:
            p = admin(conn, authorization, ("admin",))
            if isinstance(p, JSONResponse):
                return p
            j = jira()
            if j is None:
                return JSONResponse(status_code=409, content={"error": "not_configured",
                                                              "detail": "Jira OAuth is not configured on the server."})
            con = connectors.oauth_connector(j, str(p.tenant_id), body.projects)
            with tenant_tx(conn, p.tenant_id, p.principal_id):
                prior = conn.execute("SELECT scope FROM runtime.connector_run WHERE connector = 'jira'"
                                     " AND status = 'ok'").fetchall()
                last = conn.execute("SELECT cursor_after FROM runtime.connector_run WHERE connector = 'jira' AND"
                                    " status = 'ok' ORDER BY started_at DESC LIMIT 1").fetchone()
            if body.dry_run:
                try:
                    cursor = None if body.mode == "initial" or not last else last["cursor_after"]
                    records, newest = con.fetch(cursor)
                except connectors.ConnectorFailed as e:
                    return JSONResponse(status_code=502, content={"error": "jira_failed", "detail": str(e)})
                seen = {}
                for r in prior:
                    seen |= (r["scope"] or {}).get("submitted_versions", {})
                new = connectors.changed(records, seen)
                record(conn, p.tenant_id, p.principal_id, "jira.sync_previewed",
                       {"projects": body.projects, "fetched": len(records), "would_submit": len(new)})
                return {"dry_run": True, "projects": body.projects, "fetched": len(records), "would_submit": len(new),
                        "already_submitted_unchanged": len(records) - len(new), "cursor": newest,
                        "sample": [{"key": r["key"], "updated": r["updated"],
                                    "fields": sorted(r["fields"])} for r in new[:10]],
                        "next": "Run again with dry_run=false, a department and an approved source type to submit "
                                "these issues for review (nothing is admitted until reviewed and authorised)."}
            if not body.department_id or not body.source_type:
                return JSONResponse(status_code=422, content={"error": "invalid_request", "detail":
                                    "a department and an approved data-room source type are required to submit"})
            try:
                out = connectors.sync(conn, state["store"](), p.tenant_id, con, actor=p.principal_id,
                                      department_id=body.department_id, source_type=body.source_type, mode=body.mode)
            except Exception as e:  # noqa: BLE001 - intake refusals (no contributor relation, unapproved type)
                record(conn, p.tenant_id, p.principal_id, "jira.sync", {"projects": body.projects,
                                                                         "outcome": type(e).__name__})
                return JSONResponse(status_code=409, content={"error": "not_submitted", "detail": str(e)[:300]})
            record(conn, p.tenant_id, p.principal_id, "jira.sync", {"projects": body.projects, **{
                k: (str(v) if k == "submission_id" and v else v) for k, v in out.items() if k != "error"}})
            return {k: (str(v) if k == "submission_id" and v else v) for k, v in out.items()}

    @app.get("/v1/integrations/jira/migration-preview")
    def migration_preview(authorization: str | None = Header(default=None)):
        """What admitted company records would become in Jira: projects, candidate tasks, duplicates, missing owners
        and deadlines, excluded sensitive records, proposed destinations. READ-ONLY: nothing is written."""
        from datetime import UTC, datetime

        from . import ledger, migration
        from .temporal import Mode, resolve_basis

        with connect(state["dsn"]) as conn:
            p = admin(conn, authorization, USE)
            if isinstance(p, JSONResponse):
                return p
            tz = conn.execute("SELECT time_zone FROM directory.tenant WHERE tenant_id = %s",
                              (p.tenant_id,)).fetchone()["time_zone"]
            basis = resolve_basis(p.tenant_id, ledger.sealed_releases(conn, p.tenant_id), Mode.CURRENT, None, tz,
                                  datetime.now(UTC))
            j = jira()
            live = j if (j is not None and j.status(str(p.tenant_id)).get("connected")) else None
            out = migration.preview(conn, basis, tz, live, str(p.tenant_id))
            record(conn, p.tenant_id, p.principal_id, "jira.migration_previewed", out["summary"])
            return out

    @app.get("/v1/integrations/jira/overdue")
    def overdue(project: str | None = None, authorization: str | None = Header(default=None)):
        return run(authorization, lambda j, t, pr: {"issues": j.overdue(t, project)}, "jira.read",
                   lambda out: {"op": "overdue", "keys": [i["key"] for i in out["issues"]]})

    @app.get("/v1/integrations/jira/issue/{key}/assignable")
    def assignable(key: str, query: str = "", authorization: str | None = Header(default=None)):
        return run(authorization, lambda j, t, pr: {"users": j.assignable(t, key, query)}, "jira.read",
                   {"op": "assignable", "keys": [key]})

    @app.get("/v1/integrations/jira/projects/{project}/assignable")
    def project_assignable(project: str, query: str = "", authorization: str | None = Header(default=None)):
        return run(authorization, lambda j, t, pr: {"users": j.assignable(t, "", query, project=project[:20])},
                   "jira.read", {"op": "assignable", "project": project[:20]})

    @app.get("/v1/integrations/jira/users/lookup")
    def lookup_user(query: str, project: str | None = None, authorization: str | None = Header(default=None)):
        return run(authorization, lambda j, t, pr: {"users": j.lookup_user(t, query, project and project[:20])},
                   "jira.read", {"op": "user_lookup", "project": project}, relations=("admin",))

    @app.get("/v1/integrations/jira/projects/{project}/issue-types/{type_id}/date-fields")
    def date_fields(project: str, type_id: str, authorization: str | None = Header(default=None)):
        return run(authorization, lambda j, t, pr: j.date_fields(t, project[:20], type_id[:20]))

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
