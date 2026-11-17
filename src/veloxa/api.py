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

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from . import auth, config, ledger, selfcheck, tools
from .ask import pipeline, timeparse
from .authz.current import RelationshipStore
from .db import connect
from .llm import config as llm_config
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


class SessionRequest(BaseModel):
    """Open an interview session. The interviewer is the authenticated principal, never a request field."""
    model_config = ConfigDict(extra="forbid")
    as_known_at: str | None = Field(default=None, description="date or instant; default: current knowledge")


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=2000)
    idempotency_key: str = Field(min_length=8, max_length=200)


_AGENT_ERRORS = {"not_found": 404, "agent_not_activated": 403, "not_authorised": 403, "invalid_time": 422,
                 "invalid_request": 422, "idempotency_conflict": 409}


class ShiftRequest(BaseModel):
    """A deterministic commitment-shift scenario. The shift is an assumption, never evidence."""
    model_config = ConfigDict(extra="forbid")
    business_object: str = Field(min_length=1, max_length=120)
    shift_days: int = Field(ge=-730, le=730)
    as_known_at: str | None = None


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


def create_app(dsn: str | None = None, clock: Callable[[], datetime] | None = None,
               agent_runtime: Callable | None = None) -> FastAPI:
    """`clock` and `agent_runtime` (a factory taking a connection) are test-only injection points (code
    parameters, not configuration). By default agents run on the in-process agent-tool PDP, the C32 knowledge
    boundary and the facts-only composer: no model provider is configured."""
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
    from . import gateway

    gateway.install(app)        # C01 controls; installed first so the observability middleware wraps them

    @app.middleware("http")
    async def observe(request: Request, call_next):
        """C29: correlation ID, request metrics, one structured log line and error tracking per request. Only the
        route TEMPLATE is recorded (never paths with IDs, query strings, headers or bodies)."""
        import time as _time

        from . import telemetry

        rid = telemetry.request_id_from(request.headers.get("x-request-id"))
        token = telemetry.REQUEST_ID.set(rid)
        started = _time.perf_counter()
        route = "unmatched"
        try:
            response = await call_next(request)
        except Exception:
            route = getattr(request.scope.get("route"), "path", route)
            telemetry.METRICS.inc("veloxa_errors_total", route=route)
            telemetry.log.error("unhandled error", exc_info=True, extra={"fields": {"route": route}})
            response = JSONResponse(status_code=500, content={"error": "internal_error", "request_id": rid})
        route = getattr(request.scope.get("route"), "path", route)
        elapsed = _time.perf_counter() - started
        telemetry.METRICS.inc("veloxa_http_requests_total", route=route, method=request.method,
                              http_status=response.status_code)
        telemetry.METRICS.observe("veloxa_http_request_seconds", elapsed, route=route, method=request.method)
        telemetry.log.info("request", extra={"fields": {"route": route, "method": request.method,
                                                         "http_status": response.status_code}})
        response.headers["X-Request-ID"] = rid
        telemetry.REQUEST_ID.reset(token)
        return response

    if os.environ.get("VELOXA_METRICS") == "1":
        @app.get("/metrics", include_in_schema=False)
        def metrics_endpoint():
            """Internal Prometheus scrape endpoint (enabled only with VELOXA_METRICS=1): counts and latencies."""
            from . import telemetry

            return PlainTextResponse(telemetry.METRICS.render())

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
            "model": llm_config.status(),
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

    def _scenario_basis(conn, as_known_at):
        now = now_fn()
        spec = timeparse.parse("", now, schedule.time_zone, None, None, as_known_at)
        return resolve_basis(tenant_id, ledger.sealed_releases(conn, tenant_id), spec.mode, spec.as_of,
                             schedule.time_zone, now)

    @app.get("/v1/scenarios/commitments")
    def scenario_commitments(as_known_at: str | None = None, authorization: str | None = Header(default=None)):
        """Named commitments the admitted evidence states at the basis (scenario subjects)."""
        from .risk import scenarios

        with connect(state["dsn"]) as conn:
            principal = _principal(conn, authorization, now_fn())
            if isinstance(principal, JSONResponse):
                return principal
            try:
                basis = _scenario_basis(conn, as_known_at)
            except ValueError as e:
                return JSONResponse(status_code=422, content={"error": "invalid_time", "detail": str(e)})
            names = scenarios.commitments(conn, basis, schedule.time_zone) if basis.batch_seq else []
        return {"read_basis": {"batch_seq": basis.batch_seq, "banner": basis.banner()}, "commitments": names}

    @app.post("/v1/scenarios/commitment-shift")
    def scenario_shift(body: ShiftRequest, authorization: str | None = Header(default=None)):
        """Observed facts, derived implications, the assumption and the unknowns of moving one commitment.
        Deterministic; never a probability or a ranking."""
        from .risk import scenarios

        with connect(state["dsn"]) as conn:
            principal = _principal(conn, authorization, now_fn())
            if isinstance(principal, JSONResponse):
                return principal
            try:
                basis = _scenario_basis(conn, body.as_known_at)
                return scenarios.commitment_shift(conn, basis, schedule.time_zone, body.business_object,
                                                  body.shift_days)
            except scenarios.ScenarioError as e:
                return JSONResponse(status_code=422, content={"error": "invalid_scenario", "detail": str(e)})
            except ValueError as e:
                return JSONResponse(status_code=422, content={"error": "invalid_time", "detail": str(e)})

    @app.get("/v1/risks/history")
    def risk_history(authorization: str | None = Header(default=None)):
        """How each risk and adverse evidence item appeared, changed, resolved or reopened across sealed knowledge
        points. Derived from the evidence at each K (nothing stored); never ranked while D-15 is pending."""
        from .risk import lifecycle

        with connect(state["dsn"]) as conn:
            principal = _principal(conn, authorization, now_fn())
            if isinstance(principal, JSONResponse):
                return principal
            return lifecycle.compute(conn, tenant_id, principal)

    @app.get("/v1/conflicts")
    def get_conflicts(mode: str | None = None, as_of: str | None = None, as_known_at: str | None = None,
                      authorization: str | None = Header(default=None)):
        """The conflict register at a read basis: every source disagreement with provenance and time for each
        side. Derived, never stored; an earlier basis reproduces the earlier register. No value is chosen."""
        from .ask import conflicts

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
            if basis.batch_seq == 0:
                return {"register_version": conflicts.REGISTER_VERSION, "read_basis": pipeline._basis_json(basis),
                        "conflicts": [], "unknowns": ["nothing had been admitted at this knowledge point"]}
            try:
                found = conflicts.register(conn, basis, schedule.time_zone)
            except ValueError as e:      # a time axis the sources do not record: unanswerable, never an error
                return {"register_version": conflicts.REGISTER_VERSION, "read_basis": pipeline._basis_json(basis),
                        "conflicts": [], "unknowns": [str(e)]}
        return {"register_version": conflicts.REGISTER_VERSION, "read_basis": pipeline._basis_json(basis),
                "conflicts": found, "unknowns": []}

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

    def _runtime(conn):
        from .agents.runtime import AgentRuntime
        from .llm.config import agent_composer

        return agent_runtime(conn) if agent_runtime else AgentRuntime(conn, schedule.time_zone,
                                                                      llm=agent_composer(conn))

    def _agent_error(e):
        return JSONResponse(status_code=_AGENT_ERRORS.get(e.code, 400), content={"error": e.code, "detail": e.detail})

    @app.get("/v1/agents")
    def list_agents(authorization: str | None = Header(default=None)):
        """The agent registry: every agent's category, activation state and configuration version. Unassigned
        slots represent nobody; no person, persona or manager is shown or invented."""
        from .agents import registry

        with connect(state["dsn"]) as conn:
            principal = _principal(conn, authorization, now_fn())
            if isinstance(principal, JSONResponse):
                return principal
            agents = registry.listing(conn, tenant_id)
        return {"runtime_version": registry.RUNTIME_VERSION,
                "agents": [{"agent_id": a.agent_id, "category": a.category, "activation": a.activation,
                            "config_version": a.config_version, "assigned": a.represented_person is not None,
                            "tools": list(a.tools)} for a in agents]}

    @app.post("/v1/agents/{agent_id}/sessions")
    def open_agent_session(agent_id: str, body: SessionRequest, authorization: str | None = Header(default=None)):
        from .agents.runtime import AgentError

        now = now_fn()
        with connect(state["dsn"]) as conn:
            principal = _principal(conn, authorization, now)
            if isinstance(principal, JSONResponse):
                return principal
            try:
                spec = timeparse.parse("", now, schedule.time_zone, None, None, body.as_known_at)
                basis = resolve_basis(tenant_id, ledger.sealed_releases(conn, tenant_id), spec.mode, spec.as_of,
                                      schedule.time_zone, now)
            except ValueError as e:
                return JSONResponse(status_code=422, content={"error": "invalid_time", "detail": str(e)})
            try:
                return _runtime(conn).open_session(tenant_id, principal, agent_id, basis)
            except AgentError as e:
                return _agent_error(e)

    @app.post("/v1/sessions/{session_id}/messages")
    def agent_message(session_id: uuid.UUID, body: MessageRequest, authorization: str | None = Header(default=None)):
        """One agent turn: cited claims from KB ∩ AZ ∩ POL only, labelled as an AI representation; idempotent per
        (session, idempotency_key)."""
        from .agents.runtime import AgentError, AgentRequest

        with connect(state["dsn"]) as conn:
            principal = _principal(conn, authorization, now_fn())
            if isinstance(principal, JSONResponse):
                return principal
            try:
                return _runtime(conn).execute(AgentRequest(tenant_id, principal, session_id, body.message,
                                                           body.idempotency_key))
            except AgentError as e:
                return _agent_error(e)

    @app.post("/v1/sessions/{session_id}/close")
    def close_agent_session(session_id: uuid.UUID, authorization: str | None = Header(default=None)):
        from .agents.runtime import AgentError

        with connect(state["dsn"]) as conn:
            principal = _principal(conn, authorization, now_fn())
            if isinstance(principal, JSONResponse):
                return principal
            try:
                closed = _runtime(conn).close_session(tenant_id, principal, session_id)
            except AgentError as e:
                return _agent_error(e)
        return {"session_id": str(session_id), "working_memory_closed": closed}

    @app.get("/v1/timeline")
    def get_timeline(start: str | None = None, end: str | None = None, as_known_at: str | None = None,
                     authorization: str | None = Header(default=None)):
        """Dated events from admitted records (joins, recorded exits, documents issued, e-mails the requester was
        party to), through the declared timeline tool at the read basis. Never inferred events."""
        from datetime import date as _date

        now = now_fn()
        with connect(state["dsn"]) as conn:
            principal = _principal(conn, authorization, now)
            if isinstance(principal, JSONResponse):
                return principal
            try:
                s = _date.fromisoformat(start) if start else None
                e = _date.fromisoformat(end) if end else None
                spec = timeparse.parse("", now, schedule.time_zone, None, None, as_known_at)
                basis = resolve_basis(tenant_id, ledger.sealed_releases(conn, tenant_id), spec.mode, spec.as_of,
                                      schedule.time_zone, now)
            except ValueError as ex:
                return JSONResponse(status_code=422, content={"error": "invalid_time", "detail": str(ex)})
            r = tools.timeline(conn, basis, principal, s, e) if basis.batch_seq else tools.ToolResult(
                "timeline", "no_data", gaps=["nothing had been admitted at this knowledge point"])
        return {"read_basis": {"batch_seq": basis.batch_seq, "banner": basis.banner()}, "status": r.status,
                "events": [{"display": f.display, "kind": f.kind, "evidence": list(f.inputs)} for f in r.facts],
                "evidence": [{"record_id": ev.record_id, "kind": ev.kind, "system": ev.system,
                              "data_class": ev.data_class, "batch_seq": ev.batch_seq, "excerpt": ev.excerpt}
                             for ev in r.evidence], "gaps": list(r.gaps)}

    @app.get("/v1/agents/{agent_id}/sessions")
    def agent_sessions(agent_id: str, limit: int = 20, offset: int = 0,
                       authorization: str | None = Header(default=None)):
        """The caller's OWN sessions with this agent (RLS), newest first, paginated."""
        from .db import tenant_tx

        limit, offset = max(1, min(limit, 100)), max(0, offset)
        with connect(state["dsn"]) as conn:
            principal = _principal(conn, authorization, now_fn())
            if isinstance(principal, JSONResponse):
                return principal
            with tenant_tx(conn, tenant_id, principal.principal_id):
                rows = conn.execute(
                    "SELECT s.session_id, s.config_version, s.batch_seq, s.opened_at,"
                    " (SELECT count(*) FROM runtime.agent_execution e WHERE e.session_id = s.session_id) AS turns"
                    " FROM runtime.agent_session s WHERE s.agent_id = %s ORDER BY s.opened_at DESC, s.session_id"
                    " LIMIT %s OFFSET %s", (agent_id, limit + 1, offset)).fetchall()
        return {"agent_id": agent_id, "offset": offset, "limit": limit, "has_more": len(rows) > limit,
                "sessions": [{"session_id": str(r["session_id"]), "config_version": r["config_version"],
                              "batch_seq": r["batch_seq"], "opened_at": r["opened_at"].isoformat(),
                              "turns": r["turns"]} for r in rows[:limit]]}

    @app.get("/v1/sessions/{session_id}")
    def agent_session_detail(session_id: uuid.UUID, authorization: str | None = Header(default=None)):
        """One of the caller's own sessions with its stored turns, in order (for history across reloads)."""
        from .db import tenant_tx

        with connect(state["dsn"]) as conn:
            principal = _principal(conn, authorization, now_fn())
            if isinstance(principal, JSONResponse):
                return principal
            with tenant_tx(conn, tenant_id, principal.principal_id):
                s = conn.execute("SELECT session_id, agent_id, config_version, batch_seq, opened_at FROM"
                                 " runtime.agent_session WHERE session_id = %s", (session_id,)).fetchone()
                turns = conn.execute("SELECT response FROM runtime.agent_execution WHERE session_id = %s"
                                     " ORDER BY executed_at, execution_id", (session_id,)).fetchall() if s else []
        if s is None:
            return JSONResponse(status_code=404, content={"error": "not_found"})
        return {"session_id": str(s["session_id"]), "agent_id": s["agent_id"], "config_version": s["config_version"],
                "batch_seq": s["batch_seq"], "opened_at": s["opened_at"].isoformat(),
                "turns": [t["response"] for t in turns]}

    @app.get("/v1/agents/{agent_id}/memories")
    def agent_memories(agent_id: str, authorization: str | None = Header(default=None)):
        """Memory items in the caller's own scope (this agent, this interviewer), at current knowledge. Items whose
        sources the caller may no longer see are withheld and counted."""
        from .agents.authorization import CurrentAuthorization
        from .agents.memory import MemoryScope, MemoryStore

        with connect(state["dsn"]) as conn:
            principal = _principal(conn, authorization, now_fn())
            if isinstance(principal, JSONResponse):
                return principal
            k = ledger.sealed_watermark(conn, tenant_id)
            items, withheld = MemoryStore(conn, CurrentAuthorization(conn)).recall(
                MemoryScope(tenant_id, agent_id, principal.principal_id), k)
        return {"agent_id": agent_id, "batch_seq": k, "withheld": withheld,
                "items": [{"memory_id": str(i["memory_id"]), "tier": i["tier"], "label": i["label"],
                           "content": i["content"], "knowledge_basis": i["knowledge_basis"],
                           "source_records": i["source_records"], "batch_seq": i["batch_seq"],
                           "created_at": i["created_at"].isoformat()} for i in items]}

    return app
