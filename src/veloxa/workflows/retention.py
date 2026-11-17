"""DBOS workflow-record retention (C26). DRY-RUN unless an owner decision enables deletion.

DBOS 3.5.0 has NO automatic garbage collection. Workflow records stay until deleted explicitly with
`delete_workflows`, so nothing here runs on its own.

**Proposed (NOT approved) periods:**
- successful runs: 90 days;
- failed runs: 180 days.

These are owner decisions (OWNER_DECISION_REGISTER, retention). Until they are approved, `plan()` only reports what
would be removed. `apply()` refuses unless all of these are set:
- `VELOXA_WORKFLOW_RETENTION_DECISION`: the owner decision record;
- `VELOXA_WORKFLOW_RETENTION_SUCCESS_DAYS` and `VELOXA_WORKFLOW_RETENTION_ERROR_DAYS`.

**Never removed:**
- pending, enqueued or cancelled-in-flight runs (recovery needs them);
- any ID on the legal-hold list (`VELOXA_WORKFLOW_LEGAL_HOLD`, comma-separated).

Deleting DBOS records never touches Veloxa's audit chain, which keeps a `workflow.completed` event per run. Removing
an old run therefore removes replay of that run's checkpoints, not the evidence that it ran.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

PROPOSED_SUCCESS_DAYS = 90
PROPOSED_ERROR_DAYS = 180
REMOVABLE = {"SUCCESS": "success", "ERROR": "error", "MAX_RECOVERY_ATTEMPTS_EXCEEDED": "error"}


class RetentionNotApproved(Exception):
    pass


@dataclass(frozen=True)
class Plan:
    success_days: int
    error_days: int
    approved: bool
    remove: tuple[str, ...]
    held: tuple[str, ...]
    kept_active: int


def _setting(name: str) -> int | None:
    v = os.environ.get(name, "").strip()
    return int(v) if v.isdigit() else None


def _legal_hold() -> set[str]:
    return {x.strip() for x in os.environ.get("VELOXA_WORKFLOW_LEGAL_HOLD", "").split(",") if x.strip()}


def plan(client, now: datetime | None = None, success_days: int | None = None, error_days: int | None = None) -> Plan:
    """What WOULD be removed. `client` is a DBOSClient. Uses approved settings when present, else the proposals."""
    now = now or datetime.now(UTC)
    s_days = success_days or _setting("VELOXA_WORKFLOW_RETENTION_SUCCESS_DAYS") or PROPOSED_SUCCESS_DAYS
    e_days = error_days or _setting("VELOXA_WORKFLOW_RETENTION_ERROR_DAYS") or PROPOSED_ERROR_DAYS
    hold = _legal_hold()
    remove, held, active = [], [], 0
    for w in client.list_workflows(load_input=False, load_output=False):
        kind = REMOVABLE.get(w.status)
        if kind is None:
            active += 1
            continue
        finished = datetime.fromtimestamp((w.updated_at or w.created_at) / 1000, UTC)
        cutoff = now - timedelta(days=s_days if kind == "success" else e_days)
        if finished < cutoff:
            (held if w.workflow_id in hold else remove).append(w.workflow_id)
    approved = bool(os.environ.get("VELOXA_WORKFLOW_RETENTION_DECISION", "").strip()
                    and _setting("VELOXA_WORKFLOW_RETENTION_SUCCESS_DAYS")
                    and _setting("VELOXA_WORKFLOW_RETENTION_ERROR_DAYS"))
    return Plan(s_days, e_days, approved, tuple(sorted(remove)), tuple(sorted(held)), active)


def apply(client, p: Plan) -> int:
    """Delete the planned records, only with an owner-approved configuration. Returns the number removed."""
    if not p.approved:
        raise RetentionNotApproved("workflow retention is not approved: set the decision record and both periods")
    if p.remove:
        client.delete_workflows(list(p.remove))
    return len(p.remove)
