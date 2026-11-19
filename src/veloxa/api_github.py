"""Admin > Integrations > GitHub (read-only GitHub App). Administration only; every step audited (structure, never
content or tokens). Synced objects go through `connectors.submit` into the data room as a pending submission."""

from __future__ import annotations

import json

from fastapi import Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import audit, connectors, github_app
from .db import connect, tenant_tx


class AllowIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repository_ids: list[int] = Field(max_length=50)


class SyncIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    department_id: str = Field(default="engineering", min_length=1, max_length=64)
    source_type: str = Field(default="engineering_github", min_length=1, max_length=64)
    mode: str = Field(default="incremental", pattern="^(incremental|initial)$")
    dry_run: bool = True


def register(app, *, state, github_factory=None) -> None:
    def gh() -> github_app.GitHub | None:
        if "github" not in state:
            c = github_app.Config.from_env()
            if c is None:
                return None
            state["github"] = (github_factory or github_app.GitHub)(c)
        return state["github"]

    def run(authorization, fn, kind=None):
        with connect(state["dsn"]) as conn:
            p = state["require_relation"](conn, authorization, ("admin",))
            if isinstance(p, JSONResponse):
                return p
            g = gh()
            if g is None:
                return JSONResponse(status_code=409, content={
                    "error": "not_configured", "detail": "The GitHub App is not configured on the server.",
                    "env": {k: bool(__import__("os").environ.get(k)) for k in github_app.REQUIRED}})
            try:
                out = fn(g, str(p.tenant_id), p.principal_id, conn, p)
            except github_app.GitHubError as e:
                out, outcome = JSONResponse(status_code=e.status, content={"error": e.code, "detail": str(e)}), e.code
            else:
                outcome = "ok"
            if kind:
                d = out.pop("_audit", {}) if isinstance(out, dict) else {}
                with tenant_tx(conn, p.tenant_id, p.principal_id):
                    audit.append(conn, p.tenant_id, kind, {**d, "outcome": outcome})
            return out

    @app.get("/v1/integrations/github/status")
    def status(authorization: str | None = Header(default=None)):
        def fn(g, t, pr, conn, p):
            with tenant_tx(conn, p.tenant_id, pr):
                last = conn.execute("SELECT mode, status, fetched, changed, cursor_after, error, finished_at FROM"
                                    " runtime.connector_run WHERE tenant_id = %s AND connector = 'github'"
                                    " ORDER BY started_at DESC LIMIT 1", (p.tenant_id,)).fetchone()
            return {"configured": True, **g.app_info(), "allowlist": g.allowlist(t),
                    "requested_permissions": github_app.READ_PERMISSIONS,
                    "last_run": {k: (str(v) if v is not None else None) for k, v in last.items()} if last else None}
        return run(authorization, fn)

    @app.get("/v1/integrations/github/repositories")
    def repositories(authorization: str | None = Header(default=None)):
        return run(authorization, lambda g, t, pr, c, p: {"repositories": g.repositories(),
                                                           "allowlist": g.allowlist(t)})

    @app.put("/v1/integrations/github/allowlist")
    def allow(body: AllowIn, authorization: str | None = Header(default=None)):
        def fn(g, t, pr, c, p):
            repos = g.set_allowlist(t, body.repository_ids)
            return {"allowlist": [r["id"] for r in repos], "repositories": repos,
                    "_audit": {"repositories": [r["full_name"] for r in repos]}}
        return run(authorization, fn, "github.allowlist_set")

    @app.post("/v1/integrations/github/sync")
    def sync(body: SyncIn, authorization: str | None = Header(default=None)):
        def fn(g, t, pr, conn, p):
            with tenant_tx(conn, p.tenant_id, pr):
                last = conn.execute("SELECT cursor_after FROM runtime.connector_run WHERE tenant_id = %s AND"
                                    " connector = 'github' AND status = 'ok' ORDER BY started_at DESC LIMIT 1",
                                    (p.tenant_id,)).fetchone()
            since = None if body.mode == "initial" or not last else last["cursor_after"]
            recs, info = g.fetch(t, since)
            counts: dict[str, int] = {}
            for r in recs:
                counts[r["kind"]] = counts.get(r["kind"], 0) + 1
            if body.dry_run:
                return {"dry_run": True, **info, "by_kind": counts, "_audit": {"dry_run": True, "fetched": len(recs)}}
            out = connectors.submit(conn, state["store"](), p.tenant_id, "github", recs, actor=pr,
                                    department_id=body.department_id, source_type=body.source_type, mode=body.mode,
                                    scope={"repositories": info["repositories"], "allowlist": g.allowlist(t)},
                                    cursor_before=since, cursor_after=info["cursor"], key="source_id",
                                    version="version", title="GitHub sync")
            out["submission_id"] = str(out["submission_id"]) if out["submission_id"] else None
            return out | {"by_kind": counts, "pages": info["pages"],
                          "_audit": {"fetched": out["fetched"], "changed": out["changed"]}}
        return run(authorization, fn, "github.synced")

    @app.post("/v1/integrations/github/webhook")
    async def webhook(request: Request):
        """Signal only: verified + replay-protected, then acknowledged. Not registered with GitHub by Veloxa."""
        g = gh()
        body = await request.body()
        if g is None or not g.verify_webhook(body, request.headers.get("x-hub-signature-256"),
                                             request.headers.get("x-github-delivery")):
            return JSONResponse(status_code=401, content={"error": "bad_signature_or_replay"})
        evt = request.headers.get("x-github-event", "")
        repo = (json.loads(body or b"{}").get("repository") or {}).get("id")
        return JSONResponse(status_code=202, content={"accepted": True, "event": evt, "repository_id": repo,
                                                      "note": "signal only; the next sync fetches the object"})
