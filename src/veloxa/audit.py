"""Append-only, hash-chained audit events (ARCHITECTURE.md §21.3). Details hold structure and counts only."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

import psycopg


def _hash(prev_hash: str | None, seq: int, occurred_at: datetime, kind: str, detail: dict) -> str:
    body = json.dumps(
        {"prev": prev_hash, "seq": seq, "at": occurred_at.astimezone(UTC).isoformat(), "kind": kind,
         "detail": detail},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def append(conn: psycopg.Connection, tenant_id: uuid.UUID, kind: str, detail: dict) -> int:
    """Append one event inside the caller's tenant transaction. Serialised per tenant."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('audit.event:' || %s))", (str(tenant_id),))
    last = conn.execute(
        "SELECT seq, hash FROM audit.event WHERE tenant_id = %s ORDER BY seq DESC LIMIT 1", (tenant_id,)
    ).fetchone()
    seq = (last["seq"] + 1) if last else 1
    prev = last["hash"] if last else None
    occurred_at = datetime.now(UTC)
    conn.execute(
        "INSERT INTO audit.event (tenant_id, seq, occurred_at, kind, detail, prev_hash, hash)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (tenant_id, seq, occurred_at, kind, json.dumps(detail), prev, _hash(prev, seq, occurred_at, kind, detail)),
    )
    return seq


def verify_chain(conn: psycopg.Connection, tenant_id: uuid.UUID) -> bool:
    prev = None
    for row in conn.execute(
        "SELECT seq, occurred_at, kind, detail, prev_hash, hash FROM audit.event WHERE tenant_id = %s ORDER BY seq",
        (tenant_id,),
    ):
        if row["prev_hash"] != prev or row["hash"] != _hash(prev, row["seq"], row["occurred_at"], row["kind"],
                                                             row["detail"]):
            return False
        prev = row["hash"]
    return True
