"""Bootstrap and migrations (ARCHITECTURE.md §6.5, §6.6, §9.4).

1. As the cluster administrator: create the role set and extensions (idempotent), set role passwords.
2. As veloxa_migrator with SET ROLE veloxa_owner: apply versioned migrations once, in order.
3. Register the tenants of the hackathon profile (§25.1): the synthetic tenant and an empty isolation tenant.
"""

from __future__ import annotations

import hashlib
from importlib import resources

import psycopg
from psycopg import sql

from . import config
from .db import connect

LOGIN_ROLES = ("migrator", "app", "ingest", "projection")
TENANTS = (
    # slug, synthetic, time zone
    ("veloxa", True, "Asia/Kolkata"),
    ("isolation-probe", True, "UTC"),  # empty tenant used only for isolation tests (§25.1)
)


def _sql_files() -> list[tuple[str, str]]:
    folder = resources.files("veloxa") / "migrations"
    files = sorted(p.name for p in folder.iterdir() if p.name[:4].isdigit() and p.name.endswith(".sql"))
    return [(name, (folder / name).read_text(encoding="utf-8")) for name in files]


def bootstrap(admin_dsn: str, database: str) -> None:
    text = (resources.files("veloxa") / "migrations" / "bootstrap.sql").read_text(encoding="utf-8")
    text = text.replace("__DATABASE__", sql.Identifier(database).as_string(None))
    with connect(admin_dsn) as conn:
        with conn.transaction():
            conn.execute(text)
            for role in LOGIN_ROLES:
                conn.execute(
                    sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                        sql.Identifier(f"veloxa_{role}"), sql.Literal(config.role_password(role))
                    )
                )


def apply_migrations(migrator_dsn: str) -> list[str]:
    applied: list[str] = []
    with connect(migrator_dsn) as conn:
        with conn.transaction():
            conn.execute("SET LOCAL ROLE veloxa_owner")
            conn.execute("CREATE SCHEMA IF NOT EXISTS veloxa_meta")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS veloxa_meta.schema_migration ("
                " name text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            conn.execute("REVOKE ALL ON SCHEMA veloxa_meta FROM PUBLIC")
        for name, body in _sql_files():
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            with conn.transaction():
                conn.execute("SET LOCAL ROLE veloxa_owner")
                conn.execute("SELECT pg_advisory_xact_lock(hashtext('veloxa_meta.schema_migration'))")
                row = conn.execute(
                    "SELECT sha256 FROM veloxa_meta.schema_migration WHERE name = %s", (name,)
                ).fetchone()
                if row:
                    if row["sha256"] != digest:
                        raise RuntimeError(f"migration {name} changed after it was applied; add a new migration")
                    continue
                conn.execute(body)
                conn.execute(
                    "INSERT INTO veloxa_meta.schema_migration (name, sha256) VALUES (%s, %s)", (name, digest)
                )
                applied.append(name)
        with conn.transaction():
            conn.execute("SET LOCAL ROLE veloxa_owner")
            for slug, synthetic, tz in TENANTS:
                conn.execute(
                    "INSERT INTO directory.tenant (tenant_id, slug, synthetic, time_zone) VALUES (%s, %s, %s, %s)"
                    " ON CONFLICT (tenant_id) DO NOTHING",
                    (config.tenant_uuid(slug), slug, synthetic, tz),
                )
    return applied


def migrate(admin_dsn: str, migrator_dsn: str, database: str) -> list[str]:
    bootstrap(admin_dsn, database)
    return apply_migrations(migrator_dsn)


def server_version_num(dsn: str) -> int:
    with psycopg.connect(dsn) as conn:
        return int(conn.execute("SHOW server_version_num").fetchone()[0])
