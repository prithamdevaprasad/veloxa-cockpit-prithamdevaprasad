"""Database access. Tenant context is always transaction-local (ARCHITECTURE.md §9.3).

Session-level SET is never used: with transaction pooling it would bleed across tenants.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row


def connect(dsn: str) -> psycopg.Connection:
    # autocommit=True so that every unit of work is an explicit `conn.transaction()` block.
    # connect_timeout: fail fast instead of hanging on an unreachable host.
    return psycopg.connect(dsn, autocommit=True, row_factory=dict_row, connect_timeout=10)


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
