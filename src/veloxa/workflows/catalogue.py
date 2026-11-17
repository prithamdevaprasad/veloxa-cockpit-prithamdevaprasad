"""The durable workflow catalogue (ARCHITECTURE.md §17.3), registered with DBOS under pinned, versioned names.

Persisted state (DBOS inputs, step outputs) holds ONLY opaque ids, counts and statuses. Steps open their own
database connections from configuration (never from workflow input), run as the least-privileged Veloxa role for
their activity, and are idempotent or guarded, so a recovered run never repeats a completed side effect:

veloxa.pack_pipeline.v1(tenant_id, pack_id)
    admit    guarded: an already sealed pack is not re-admitted; admit_pack still enforces the release time and the
             admission authorisation register (release is not authorisation; parsing is not admission)
    project  projector.build: idempotent per batch
    identity resolution.integrate.sync_batch: idempotent per batch; decides nothing
    audit    one `workflow.completed` audit event (ids and counts only)
veloxa.agent_turn.v1(tenant_id, request_id, interviewer_id)
    turn     reads the request under the interviewer's RLS, re-resolves the interviewer from the latest sealed HR
             export, and runs ONE turn through the shared agent runtime (KB ∩ AZ ∩ POL unchanged). The runtime's
             (session, idempotency key) guard makes a recovered step return the stored turn instead of a second one.
Retries are allowed only for transient database errors; authorization and validation failures are permanent.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import psycopg
from dbos import DBOS
from pydantic import BaseModel, ConfigDict, Field

from .. import audit, config, ledger
from ..db import connect, tenant_tx
from .runner import Definition

# Test-only injection point for the agent runtime factory (a code parameter, as in api.create_app); None in
# production, where the runtime uses the C32 knowledge boundary and the facts-only composer.
AGENT_RUNTIME_FACTORY: Any = None


def _dsn(role: str) -> str:
    return config.db_settings().dsn(f"veloxa_{role}", config.role_password(role))


def _transient(e: BaseException) -> bool:
    return isinstance(e, psycopg.OperationalError)


class PermanentFailure(Exception):
    """A failure no retry can fix (authorization, validation, refused admission)."""


# ------------------------------------------------------------------------------------------------ pack pipeline
class PackArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: uuid.UUID
    pack_id: str = Field(pattern=r"^D[1-9]$")


@DBOS.step(name="veloxa.pack_pipeline.admit", retries_allowed=True, max_attempts=3, should_retry=_transient)
def _admit(tenant_id: str, pack_id: str) -> int:
    from ..ingest.pipeline import admit_pack
    from ..releases import AdmissionRefused, current_time, load_schedule

    tid = uuid.UUID(tenant_id)
    with connect(_dsn("ingest")) as conn:
        sealed = {b["pack_id"]: b["batch_seq"] for b in ledger.batches(conn, tid) if b["sealed"]}
        if pack_id in sealed:
            return sealed[pack_id]                                 # guard: never re-admit
        root = config.data_path()
        if root is None:
            raise PermanentFailure("VELOXA_DATA_PATH is not configured")
        try:
            admit_pack(conn, tid, load_schedule(), pack_id, root, current_time())
        except AdmissionRefused as e:
            raise PermanentFailure(f"admission refused: {e}") from e
        sealed = {b["pack_id"]: b["batch_seq"] for b in ledger.batches(conn, tid) if b["sealed"]}
    if pack_id not in sealed:
        raise PermanentFailure("the pack was not sealed")
    return sealed[pack_id]


@DBOS.step(name="veloxa.pack_pipeline.project", retries_allowed=True, max_attempts=3, should_retry=_transient)
def _project(tenant_id: str) -> int:
    from .. import projector

    with connect(_dsn("projection")) as conn:
        return sum(rows for _, rows in projector.build(conn, uuid.UUID(tenant_id)))


@DBOS.step(name="veloxa.pack_pipeline.identity", retries_allowed=True, max_attempts=3, should_retry=_transient)
def _identity(tenant_id: str, batch_seq: int) -> dict:
    from ..resolution import integrate

    with connect(_dsn("projection")) as conn:
        return integrate.sync_batch(conn, uuid.UUID(tenant_id), batch_seq)


@DBOS.step(name="veloxa.workflow.audit", retries_allowed=True, max_attempts=3, should_retry=_transient)
def _audit(tenant_id: str, workflow_id: str, name: str, detail: dict) -> None:
    tid = uuid.UUID(tenant_id)
    with connect(_dsn("projection")) as conn, tenant_tx(conn, tid):
        audit.append(conn, tid, "workflow.completed", {"workflow_id": workflow_id, "workflow": name, **detail})
    from .. import telemetry

    telemetry.METRICS.inc("veloxa_workflow_completed_total", workflow=name)


@DBOS.workflow(name="veloxa.pack_pipeline.v1", max_recovery_attempts=5)
def pack_pipeline(tenant_id: str, pack_id: str, args_sha256: str) -> dict:
    k = _admit(tenant_id, pack_id)
    projected = _project(tenant_id)
    identities = _identity(tenant_id, k)
    summary = {"pack_id": pack_id, "batch_seq": k, "projected_rows": projected, "identity": identities}
    _audit(tenant_id, DBOS.workflow_id, "veloxa.pack_pipeline.v1", summary)
    return summary


# ------------------------------------------------------------------------------------------------ agent turn
class TurnArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: uuid.UUID
    request_id: uuid.UUID
    interviewer_id: str = Field(pattern=r"^person:[A-Za-z0-9_-]{1,40}$")


@DBOS.step(name="veloxa.agent_turn.turn", retries_allowed=True, max_attempts=3, should_retry=_transient)
def _turn(tenant_id: str, request_id: str, interviewer_id: str) -> dict:
    from .. import tools
    from ..agents.runtime import AgentError, AgentRequest, AgentRuntime
    from ..releases import load_schedule
    from ..temporal import Mode, resolve_basis

    tid = uuid.UUID(tenant_id)
    schedule = load_schedule()
    with connect(_dsn("app")) as conn:
        with tenant_tx(conn, tid, interviewer_id):
            req = conn.execute("SELECT session_id, message, idempotency_key FROM runtime.agent_request WHERE"
                               " request_id = %s", (request_id,)).fetchone()
        if req is None:
            raise PermanentFailure("no such request for this interviewer")
        basis = resolve_basis(tid, ledger.sealed_releases(conn, tid), Mode.CURRENT, None, schedule.time_zone,
                              datetime.now(UTC))
        principal = tools.resolve_principal_by_emp_id(conn, basis, interviewer_id.removeprefix("person:")) \
            if basis.batch_seq else None
        if principal is None or principal.principal_id != interviewer_id:
            raise PermanentFailure("the interviewer does not resolve to exactly one person")
        rt = AGENT_RUNTIME_FACTORY(conn) if AGENT_RUNTIME_FACTORY else AgentRuntime(conn, schedule.time_zone)
        try:
            turn = rt.execute(AgentRequest(tid, principal, req["session_id"], req["message"], req["idempotency_key"]))
        except AgentError as e:
            raise PermanentFailure(f"{e.code}: {e.detail}") from e
    return {"execution_id": turn["execution_id"], "status": turn["status"], "replayed": turn["replayed"]}


@DBOS.workflow(name="veloxa.agent_turn.v1", max_recovery_attempts=5)
def agent_turn(tenant_id: str, request_id: str, interviewer_id: str, args_sha256: str) -> dict:
    return _turn(tenant_id, request_id, interviewer_id)


CATALOGUE: dict[str, Definition] = {
    "veloxa.pack_pipeline.v1": Definition("veloxa.pack_pipeline.v1", PackArgs, pack_pipeline),
    "veloxa.agent_turn.v1": Definition("veloxa.agent_turn.v1", TurnArgs, agent_turn),
}


def submit_agent_request(conn: psycopg.Connection, tenant_id: uuid.UUID, interviewer_id: str, session_id: uuid.UUID,
                         message: str, idempotency_key: str) -> uuid.UUID:
    """Store the message in the application database (RLS, own interviewer) and return its opaque request id.
    Idempotent per (session, key)."""
    with tenant_tx(conn, tenant_id, interviewer_id):
        row = conn.execute("SELECT request_id FROM runtime.agent_request WHERE session_id = %s AND idempotency_key"
                           " = %s", (session_id, idempotency_key)).fetchone()
        if row:
            return row["request_id"]
        rid = uuid.uuid4()
        conn.execute("INSERT INTO runtime.agent_request (tenant_id, request_id, session_id, interviewer_id, message,"
                     " idempotency_key) VALUES (%s,%s,%s,%s,%s,%s)",
                     (tenant_id, rid, session_id, interviewer_id, message, idempotency_key))
    return rid
