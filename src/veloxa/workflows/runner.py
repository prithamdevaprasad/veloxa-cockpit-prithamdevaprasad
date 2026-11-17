"""C26 durable execution: the `ActivityRunner` contract (ARCHITECTURE.md §5.3, §17.1) and its DBOS adapter (§17.2).

DBOS is the approved initial runtime: workflows as a library, state in PostgreSQL system tables, no extra service
(no Conductor). The adapter is thin: DBOS's own system tables are the ONLY workflow journal. They live in a
SEPARATE database (`VELOXA_WORKFLOW_DATABASE_URL`), so DBOS can never touch Veloxa's application tables, and they are
outside Veloxa's RLS, which is why workflow inputs and step outputs carry only opaque ids and counts (§6 of the
Sprint 10 directive): never messages, evidence, payroll values, tokens or connection strings.

Correctness rules (§17.4):
- every workflow has a pinned, versioned name (`veloxa.<name>.v<N>`) and validated, declared arguments;
- the workflow id is `<name>:<idempotency key>`: starting it again returns the same run; reusing a key with
  different arguments is refused;
- `application_version` is recorded on every run (DBOS stores it), so code changes are gated for in-flight runs;
- steps are idempotent or guarded; retries are declared per step.
Moving to Temporal (§17.5) replaces this adapter, not the activities or the contract.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

APPLICATION_VERSION = "veloxa-workflows/1"
EXECUTOR_ID = "veloxa-local"


class WorkflowError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code, self.detail = code, detail


@dataclass(frozen=True)
class WorkflowHandle:
    workflow_id: str
    name: str


@dataclass(frozen=True)
class WorkflowState:
    workflow_id: str
    name: str
    status: str                    # PENDING | ENQUEUED | SUCCESS | ERROR | CANCELLED | MAX_RECOVERY_ATTEMPTS_EXCEEDED
    application_version: str | None
    error: str | None


class ActivityRunner(Protocol):
    def start(self, workflow: str, args: dict, idempotency_key: str) -> WorkflowHandle: ...
    def state(self, workflow_id: str) -> WorkflowState | None: ...
    def result(self, workflow_id: str) -> Any: ...
    def cancel(self, workflow_id: str) -> None: ...


@dataclass(frozen=True)
class Definition:
    name: str                       # pinned and versioned
    args: type[BaseModel]
    fn: Any                         # the DBOS-registered workflow function


def system_database_url() -> str:
    url = os.environ.get("VELOXA_WORKFLOW_DATABASE_URL")
    if not url:
        raise WorkflowError("not_configured", "VELOXA_WORKFLOW_DATABASE_URL (the DBOS system database) is required")
    return url


def _args_digest(args: dict) -> str:
    return hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()


class DBOSActivityRunner:
    """The DBOS adapter. One per process (DBOS is process-wide). `catalogue` maps pinned names to definitions."""

    def __init__(self, catalogue: dict[str, Definition], system_db_url: str | None = None,
                 executor_id: str = EXECUTOR_ID):
        from dbos import DBOS

        self.catalogue = catalogue
        self._dbos = DBOS(config={"name": "veloxa", "system_database_url": system_db_url or system_database_url(),
                                  "application_version": APPLICATION_VERSION, "executor_id": executor_id,
                                  "log_level": "WARNING", "enable_otlp": False})

    def launch(self) -> None:
        from dbos import DBOS

        DBOS.launch()                    # also recovers this executor's PENDING workflows

    def shutdown(self) -> None:
        from dbos import DBOS

        DBOS.destroy(destroy_registry=False)

    def start(self, workflow: str, args: dict, idempotency_key: str) -> WorkflowHandle:
        from dbos import DBOS, SetWorkflowID

        d = self.catalogue.get(workflow)
        if d is None:
            raise WorkflowError("unknown_workflow", f"{workflow!r} is not in the workflow catalogue")
        if not isinstance(idempotency_key, str) or not 8 <= len(idempotency_key) <= 200:
            raise WorkflowError("invalid_request", "idempotency_key must be 8 to 200 characters")
        try:
            clean = d.args.model_validate(args).model_dump(mode="json")
        except ValidationError as e:
            raise WorkflowError("invalid_arguments", str(e).splitlines()[0]) from e
        wid = f"{workflow}:{idempotency_key}"
        clean["args_sha256"] = _args_digest({k: v for k, v in clean.items() if k != "args_sha256"})
        existing = DBOS.list_workflows(workflow_ids=[wid], load_input=True, load_output=False)
        if existing:
            prior = (existing[0].input or {}).get("kwargs", {}) if isinstance(existing[0].input, dict) else {}
            if prior.get("args_sha256") not in (None, clean["args_sha256"]):
                raise WorkflowError("idempotency_conflict", "the key was used for a run with different arguments")
            return WorkflowHandle(wid, workflow)
        with SetWorkflowID(wid):
            DBOS.start_workflow(d.fn, **clean)
        return WorkflowHandle(wid, workflow)

    def state(self, workflow_id: str) -> WorkflowState | None:
        from dbos import DBOS

        s = DBOS.get_workflow_status(workflow_id)
        if s is None:
            return None
        return WorkflowState(s.workflow_id, s.name, s.status, s.app_version,
                             type(s.error).__name__ if s.error else None)

    def result(self, workflow_id: str) -> Any:
        from dbos import DBOS

        return DBOS.retrieve_workflow(workflow_id).get_result()

    def cancel(self, workflow_id: str) -> None:
        from dbos import DBOS

        DBOS.cancel_workflow(workflow_id)
