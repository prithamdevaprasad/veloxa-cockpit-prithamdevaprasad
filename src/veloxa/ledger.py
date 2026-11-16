"""Read access to the evidence ledger at an explicit knowledge point K (ARCHITECTURE.md §8.2, §8.4 T1)."""

from __future__ import annotations

import uuid
from datetime import datetime

import psycopg

from .db import tenant_tx


class InvalidTime(Exception):
    pass


def sealed_watermark(conn: psycopg.Connection, tenant_id: uuid.UUID) -> int:
    with tenant_tx(conn, tenant_id):
        row = conn.execute(
            "SELECT coalesce(max(batch_seq), 0) AS k FROM ledger.admission_batch WHERE tenant_id = %s AND sealed",
            (tenant_id,),
        ).fetchone()
    return int(row["k"])


def k_for_instant(conn: psycopg.Connection, tenant_id: uuid.UUID, at: datetime) -> int:
    """KNOWN_AT: the highest batch sealed with a release time at or before `at` (0 if none)."""
    with tenant_tx(conn, tenant_id):
        row = conn.execute(
            "SELECT coalesce(max(batch_seq), 0) AS k FROM ledger.admission_batch"
            " WHERE tenant_id = %s AND sealed AND release_at <= %s",
            (tenant_id, at),
        ).fetchone()
    return int(row["k"])


def visible_record_ids(conn: psycopg.Connection, tenant_id: uuid.UUID, k: int) -> list[str]:
    """Record IDs visible at K. A K beyond the sealed watermark is rejected (§8.2)."""
    if k < 0 or k > sealed_watermark(conn, tenant_id):
        raise InvalidTime(f"knowledge point {k} is beyond the sealed watermark")
    with tenant_tx(conn, tenant_id, batch_seq=k):
        rows = conn.execute("SELECT record_id FROM ledger.visible_source_record ORDER BY record_id").fetchall()
    return [r["record_id"] for r in rows]


def sealed_releases(conn: psycopg.Connection, tenant_id: uuid.UUID) -> list[tuple[int, datetime]]:
    """[(batch_seq, release_at)] for sealed batches: the input to temporal.resolve_basis."""
    with tenant_tx(conn, tenant_id):
        rows = conn.execute(
            "SELECT batch_seq, release_at FROM ledger.admission_batch WHERE tenant_id = %s AND sealed ORDER BY 1",
            (tenant_id,),
        ).fetchall()
    return [(r["batch_seq"], r["release_at"]) for r in rows]


def batches(conn: psycopg.Connection, tenant_id: uuid.UUID) -> list[dict]:
    with tenant_tx(conn, tenant_id):
        return conn.execute(
            "SELECT batch_seq, pack_id, release_at, sealed, sealed_at, file_count, record_count, manifest_sha256"
            " FROM ledger.admission_batch WHERE tenant_id = %s ORDER BY batch_seq",
            (tenant_id,),
        ).fetchall()
