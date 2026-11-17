"""HTTP API (ARCHITECTURE.md §19): health, time metadata, Ask Veloxa v0 (POST /v1/ask, facts-only mode) and the
interim briefing (GET /v1/briefing, an unranked watchlist while D-15 is pending).

Least privilege: the API connects ONLY as veloxa_app and refuses to start if privileged database credentials
(administrator, migrator or ingest passwords) are present in its environment. Migrations and admission run in a
separate one-shot process (`veloxa init`).

The client never passes a tenant ID: the hackathon profile resolves the single configured tenant (§19.1).
Metadata endpoints return release times and counts, never record values. /v1/ask returns an `Answer` (§19.4)
whose claims are verified against evidence the configured principal may see at the read basis; a refusal is a
successful response (status "refused"), and typed errors are `not_authenticated` (401), `not_authorised` (403)
and `invalid_time` (422).

Requester-facing endpoints need an authenticated principal (auth.py). No identity provider is integrated yet, so
by default they FAIL CLOSED with 401; only the explicit local development fixture authenticates anyone.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import auth, config, ledger, selfcheck, tools
from .ask import pipeline, timeparse
from .authz.current import RelationshipStore
from .db import connect
from .releases import current_time, load_authorisations, load_schedule
from .resolution import store as resolution
from .risk import briefing
from .temporal import Mode, resolve_basis

PRIVILEGED_ENV = ("VELOXA_DB_ADMIN_PASSWORD", "VELOXA_DB_ADMIN_USER", "VELOXA_MIGRATOR_PASSWORD",
                  "VELOXA_INGEST_PASSWORD", "VELOXA_PROJECTION_PASSWORD")


def assert_unprivileged(env: Mapping[str, str] | None = None) -> None:
    env = os.environ if env is None else env
    present = [name for name in PRIVILEGED_ENV if env.get(name)]
    if present:
        raise RuntimeError(
            "the API process must not receive privileged database credentials; remove: " + ", ".join(present)
        )


class AskRequest(BaseModel):
    """The client never passes a tenant or a principal (§19.1); unknown fields are rejected."""
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=1000)
    mode: str | None = Field(default=None, description="CURRENT | KNOWN_AT | RECORDED_BY | VALID_AT")
    as_of: str | None = Field(default=None, description="the R or V date for RECORDED_BY or VALID_AT")
    as_known_at: str | None = Field(default=None, description="date or instant; implies KNOWN_AT")


class DecisionRequest(BaseModel):
    """A C12 human decision. The decider is the authenticated principal, never a request field."""
    model_config = ConfigDict(extra="forbid")
    candidate_id: uuid.UUID
    outcome: str = Field(pattern="^(approved|rejected|deferred)$")
    rationale: str = Field(min_length=1, max_length=2000)
    decision_record: str = Field(min_length=1, max_length=200)
    evidence_refs: list[str] = Field(default_factory=list, max_length=50)
    request_key: str | None = Field(default=None, min_length=8, max_length=200,
                                    description="a replay with the same key returns the original decision")


class ResolutionRequest(BaseModel):
    """Asks for a human decision on a candidate; the requester is the authenticated principal."""
    model_config = ConfigDict(extra="forbid")
    candidate_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=2000)


def _app_dsn() -> str:
    return config.db_settings().dsn("veloxa_app", config.role_password("app"))


def create_app(dsn: str | None = None, clock: Callable[[], datetime] | None = None) -> FastAPI:
    """`clock` is a test-only injection point (a code parameter, not configuration)."""
    assert_unprivileged()
    auth_settings = auth.settings_from_env()  # raises if the development fixture is half-configured
    schedule = load_schedule()
    tenant_id = config.tenant_uuid(schedule.tenant_slug)
    now_fn = clock or current_time
    state: dict = {}

    def _pack_states(conn) -> tuple[datetime, dict, list[dict]]:
        """Availability (owner decision, 16 Nov 2026): serve the latest SEALED batch. A released pack that is
        not sealed is reported as pending; nothing from it is ever exposed (unsealed rows are not counted)."""
        now = now_fn()
        sealed = {b["pack_id"]: b for b in ledger.batches(conn, tenant_id) if b["sealed"]}
        authorised = load_authorisations()  # released but unauthorised packs are never admitted automatically
        rows = []
        for p in schedule.packs:
            b = sealed.get(p.id)
            status = ("sealed" if b else "not_released" if p.release_at > now
                      else "pending_admission" if p.id in authorised else "awaiting_authorisation")
            rows.append({
                "pack": p.id,
                "batch_seq": p.batch_seq,
                "release_at": p.release_at.isoformat(),
                "released": p.release_at <= now,
                "admitted": b is not None,
                "status": status,
                "sealed_at": b["sealed_at"].astimezone(UTC).isoformat() if b else None,
                "file_count": b["file_count"] if b else None,
                "record_count": b["record_count"] if b else None,
            })
        return now, sealed, rows

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        state["dsn"] = dsn or _app_dsn()
        with connect(state["dsn"]) as conn:
            failures = selfcheck.run(conn)
        if failures:
            raise RuntimeError("startup self-check failed: " + "; ".join(failures))
        yield

    app = FastAPI(title="Veloxa CXO Cockpit", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict:
        with connect(state["dsn"]) as conn:
            failures = selfcheck.run(conn)
            _, _, rows = _pack_states(conn)
        pending = [r["pack"] for r in rows if r["status"] == "pending_admission"]
        awaiting = [r["pack"] for r in rows if r["status"] == "awaiting_authorisation"]
        return {
            "status": "ok" if not failures and not pending and not awaiting else "degraded",
            "selfcheck": "pass" if not failures else "fail",
            "pending_admission": pending,
            "awaiting_authorisation": awaiting,
            "auth": auth_settings.mode,
        }

    @app.get("/v1/meta/clock")
    def clock_endpoint() -> dict:
        with connect(state["dsn"]) as conn:
            k = ledger.sealed_watermark(conn, tenant_id)
        return {"now": now_fn().isoformat(), "time_zone": schedule.time_zone, "sealed_watermark": k}

    @app.get("/v1/meta/releases")
    def releases() -> dict:
        with connect(state["dsn"]) as conn:
            now, _, rows = _pack_states(conn)
        return {"now": now.isoformat(), "packs": rows}

    def _principal(conn, authorization: str | None, now: datetime):
        """The authenticated principal, or an error response. Never derived from request content."""
        emp_id = auth_settings.authenticate(authorization)
        if emp_id is None:
            detail = ("an authenticated principal is required; no identity provider is configured, so "
                      "requester-facing endpoints are closed" if auth_settings.mode == "none"
                      else "an authenticated principal is required")
            return JSONResponse(status_code=401, headers={"WWW-Authenticate": "Bearer"},
                                content={"error": "not_authenticated", "detail": detail})
        current = resolve_basis(tenant_id, ledger.sealed_releases(conn, tenant_id), Mode.CURRENT, None,
                                schedule.time_zone, now)
        principal = tools.resolve_principal_by_emp_id(conn, current, emp_id) if current.batch_seq else None
        if principal is None:
            return JSONResponse(status_code=403, content={
                "error": "not_authorised",
                "detail": "the authenticated principal does not resolve to exactly one person in the latest sealed "
                          "HR export"})
        return principal

    @app.post("/v1/ask")
    def ask(body: AskRequest, authorization: str | None = Header(default=None)):
        now = now_fn()
        with connect(state["dsn"]) as conn:
            principal = _principal(conn, authorization, now)
            if isinstance(principal, JSONResponse):
                return principal
            try:
                return pipeline.answer(conn, tenant_id, schedule.time_zone, principal, body.question, now,
                                       mode=body.mode, as_of=body.as_of, as_known_at=body.as_known_at)
            except pipeline.InvalidTime as e:
                return JSONResponse(status_code=422, content={"error": "invalid_time", "detail": str(e)})

    @app.get("/v1/briefing")
    def get_briefing(mode: str | None = None, as_of: str | None = None, as_known_at: str | None = None,
                     authorization: str | None = Header(default=None)):
        """Interim unranked watchlist (top_three is null while D-15 is pending). No weights, tiers or ranking
        options are accepted from the request."""
        now = now_fn()
        with connect(state["dsn"]) as conn:
            principal = _principal(conn, authorization, now)
            if isinstance(principal, JSONResponse):
                return principal
            try:
                spec = timeparse.parse("", now, schedule.time_zone, mode, as_of, as_known_at)
                basis = resolve_basis(tenant_id, ledger.sealed_releases(conn, tenant_id), spec.mode, spec.as_of,
                                      schedule.time_zone, now)
            except ValueError as e:
                return JSONResponse(status_code=422, content={"error": "invalid_time", "detail": str(e)})
            return briefing.serve(conn, tenant_id, principal, basis, now)

    @app.get("/v1/today")
    def get_today(authorization: str | None = Header(default=None)):
        """What the cockpit shows when it opens (voice note T6a): at most three evidence-backed attention items,
        without a question. Not ranked while D-15 is pending. Recorded in the same immutable briefing snapshot."""
        now = now_fn()
        with connect(state["dsn"]) as conn:
            principal = _principal(conn, authorization, now)
            if isinstance(principal, JSONResponse):
                return principal
            basis = resolve_basis(tenant_id, ledger.sealed_releases(conn, tenant_id), Mode.CURRENT, None,
                                  schedule.time_zone, now)
            b = briefing.serve(conn, tenant_id, principal, basis, now)
        return {"today": b["today"], "top_three": b["top_three"], "ranking_mode": b["ranking_mode"],
                "owner_decision_status": b["owner_decision_status"], "read_basis": b["read_basis"],
                "snapshot_id": b["snapshot_id"], "full_briefing": f"/v1/briefing/{b['snapshot_id']}"}

    def _resolver(conn, authorization: str | None):
        """C12 decisions need the ReBAC relation `resolve` on identity_queue:default. No tuple grants it by
        default, so the queue is closed until the owner records one."""
        principal = _principal(conn, authorization, now_fn())
        if isinstance(principal, JSONResponse):
            return principal
        try:
            granted, _ = RelationshipStore(conn).check(tenant_id, principal.principal_id, "resolve",
                                                       "identity_queue", "default")
        except Exception:  # noqa: BLE001 - an unavailable relationship store denies
            granted = False
        if not granted:
            return JSONResponse(status_code=403, content={
                "error": "not_authorised", "detail": "resolving identities needs the `resolve` relation on the queue"})
        return principal

    @app.get("/v1/identity/queue")
    def identity_queue(authorization: str | None = Header(default=None)):
        """Candidates awaiting a human decision, with their rule classification (never a score)."""
        with connect(state["dsn"]) as conn:
            principal = _resolver(conn, authorization)
            if isinstance(principal, JSONResponse):
                return principal
            rows = resolution.queue(conn, tenant_id)
        return {"candidates": [{k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in r.items()}
                               for r in rows]}

    @app.post("/v1/identity/requests")
    def identity_request(body: ResolutionRequest, authorization: str | None = Header(default=None)):
        """Record a request for a human decision (no state changes)."""
        with connect(state["dsn"]) as conn:
            principal = _resolver(conn, authorization)
            if isinstance(principal, JSONResponse):
                return principal
            try:
                rid = resolution.request_decision(conn, tenant_id, principal.principal_id, body.candidate_id,
                                                  body.reason)
            except resolution.Invalid as e:
                return JSONResponse(status_code=422, content={"error": "invalid_request", "detail": str(e)})
        return {"request_id": str(rid), "candidate_id": str(body.candidate_id)}

    @app.post("/v1/identity/decisions")
    def identity_decision(body: DecisionRequest, authorization: str | None = Header(default=None)):
        """Append a versioned decision. Approval of anything but a synthetic test identity is refused (403)."""
        with connect(state["dsn"]) as conn:
            principal = _resolver(conn, authorization)
            if isinstance(principal, JSONResponse):
                return principal
            try:
                return resolution.decide(conn, tenant_id, principal.principal_id, body.candidate_id, body.outcome,
                                         body.rationale, body.decision_record, body.evidence_refs, body.request_key)
            except resolution.NotPermitted as e:
                return JSONResponse(status_code=403, content={"error": "decision_not_permitted", "detail": str(e)})
            except resolution.Invalid as e:
                return JSONResponse(status_code=422, content={"error": "invalid_decision", "detail": str(e)})

    @app.get("/v1/briefing/{snapshot_id}")
    def get_briefing_snapshot(snapshot_id: uuid.UUID, authorization: str | None = Header(default=None)):
        """A recorded briefing exactly as served, with the D-15 policy version in force at the time."""
        with connect(state["dsn"]) as conn:
            principal = _principal(conn, authorization, now_fn())
            if isinstance(principal, JSONResponse):
                return principal
            found = briefing.fetch(conn, tenant_id, principal, str(snapshot_id))
        if found is None:
            return JSONResponse(status_code=404, content={"error": "not_found"})
        return found

    return app
