"""Reproducible SYNTHETIC demo environment (Sprint 9 demo readiness; also the Playwright backend).

    python scripts/demo_env.py [--port 8765] [--keep]

1. Creates a DISPOSABLE database `veloxa_demo_<random>` on the server in VELOXA_TEST_ADMIN_DSN (never the real
   `veloxa` database), migrates it, and admits the SYNTHETIC D1 pack (tests/synthetic_pack.py) into the configured
   tenant. No challenge material is read.
2. Seeds the 14 agent slots (unassigned, blocked) and ONE labelled synthetic test agent (`syn-demo-ppl`).
3. Grants the demo interviewer (synthetic employee T001) current ReBAC read on the synthetic evidence sources.
4. Serves the real API with the local development fixture. Agents use the explicit TEST knowledge-boundary adapter
   (it answers only for `syn-` agents); every other control is production code.
5. Drops the database on exit unless --keep is given, and drops demo databases left by runs that were killed
   abruptly. Reset = restart.

The demo token is printed; it is a local fixture value, not a secret.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from tempfile import mkdtemp
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
ROLE_PASSWORDS = {"MIGRATOR": "local-dev-migrator", "APP": "local-dev-app", "INGEST": "local-dev-ingest",
                  "PROJECTION": "local-dev-projection"}
DEMO_TOKEN = "synthetic-demo-fixture-token-0123456789abcdef"  # noqa: S105  (documented local demo fixture)
SOURCES = ("keka_hrms", "zoho_books", "documents", "meeting_summaries", "email", "challenge_brief", "data_room")


def dsn(admin: str, db: str, role: str | None = None) -> str:
    p = urlsplit(admin)
    netloc = p.netloc if role is None else f"veloxa_{role}:{ROLE_PASSWORDS[role.upper()]}@{p.netloc.rsplit('@', 1)[-1]}"
    return urlunsplit((p.scheme, netloc, f"/{db}", p.query, p.fragment))


def build(admin_dsn: str) -> tuple[str, uuid.UUID]:
    import psycopg
    from synthetic_pack import make_d1

    from veloxa import config, projector
    from veloxa.agents import registry
    from veloxa.db import connect
    from veloxa.ingest.pipeline import admit_pack
    from veloxa.migrate import migrate
    from veloxa.releases import load_schedule

    for role, pw in ROLE_PASSWORDS.items():
        os.environ.setdefault(f"VELOXA_{role}_PASSWORD", pw)
    name = f"veloxa_demo_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(admin_dsn, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    migrate(dsn(admin_dsn, name), dsn(admin_dsn, name, "migrator"), name)
    schedule = load_schedule()
    tenant = config.tenant_uuid(schedule.tenant_slug)
    with psycopg.connect(dsn(admin_dsn, name), autocommit=True) as a:
        a.execute("INSERT INTO directory.tenant (tenant_id, slug, synthetic, time_zone) VALUES (%s,%s,true,%s)"
                  " ON CONFLICT (tenant_id) DO NOTHING", (tenant, schedule.tenant_slug, schedule.time_zone))
    root = Path(mkdtemp(prefix="veloxa-demo-"))
    make_d1(root)
    with connect(dsn(admin_dsn, name, "ingest")) as c:
        admit_pack(c, tenant, schedule, "D1", root, datetime.now(UTC))
    with connect(dsn(admin_dsn, name, "projection")) as c:
        projector.build(c, tenant)
        registry.seed_slots(c, tenant)
        caps, tools = registry.category_default("people_ops")
        registry.publish(c, tenant, registry.AgentConfig("syn-demo-ppl", "people_ops", caps, tools,
                                                         activation="synthetic_test"))
    with psycopg.connect(dsn(admin_dsn, name), autocommit=True) as a:
        a.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant),))
        for system in SOURCES:
            a.execute("INSERT INTO policy.relationship_tuple (tenant_id, tuple_id, resource_type, resource_id,"
                      " relation, subject_type, subject_id, source) VALUES (%s,%s,'evidence_source',%s,'read',"
                      "'person','T001','synthetic demo')", (tenant, uuid.uuid4(), system))
    return name, tenant


def drop_leftovers(admin: str) -> None:
    """Remove demo databases this script created and could not finish (never any other database)."""
    import psycopg

    with psycopg.connect(admin, autocommit=True) as c:
        for (n,) in c.execute("SELECT datname FROM pg_database").fetchall():
            if n.startswith("veloxa_demo_") and len(n) == len("veloxa_demo_") + 8:
                c.execute(f'DROP DATABASE IF EXISTS "{n}" WITH (FORCE)')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    admin = os.environ.get("VELOXA_TEST_ADMIN_DSN")
    if not admin:
        print("VELOXA_TEST_ADMIN_DSN is required (a PostgreSQL 18 administrator DSN for a disposable database)")
        return 2
    drop_leftovers(admin)       # a previous run killed abruptly (e.g. by a test runner) could not drop its database
    try:
        name, _ = build(admin)
    except Exception:
        drop_leftovers(admin)
        raise
    print(f"demo database {name} ready; token {DEMO_TOKEN}", flush=True)
    import psycopg
    import uvicorn
    from agent_fixtures import SyntheticTestKnowledgeBoundary

    from veloxa.agents.runtime import AgentRuntime
    from veloxa.api import PRIVILEGED_ENV, create_app

    for k in PRIVILEGED_ENV:                         # the API process never holds privileged credentials
        os.environ.pop(k, None)
    os.environ.update({"VELOXA_DEV_AUTH": "1", "VELOXA_DEV_TOKEN": DEMO_TOKEN, "VELOXA_DEV_PRINCIPAL": "T001"})
    os.environ.setdefault("VELOXA_RATE_LIMIT_PER_MIN", "6000")   # one local demo token drives every browser test
    app = create_app(dsn=dsn(admin, name, "app"),
                     agent_runtime=lambda conn: AgentRuntime(conn, "Asia/Kolkata", kb=SyntheticTestKnowledgeBoundary()))
    try:
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    finally:
        if not args.keep:
            with psycopg.connect(admin, autocommit=True) as c:
                c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    return 0


if __name__ == "__main__":
    sys.exit(main())
