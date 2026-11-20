"""D-15 governance gate (ARCHITECTURE.md §16.2, §16.7, §26.1; DECISIONS.md D-15).

Weights, anchors, guardrail tiers and gate thresholds are OWNER value judgements. This module makes that
enforceable:
- The policy in force is read from `policy.ranking_decision` (migration 0005), which only the privileged command
  line can write (`veloxa policy record-d15`); the application role can only read it, and no API request can
  supply weights or tiers.
- While no approved decision is recorded, D-15 is PENDING and `weighted_ranking` refuses to run.
- An approved decision is versioned (1, 2, 3 ...), append-only, hashed and audited; a change is a new version.
  Every briefing snapshot records the policy status, version and hash in force when it was produced.
- This engine version implements NO weighted ranking. Even with an approved decision it keeps serving the
  unweighted comparison and says so explicitly; it never switches silently.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import psycopg

from .. import audit
from ..db import tenant_tx

ENGINE_VERSION = "risk-v1.0-d15-weighted"
WEIGHTED_RANKING_IMPLEMENTED = True   # D-15 v1 (veloxa.risk.ranking)
REQUIRED_CONTENT = ("weights", "anchors", "guardrail_tiers", "gate_thresholds")


class RankingBlocked(Exception):
    """Weighted ranking was requested but may not run."""


@dataclass(frozen=True)
class PolicyState:
    status: str                       # "pending" | "approved"
    version: int | None = None
    sha256: str | None = None
    approved_by: str | None = None
    approved_on: date | None = None
    decision_record: str | None = None

    def as_json(self) -> dict:
        return {"decision": "D-15", "status": self.status, "version": self.version, "sha256": self.sha256,
                "approved_by": self.approved_by,
                "approved_on": self.approved_on.isoformat() if self.approved_on else None,
                "decision_record": self.decision_record}


PENDING = PolicyState("pending")


def current_policy(conn: psycopg.Connection, tenant_id: uuid.UUID) -> PolicyState:
    with tenant_tx(conn, tenant_id):
        row = conn.execute(
            "SELECT version, content_sha256, approved_by, approved_on, decision_record FROM policy.ranking_decision"
            " WHERE tenant_id = %s ORDER BY version DESC LIMIT 1", (tenant_id,)).fetchone()
    if row is None:
        return PENDING
    return PolicyState("approved", row["version"], row["content_sha256"], row["approved_by"], row["approved_on"],
                       row["decision_record"])


def ranking_mode(policy: PolicyState) -> str:
    """The BRIEFING's mode (Today view, top_three). It stays the unweighted partial order in every case: top_three
    semantics are preserved (it is null). The D-15 weighted ranked list is a separate view (`/v1/worry`)."""
    return "unweighted_partial_order"


def weighted_ranking(policy: PolicyState, items: list, content: dict | None = None) -> dict:
    """The only entry point for weighted ranking. Blocked while D-15 is pending; otherwise ranks with the RECORDED
    decision content (versioned, hashed, audited in policy.ranking_decision), never with request-supplied weights."""
    if policy.status != "approved" or content is None:
        raise RankingBlocked("weighted ranking cannot run: D-15 (risk weights, anchors, guardrail tiers, gate "
                             "thresholds) is pending an owner decision")
    from . import ranking

    return ranking.rank(items, content)


def owner_decision_status(policy: PolicyState) -> str:
    if policy.status == "pending":
        return "D-15 pending"
    return f"D-15 approved (version {policy.version}); weighted ranking by {ENGINE_VERSION} (/v1/worry)"


def load_decision_file(path: Path) -> tuple[dict, dict, str]:
    """Read an owner decision file: [decision] approved_by, approved_on, decision_record; [content] with the
    approved weights, anchors, guardrail tiers and gate thresholds. Returns (decision, content, sha256)."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    decision, content = data.get("decision", {}), data.get("content", {})
    if decision.get("decision_ref") != "D-15":
        raise ValueError("decision_ref must be 'D-15'")
    if decision.get("status") != "approved":
        raise ValueError("only an APPROVED owner decision can be recorded (status = 'approved')")
    for k in ("approved_by", "decision_record"):
        if not str(decision.get(k, "")).strip():
            raise ValueError(f"{k} is required")
    if not isinstance(decision.get("approved_on"), date):
        raise ValueError("approved_on must be a date (YYYY-MM-DD)")
    missing = [k for k in REQUIRED_CONTENT if k not in content]
    if missing:
        raise ValueError(f"content must state every D-15 element; missing: {', '.join(missing)}")
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
    return decision, content, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def record_decision(conn: psycopg.Connection, tenant_id: uuid.UUID, path: Path) -> int:
    """Privileged: record an owner-approved D-15 decision as the next version, with an audit event."""
    decision, content, digest = load_decision_file(path)
    with tenant_tx(conn, tenant_id):
        latest = conn.execute("SELECT max(version) AS v FROM policy.ranking_decision WHERE tenant_id = %s",
                              (tenant_id,)).fetchone()["v"] or 0
        version = latest + 1
        conn.execute(
            "INSERT INTO policy.ranking_decision (tenant_id, version, decision_ref, status, approved_by, approved_on,"
            " decision_record, content, content_sha256) VALUES (%s, %s, 'D-15', 'approved', %s, %s, %s, %s, %s)",
            (tenant_id, version, decision["approved_by"], decision["approved_on"], decision["decision_record"],
             json.dumps(content, default=str), digest))
        audit.append(conn, tenant_id, "policy.d15_recorded", {"version": version, "sha256": digest})
    return version
