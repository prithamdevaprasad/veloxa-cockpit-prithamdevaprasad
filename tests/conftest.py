"""Shared fixtures. Database tests need VELOXA_TEST_ADMIN_DSN (a PostgreSQL 18 administrator DSN).

Each test session creates a throwaway database, migrates it, and drops it afterwards.
Each admission test gets its own fresh tenant, because admitted history can never be deleted.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

sys.path.insert(0, str(Path(__file__).parent))

# Local-development defaults, identical to docker-compose.yml; CI sets its own values.
for _role, _default in (("MIGRATOR", "local-dev-migrator"), ("APP", "local-dev-app"), ("INGEST", "local-dev-ingest"),
                        ("PROJECTION", "local-dev-projection")):
    os.environ.setdefault(f"VELOXA_{_role}_PASSWORD", _default)

ADMIN_DSN = os.environ.get("VELOXA_TEST_ADMIN_DSN")


def _with_db(dsn: str, database: str, user: str | None = None, password: str | None = None) -> str:
    parts = urlsplit(dsn)
    netloc = parts.netloc
    if user is not None:
        host = netloc.rsplit("@", 1)[-1]
        netloc = f"{user}:{password}@{host}"
    return urlunsplit((parts.scheme, netloc, f"/{database}", parts.query, parts.fragment))


class TestDatabase:
    def __init__(self, admin_dsn: str, name: str):
        self.name = name
        self.admin_dsn = _with_db(admin_dsn, name)

    def dsn(self, role: str) -> str:
        return _with_db(self.admin_dsn, self.name, f"veloxa_{role}", os.environ[f"VELOXA_{role.upper()}_PASSWORD"])


@pytest.fixture(scope="session")
def db():
    if not ADMIN_DSN:
        pytest.skip("VELOXA_TEST_ADMIN_DSN not set: database tests skipped")
    import psycopg

    from veloxa.migrate import migrate

    name = f"veloxa_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    tdb = TestDatabase(ADMIN_DSN, name)
    try:
        migrate(tdb.admin_dsn, tdb.dsn("migrator"), name)
        yield tdb
    finally:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def tenant(db):
    """A fresh tenant for one test (created by the administrator, like the tenant directory would)."""
    import psycopg

    tid = uuid.uuid4()
    with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO directory.tenant (tenant_id, slug, synthetic, time_zone) VALUES (%s, %s, true, 'UTC')",
            (tid, f"t-{tid.hex[:12]}"),
        )
    return tid


@pytest.fixture
def ingest_conn(db):
    from veloxa.db import connect

    with connect(db.dsn("ingest")) as conn:
        yield conn


@pytest.fixture
def app_conn(db):
    from veloxa.db import connect

    with connect(db.dsn("app")) as conn:
        yield conn


@pytest.fixture
def schedule():
    from veloxa.releases import load_schedule

    return load_schedule()


@pytest.fixture
def synthetic_root(tmp_path):
    from synthetic_pack import make_d1

    make_d1(tmp_path)
    return tmp_path


def at(text: str) -> datetime:
    return datetime.fromisoformat(text)


D1_DAY = at("2026-11-16T12:00:00+05:30")
AFTER_ALL = at("2026-11-20T12:00:00+05:30")
