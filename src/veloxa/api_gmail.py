"""Admin > Integrations > Gmail (read-only). Connecting, choosing the scope, syncing and disconnecting require
workspace ADMINISTRATION; every step is an audit event (structure only: never message content or tokens).
Synced messages go through `connectors.submit` into the data room as a pending submission: the same review and
admission as a manual upload. See `gmail_oauth` for the security model."""

from __future__ import annotations

import uuid

from fastapi import Header
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from . import audit, connectors, gmail_oauth
from .db import connect, tenant_tx


class ScopeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    labels: list[str] = Field(default_factory=list, max_length=20)
    senders: list[str] = Field(default_factory=list, max_length=50)
    after: str | None = Field(default=None, max_length=10)
    limit: int = Field(default=25, ge=1, le=gmail_oauth.MAX_IMPORT)


class SyncIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    department_id: str = Field(min_length=1, max_length=64)
    source_type: str = Field(min_length=1, max_length=64)
    dry_run: bool = True


def register(app, *, state, gmail_factory=None) -> None:
    def gmail() -> gmail_oauth.Gmail | None:
        if "gmail" not in state:
            c = gmail_oauth.Client.from_env()
            if c is None:
                return None
            state["gmail"] = (gmail_factory or gmail_oauth.Gmail)(c)
        return state["gmail"]

    def record(conn, tenant_id, principal_id, kind, detail):
        with tenant_tx(conn, tenant_id, principal_id):
            audit.append(conn, tenant_id, kind, detail)

    def run(authorization, fn, kind=None):
        with connect(state["dsn"]) as conn:
            p = state["require_relation"](conn, authorization, ("admin",))
            if isinstance(p, JSONResponse):
                return p
            g = gmail()
            if g is None:
                return JSONResponse(status_code=409, content={
                    "error": "not_configured", "detail": "Gmail OAuth is not configured on the server.",
                    "callback_path": gmail_oauth.CALLBACK_PATH})
            try:
                out = fn(g, str(p.tenant_id), p.principal_id, conn)
            except gmail_oauth.GmailError as e:
                if kind:
                    record(conn, p.tenant_id, p.principal_id, kind, {"outcome": e.code})
                return JSONResponse(status_code=e.status, content={"error": e.code, "detail": str(e)})
            if kind:
                d = out.pop("_audit", {}) if isinstance(out, dict) else {}
                record(conn, p.tenant_id, p.principal_id, kind, {**d, "outcome": "ok"})
            return out

    @app.get("/v1/integrations/gmail/status")
    def status(authorization: str | None = Header(default=None)):
        with connect(state["dsn"]) as conn:
            p = state["require_relation"](conn, authorization, ("admin",))
            if isinstance(p, JSONResponse):
                return p
        g = gmail()
        if g is None:
            return {"client_configured": False, "connected": False, "callback_path": gmail_oauth.CALLBACK_PATH,
                    "requested_scopes": [gmail_oauth.SCOPE]}
        return g.status(str(p.tenant_id))

    @app.post("/v1/integrations/gmail/connect")
    def connect_start(authorization: str | None = Header(default=None)):
        return run(authorization, lambda g, t, pr, c: {"authorize_url": g.authorize_url(t, pr)},
                   "gmail.connect_started")

    @app.get("/v1/integrations/gmail/callback")
    def callback(code: str | None = None, error: str | None = None, state: str | None = None):  # noqa: A002
        g = gmail()
        st = state
        if g is None:
            return RedirectResponse("/#/admin?gmail=not_configured", status_code=303)
        if error:
            g.states.take(st)
            return RedirectResponse("/#/admin?gmail=denied", status_code=303)
        try:
            tenant, principal = g.callback(code, st)
        except gmail_oauth.GmailError as e:
            return RedirectResponse(f"/#/admin?gmail={e.code}", status_code=303)
        with connect(_dsn()) as conn:
            record(conn, uuid.UUID(tenant), principal, "gmail.connected", {"scopes": [gmail_oauth.SCOPE]})
        return RedirectResponse("/#/admin?gmail=connected", status_code=303)

    def _dsn():
        return state_ref["dsn"]

    state_ref = state

    @app.get("/v1/integrations/gmail/labels")
    def labels(authorization: str | None = Header(default=None)):
        return run(authorization, lambda g, t, pr, c: {"labels": g.labels(t)})

    @app.put("/v1/integrations/gmail/scope")
    def set_scope(body: ScopeIn, authorization: str | None = Header(default=None)):
        def fn(g, t, pr, c):
            out = g.set_scope(t, body.model_dump())
            s = out["scope"]
            return out | {"_audit": {"labels": len(s["labels"]), "senders": len(s["senders"]), "after": s["after"],
                                     "limit": s["limit"]}}
        return run(authorization, fn, "gmail.scope_set")

    @app.post("/v1/integrations/gmail/sync")
    def sync(body: SyncIn, authorization: str | None = Header(default=None)):
        def fn(g, t, pr, conn):
            before = (g.vault.get(t).history_id if g.vault.get(t) else None)
            recs, info = g.fetch(t)
            if body.dry_run:
                return {"dry_run": True, **info, "preview": [{k: r[k] for k in ("message_id", "from", "subject",
                                                                                 "date")} for r in recs[:10]],
                        "_audit": {"dry_run": True, "kept": info["kept"]}}
            scope = {"mailbox": g.vault.get(t).mailbox, **(g.vault.get(t).scope or {})}
            out = connectors.submit(conn, state["store"](), uuid.UUID(t), "gmail", recs, actor=pr,
                                    department_id=body.department_id, source_type=body.source_type,
                                    mode=info["mode"], scope=scope, cursor_before=before, cursor_after=info["cursor"],
                                    key="message_id", version="history_id", title="Gmail sync")
            out["submission_id"] = str(out["submission_id"]) if out["submission_id"] else None
            return out | info | {"_audit": {"fetched": out["fetched"], "changed": out["changed"]}}
        return run(authorization, fn, "gmail.synced")

    @app.post("/v1/integrations/gmail/disconnect")
    def disconnect(authorization: str | None = Header(default=None)):
        return run(authorization, lambda g, t, pr, c: g.disconnect(t), "gmail.disconnected")
