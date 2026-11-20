"""Database access. Tenant context is always transaction-local (ARCHITECTURE.md §9.3).

Session-level SET is never used: with transaction pooling it would bleed across tenants.
"""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

_POOLS: dict[str, object] = {}
_POOLS_LOCK = threading.Lock()


def _reset(conn: psycopg.Connection) -> None:
    """On return to the pool: no session state may survive into the next request (tenant context is
    transaction-local anyway; this is defence in depth)."""
    conn.execute("RESET ALL")


def _pool(dsn: str, size: int):
    from psycopg_pool import ConnectionPool

    with _POOLS_LOCK:
        pool = _POOLS.get(dsn)
        if pool is None:
            pool = ConnectionPool(dsn, min_size=min(2, size), max_size=size, open=True, reset=_reset,
                                  timeout=float(os.environ.get("VELOXA_DB_POOL_TIMEOUT", "30")),
                                  kwargs={"autocommit": True, "row_factory": dict_row, "connect_timeout": 10},
                                  name="veloxa")
            _POOLS[dsn] = pool
        return pool


def connect(dsn: str):
    """A connection used as `with connect(dsn) as conn:`.

    - Default: a new connection each time (CLI, migrations, tests).
    - With VELOXA_DB_POOL_MAX=<n> (the API process): a pooled connection, returned to the pool on exit, with at
      most n connections per DSN; a request waits up to VELOXA_DB_POOL_TIMEOUT seconds for a free one.
    autocommit=True so that every unit of work is an explicit `conn.transaction()` block; connect_timeout fails
    fast instead of hanging on an unreachable host."""
    size = int(os.environ.get("VELOXA_DB_POOL_MAX", "0") or 0)
    if size > 0:
        return _pool(dsn, size).connection()
    return psycopg.connect(dsn, autocommit=True, row_factory=dict_row, connect_timeout=10)


def pool_stats() -> dict:
    """Pool counters for diagnostics (no DSNs or credentials)."""
    return {name: {k: v for k, v in pool.get_stats().items() if k in (
        "pool_min", "pool_max", "pool_size", "pool_available", "requests_waiting", "requests_num",
        "requests_queued", "requests_wait_ms", "requests_errors", "connections_num", "connections_errors")}
        for name, pool in ((f"pool{i}", p) for i, p in enumerate(_POOLS.values()))}


@contextmanager
def tenant_tx(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    principal_id: str = "system",
    batch_seq: int | None = None,
) -> Iterator[psycopg.Connection]:
    """Open a transaction whose first statement sets the tenant, principal and knowledge point K.

    If batch_seq is None, app.batch_seq is set to '' so that any read of a K-dependent view fails closed.
    """
    with conn.transaction():
        conn.execute(
            "SELECT set_config('app.tenant_id', %s, true),"
            "       set_config('app.principal_id', %s, true),"
            "       set_config('app.batch_seq', %s, true)",
            (str(tenant_id), principal_id, "" if batch_seq is None else str(int(batch_seq))),
        )
        yield conn
