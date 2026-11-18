"""Product API (migration 0020): saved threads, contextual Ask, agent updates and department clarifications."""

from __future__ import annotations

from typing import Literal

from fastapi import Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import product
from .db import connect


class ThreadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="New conversation", min_length=1, max_length=200)
    context: dict = Field(default_factory=dict)


class ThreadAsk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=2000)
    as_known_at: str | None = None


class Rename(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)


class ClarificationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    department_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    question: str = Field(min_length=1, max_length=2000)
    context: dict = Field(default_factory=dict)
    submit: bool = False


class ClarificationEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["submitted", "answered", "withdrawn"]
    note: str | None = Field(default=None, max_length=4000)
    submission_id: str | None = None


class PublishIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project: str | None = Field(default=None, max_length=120)


def _err(e: product.ProductError):
    code = {"not_found": 404, "not_permitted": 403}.get(e.reason, 422)
    return JSONResponse(status_code=code, content={"error": e.reason, "detail": str(e)})


def register(app, *, state, principal_of, now_fn, tz_of) -> None:
    def who(conn, authorization):
        return principal_of(conn, authorization, now_fn())

    @app.post("/v1/threads")
    def create_thread(body: ThreadIn, authorization: str | None = Header(default=None)):
        with connect(state["dsn"]) as conn:
            p = who(conn, authorization)
            if isinstance(p, JSONResponse):
                return p
            return {"thread_id": product.create_thread(conn, p.tenant_id, p.principal_id, body.title, body.context)}

    @app.get("/v1/threads")
    def threads(archived: bool = False, authorization: str | None = Header(default=None)):
        with connect(state["dsn"]) as conn:
            p = who(conn, authorization)
            if isinstance(p, JSONResponse):
                return p
            return {"threads": product.list_threads(conn, p.tenant_id, p.principal_id, archived)}

    @app.get("/v1/threads/{thread_id}")
    def thread(thread_id: str, authorization: str | None = Header(default=None)):
        with connect(state["dsn"]) as conn:
            p = who(conn, authorization)
            if isinstance(p, JSONResponse):
                return p
            t = product.get_thread(conn, p.tenant_id, p.principal_id, thread_id)
        return t if t else JSONResponse(status_code=404, content={"error": "not_found", "detail": "no such thread"})

    @app.post("/v1/threads/{thread_id}/ask")
    def thread_ask(thread_id: str, body: ThreadAsk, authorization: str | None = Header(default=None)):
        with connect(state["dsn"]) as conn:
            p = who(conn, authorization)
            if isinstance(p, JSONResponse):
                return p
            try:
                return product.ask_in_thread(conn, p.tenant_id, p, tz_of(conn, p.tenant_id), thread_id,
                                             body.question, now_fn(), as_known_at=body.as_known_at)
            except product.ProductError as e:
                return _err(e)
            except ValueError as e:
                return JSONResponse(status_code=422, content={"error": "invalid_time", "detail": str(e)})

    @app.post("/v1/threads/{thread_id}/rename")
    def rename(thread_id: str, body: Rename, authorization: str | None = Header(default=None)):
        with connect(state["dsn"]) as conn:
            p = who(conn, authorization)
            if isinstance(p, JSONResponse):
                return p
            try:
                product.thread_event(conn, p.tenant_id, p.principal_id, thread_id, "renamed", body.title)
            except product.ProductError as e:
                return _err(e)
        return {"thread_id": thread_id, "title": body.title}

    @app.post("/v1/threads/{thread_id}/{action}")
    def archive(thread_id: str, action: Literal["archive", "restore"],
                authorization: str | None = Header(default=None)):
        with connect(state["dsn"]) as conn:
            p = who(conn, authorization)
            if isinstance(p, JSONResponse):
                return p
            try:
                product.thread_event(conn, p.tenant_id, p.principal_id, thread_id,
                                     "archived" if action == "archive" else "restored")
            except product.ProductError as e:
                return _err(e)
        return {"thread_id": thread_id, "archived": action == "archive"}

    @app.get("/v1/agent-updates")
    def updates(department: str | None = None, limit: int = 50, authorization: str | None = Header(default=None)):
        with connect(state["dsn"]) as conn:
            p = who(conn, authorization)
            if isinstance(p, JSONResponse):
                return p
            return {"updates": product.list_updates(conn, p.tenant_id, p.principal_id, department,
                                                    max(1, min(limit, 200)))}

    @app.get("/v1/agent-updates/{update_id}")
    def update(update_id: str, authorization: str | None = Header(default=None)):
        with connect(state["dsn"]) as conn:
            p = who(conn, authorization)
            if isinstance(p, JSONResponse):
                return p
            u = product.get_update(conn, p.tenant_id, p.principal_id, update_id)
        return u if u else JSONResponse(status_code=404, content={"error": "not_found", "detail": "no such update"})

    @app.post("/v1/agent-updates/{update_id}/thread")
    def update_thread(update_id: str, authorization: str | None = Header(default=None)):
        """Ask about this update: a contextual thread (the update's agent, project and evidence as context)."""
        with connect(state["dsn"]) as conn:
            p = who(conn, authorization)
            if isinstance(p, JSONResponse):
                return p
            u = product.get_update(conn, p.tenant_id, p.principal_id, update_id)
            if u is None:
                return JSONResponse(status_code=404, content={"error": "not_found", "detail": "no such update"})
            ctx = {"kind": "agent_update", "update_id": update_id, "agent_id": u["agent_id"],
                   "subject": u["project"] or u["title"], "evidence": u["evidence"], "synthetic": u["synthetic"]}
            tid = product.create_thread(conn, p.tenant_id, p.principal_id, f"About: {u['title']}"[:200], ctx)
        return {"thread_id": tid, "context": ctx}

    @app.post("/v1/sessions/{session_id}/publish-update")
    def publish(session_id: str, body: PublishIn, authorization: str | None = Header(default=None)):
        with connect(state["dsn"]) as conn:
            p = who(conn, authorization)
            if isinstance(p, JSONResponse):
                return p
            try:
                return product.publish_update(conn, p.tenant_id, p.principal_id, session_id, body.project)
            except product.ProductError as e:
                return _err(e)

    @app.post("/v1/clarifications")
    def clarify(body: ClarificationIn, authorization: str | None = Header(default=None)):
        with connect(state["dsn"]) as conn:
            p = who(conn, authorization)
            if isinstance(p, JSONResponse):
                return p
            try:
                return product.create_clarification(conn, p.tenant_id, p.principal_id, body.department_id,
                                                    body.question, body.context, body.submit)
            except product.ProductError as e:
                return _err(e)

    @app.get("/v1/clarifications")
    def clarifications(authorization: str | None = Header(default=None)):
        with connect(state["dsn"]) as conn:
            p = who(conn, authorization)
            if isinstance(p, JSONResponse):
                return p
            return {"requests": product.list_clarifications(conn, p.tenant_id, p.principal_id)}

    @app.post("/v1/clarifications/{request_id}/events")
    def clarification_event(request_id: str, body: ClarificationEvent,
                            authorization: str | None = Header(default=None)):
        with connect(state["dsn"]) as conn:
            p = who(conn, authorization)
            if isinstance(p, JSONResponse):
                return p
            try:
                return {"state": product.clarification_event(conn, p.tenant_id, p.principal_id, request_id,
                                                             body.state, body.note, body.submission_id)}
            except product.ProductError as e:
                return _err(e)
