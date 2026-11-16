"""HTTP API (ARCHITECTURE.md §19): health, time metadata and Ask Veloxa v0 (POST /v1/ask, facts-only mode).

Least privilege: the API connects ONLY as veloxa_app and refuses to start if privileged database credentials
(administrator, migrator or ingest passwords) are present in its environment. Migrations and admission run in a
separate one-shot process (`veloxa init`).

The client never passes a tenant ID: the hackathon profile resolves the single configured tenant (§19.1).
Metadata endpoints return release times and counts, never record values. /v1/ask returns an `Answer` (§19.4)
whose claims are verified against evidence the configured principal may see at the read basis; a refusal is a
successful response (status "refused"), and typed errors are `not_authorised` (403) and `invalid_time` (422).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import config, ledger, selfcheck, tools
from .ask import pipeline
from .db import connect
from .releases import current_time, load_schedule
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


def _app_dsn() -> str:
    return config.db_settings().dsn("veloxa_app", config.role_password("app"))


def create_app(dsn: str | None = None, clock: Callable[[], datetime] | None = None) -> FastAPI:
    """`clock` is a test-only injection point (a code parameter, not configuration)."""
    assert_unprivileged()
    schedule = load_schedule()
    tenant_id = config.tenant_uuid(schedule.tenant_slug)
    now_fn = clock or current_time
    state: dict = {}

    def _pack_states(conn) -> tuple[datetime, dict, list[dict]]:
        """Availability (owner decision, 16 Nov 2026): serve the latest SEALED batch. A released pack that is
        not sealed is reported as pending; nothing from it is ever exposed (unsealed rows are not counted)."""
        now = now_fn()
        sealed = {b["pack_id"]: b for b in ledger.batches(conn, tenant_id) if b["sealed"]}
        rows = []
        for p in schedule.packs:
            b = sealed.get(p.id)
            status = "sealed" if b else ("pending_admission" if p.release_at <= now else "not_released")
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
        return {
            "status": "ok" if not failures and not pending else "degraded",
            "selfcheck": "pass" if not failures else "fail",
            "pending_admission": pending,
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

    @app.post("/v1/ask")
    def ask(body: AskRequest):
        now = now_fn()
        with connect(state["dsn"]) as conn:
            current = resolve_basis(tenant_id, ledger.sealed_releases(conn, tenant_id), Mode.CURRENT, None,
                                    schedule.time_zone, now)
            principal = (tools.resolve_principal_by_title(conn, current, config.principal_title())
                         if current.batch_seq else None)
            if principal is None:
                return JSONResponse(status_code=403, content={
                    "error": "not_authorised",
                    "detail": "the configured principal does not resolve to exactly one person in the latest "
                              "sealed HR export"})
            try:
                return pipeline.answer(conn, tenant_id, schedule.time_zone, principal, body.question, now,
                                       mode=body.mode, as_of=body.as_of, as_known_at=body.as_known_at)
            except pipeline.InvalidTime as e:
                return JSONResponse(status_code=422, content={"error": "invalid_time", "detail": str(e)})

    return app
