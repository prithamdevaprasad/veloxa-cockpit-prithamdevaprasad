"""Veloxa Demo Lab (Profile B): a fully populated FICTIONAL company in an isolated, allowlisted database.

    python scripts/demo_lab.py --db veloxa_demolab --create         # build (the database must not exist yet)
    python scripts/demo_lab.py --db veloxa_demolab --serve --port 8770

**Safety:**
- **Database name:** only names matching `veloxa_demolab` or `veloxa_demolab_<a-z0-9>` are accepted. Anything
  else, including the real `veloxa` database and every `veloxa_test_*`, is refused before connecting to it.
- **Create, never drop:** the database is created only with `--create`, and only if it does not exist. Nothing is
  ever dropped (remove it yourself with `DROP DATABASE veloxa_demolab`).
- **Same architecture as production:** tenants, ReBAC roles, source types, the data room (upload, validation,
  review, separate authorisation, admission, sealing, projection), the shared agent runtime and persisted
  sessions. No business record is inserted directly.
- **Synthetic only:** every record is fictional and carries a synthetic marker. Agents are `syn-` agents, marked
  DEMO. The original release packs are never read.
"""

from __future__ import annotations

import argparse
import base64  # noqa: F401  (kept for parity with the upload API)
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
ALLOW = re.compile(r"^veloxa_demolab(_[a-z0-9]{1,16})?$")
ROLE_PASSWORDS = {"MIGRATOR": "local-dev-migrator", "APP": "local-dev-app", "INGEST": "local-dev-ingest",
                  "PROJECTION": "local-dev-projection"}
DEMO_TOKEN = "synthetic-demo-lab-fixture-token-0123456789"  # noqa: S105  (documented local demo fixture)
CEO = "person:demo-ceo"
ANALYST = "person:demo-analyst"
AUTHORISER = "person:demo-authoriser"
ADMIN = "person:demo-admin"

AGENTS = [  # 14 synthetic demo agents: 5 engineering, 3 finance, 3 people operations, 3 marketing
    ("syn-eng-platform", "engineering", "Platform delivery", "What is the status of the Nimbus platform work?",
     "Nimbus"),
    ("syn-eng-reliability", "engineering", "Reliability and incidents", "Which incidents affected Helix?", "Helix"),
    ("syn-eng-releases", "engineering", "Release readiness", "When is Helix 2.0 due?", "Helix"),
    ("syn-eng-quartz", "engineering", "Quartz delivery", "What happened to Quartz 2.0?", "Quartz"),
    ("syn-eng-juniper", "engineering", "Juniper delivery", "What is the Juniper experience work?", "Juniper"),
    ("syn-fin-revenue", "finance", "Revenue reporting", "What is the ledger spend?", None),
    ("syn-fin-vendors", "finance", "Vendor spend", "What is the spend by vendor?", None),
    ("syn-fin-cash", "finance", "Cash and runway", "What is the ledger spend by cost centre?", None),
    ("syn-ppl-headcount", "people_ops", "Workforce", "What is our headcount by department?", None),
    ("syn-ppl-org", "people_ops", "Organisation", "Who is the Chief Executive Officer?", None),
    ("syn-ppl-hiring", "people_ops", "Hiring", "What is our headcount?", None),
    ("syn-mkt-campaigns", "marketing", "Campaigns", "Which campaigns depend on the Quartz release?", "Quartz"),
    ("syn-mkt-launch", "marketing", "Launch readiness", "What is the Nimbus launch wave?", "Nimbus"),
    ("syn-mkt-pipeline", "marketing", "Attributed pipeline", "What does the Kestrel launch wave cover?", "Kestrel"),
]


def _dsn(admin: str, db: str, role: str | None = None) -> str:
    p = urlsplit(admin)
    if role is None:
        netloc = p.netloc
    else:
        pw = os.environ.get(f"VELOXA_{role.upper()}_PASSWORD", ROLE_PASSWORDS[role.upper()])
        netloc = f"veloxa_{role}:{pw}@{p.netloc.rsplit('@', 1)[-1]}"
    return urlunsplit((p.scheme, netloc, f"/{db}", p.query, p.fragment))


def check_name(name: str) -> None:
    if not ALLOW.fullmatch(name):
        raise SystemExit(f"refused: {name!r} is not an allowlisted Demo Lab database (veloxa_demolab[_x])")


def create_database(admin: str, name: str) -> None:
    import psycopg

    check_name(name)
    with psycopg.connect(admin, autocommit=True) as c:
        if c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
            raise SystemExit(f"refused: {name} already exists (the Demo Lab never reuses or overwrites a database)")
        c.execute(f'CREATE DATABASE "{name}"')
    for role, pw in ROLE_PASSWORDS.items():
        os.environ.setdefault(f"VELOXA_{role}_PASSWORD", pw)
    from veloxa.migrate import migrate

    migrate(_dsn(admin, name), _dsn(admin, name, "migrator"), name)


def populate(dsns: dict, store, files_dir: Path, slug: str = "demo") -> dict:
    """Build the demo workspace inside an already-migrated database (also used by the benchmark tests)."""
    from synthetic_company import generate as gen

    from veloxa import intake, product, tenancy
    from veloxa.db import connect

    manifest = gen.generate(files_dir)
    with connect(dsns["admin"]) as a, connect(dsns["app"]) as app:
        tid = tenancy.create_tenant(a, slug, "Asia/Kolkata", "Lumora Systems (DEMO)", synthetic=True)
        tenancy.bootstrap_synthetic_admin(a, tid, ADMIN, "Demo Workspace Admin")
        people = {CEO: "Demo CEO", ANALYST: "Demo Analyst", AUTHORISER: "Demo Data Owner",
                  "person:demo-outsider": "Demo Outsider (no roles)"}
        for d in gen.DEPARTMENTS:
            people[f"person:demo-{d}-head"] = f"Demo {gen.DEPARTMENTS[d]} Head"
            people[f"person:demo-{d}-contrib"] = f"Demo {gen.DEPARTMENTS[d]} Contributor"
            people[f"person:demo-{d}-reviewer"] = f"Demo {gen.DEPARTMENTS[d]} Reviewer"
        for pid, name in people.items():
            tenancy.add_synthetic_member(a, tid, pid, name, f"{pid.split(':')[1]}@lumora.example")
        g = lambda *x: tenancy.grant(app, tid, ADMIN, *x)  # noqa: E731
        for d, name in gen.DEPARTMENTS.items():
            tenancy.create_department(app, tid, ADMIN, d, name, "demo setup")
            g("department", d, "head", f"person:demo-{d}-head", "demo head")
            g("department", d, "contributor", f"person:demo-{d}-contrib", "demo contributor")
            g("department", d, "reviewer", f"person:demo-{d}-reviewer", "demo reviewer")
        g("tenant", str(tid), "company_reader", CEO, "explicit company-wide read for the demo CEO")
        g("tenant", str(tid), "admission_authoriser", AUTHORISER, "demo data owner")
        for st, dept, kind, system, cls, records, glob, title, keys, _f in gen.SOURCES:
            g("evidence_source", system, "read", f"department:{dept}", f"{dept} owns {st}", "member")
            g("evidence_source", system, "read", ANALYST, "demo analyst (agent interviews)")
            intake.authorise_source_type(app, tid, AUTHORISER, source_type=st, title=title, department_id=dept,
                                         kind=kind, system=system, data_class=cls, records=records, file_glob=glob,
                                         contract=None, key_columns=keys,
                                         decision_record=f"DEMO-{st} (synthetic Demo Lab)")
    # Every file through the data room: upload -> validate -> review -> authorise -> admit (system worker).
    for st, dept, *_rest in gen.SOURCES:
        for f in sorted((files_dir / st).iterdir()):
            with connect(dsns["app"]) as app:
                sid = intake.upload(app, store, tid, f"person:demo-{dept}-contrib", dept, st, f.stem.replace("_", " "),
                                    [intake.Upload(f.name, f.read_bytes())], "2026")
            with connect(dsns["ingest"]) as ing:
                rep = intake.validate(ing, store, tid, sid)
            if rep["errors"]:
                raise RuntimeError(f"demo file {f.name} failed validation: {rep['errors']}")
            with connect(dsns["app"]) as app:
                intake.decide(app, tid, f"person:demo-{dept}-reviewer", sid, "accept", "demo review")
                intake.decide(app, tid, AUTHORISER, sid, "authorise", "demo authorisation")
            with connect(dsns["ingest"]) as ing, connect(dsns["projection"]) as prj:
                intake.admit(ing, prj, store, tid, sid)
    # 14 synthetic agents on the shared runtime; one persisted interview each; updates FROM those sessions.
    from agent_fixtures import SyntheticTestKnowledgeBoundary

    from veloxa import ledger
    from veloxa.agents import registry
    from veloxa.agents.runtime import AgentRequest, AgentRuntime
    from veloxa.releases import current_time
    from veloxa.temporal import Mode, resolve_basis
    from veloxa.tools import Principal

    with connect(dsns["projection"]) as prj:
        for aid, cat, _p, _q, _proj in AGENTS:
            caps, tools_ = registry.category_default(cat)
            registry.publish(prj, tid, registry.AgentConfig(aid, cat, caps, tools_, activation="synthetic_test"))
    updates = 0
    with connect(dsns["app"]) as app:
        rt = AgentRuntime(app, "Asia/Kolkata", kb=SyntheticTestKnowledgeBoundary())
        basis = resolve_basis(tid, ledger.sealed_releases(app, tid), Mode.CURRENT, None, "Asia/Kolkata",
                              current_time())
        who = Principal(ANALYST, "authenticated", "demo-analyst" + "@" + "lumora.example")  # synthetic
        for i, (aid, _cat, _purpose, question, project) in enumerate(AGENTS):
            sid = rt.open_session(tid, who, aid, basis)["session_id"]
            rt.execute(AgentRequest(tid, who, sid, question, f"demo-lab-{i:03d}-q1"))
            try:
                product.publish_update(app, tid, ANALYST, sid, project)
                updates += 1
            except product.ProductError:
                pass                                             # an unanswered session publishes nothing
    return {"tenant_id": str(tid), "slug": slug, "files": sum(1 for _ in files_dir.rglob("*.*")),
            "agents": len(AGENTS), "updates": updates, "facts": manifest["facts"], "manifest": manifest}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--create", action="store_true", help="create and populate (the database must not exist)")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--originals", default=None, help="original store directory (default: a temp directory)")
    args = ap.parse_args()
    check_name(args.db)
    admin = os.environ.get("VELOXA_TEST_ADMIN_DSN")
    if not admin:
        print("VELOXA_TEST_ADMIN_DSN is required (a PostgreSQL 18 administrator DSN)")
        return 2
    for role, pw in ROLE_PASSWORDS.items():
        os.environ.setdefault(f"VELOXA_{role}_PASSWORD", pw)
    from veloxa.storage import LocalContentStore

    originals = Path(args.originals or os.environ.get("VELOXA_ORIGINALS_DIR")
                     or Path(tempfile.gettempdir()) / "veloxa-demolab-originals")
    os.environ["VELOXA_ORIGINALS_DIR"] = str(originals)
    store = LocalContentStore(originals)
    if args.create:
        create_database(admin, args.db)
        dsns = {k: _dsn(admin, args.db, None if k == "admin" else k) for k in ("admin", "app", "ingest", "projection")}
        summary = populate(dsns, store, Path(tempfile.mkdtemp(prefix="lumora-")))
        print(f"Demo Lab ready in {args.db}: tenant {summary['slug']}, {summary['files']} files admitted through the "
              f"data room, {summary['agents']} synthetic agents, {summary['updates']} agent updates")
    if args.serve:
        import threading
        import time

        import uvicorn

        from veloxa import intake
        from veloxa.agents.runtime import AgentRuntime
        from veloxa.api import PRIVILEGED_ENV, create_app
        from veloxa.db import connect

        os.environ.update({"VELOXA_DEV_AUTH": "1", "VELOXA_DEV_TOKEN": DEMO_TOKEN, "VELOXA_DEV_PRINCIPAL": CEO,
                           "VELOXA_DEV_TENANT": "demo"})
        os.environ.setdefault("VELOXA_RATE_LIMIT_PER_MIN", "6000")
        os.environ.setdefault("VELOXA_RATE_LIMIT_EXPENSIVE_PER_MIN", "600")

        ingest_dsn, projection_dsn = _dsn(admin, args.db, "ingest"), _dsn(admin, args.db, "projection")

        def worker():                                   # the data-room worker for uploads made in the demo UI
            while True:
                try:
                    with connect(ingest_dsn) as i, connect(projection_dsn) as p:
                        intake.work_once(i, p, store)
                except Exception:  # noqa: BLE001, S110 - demo worker: keep polling
                    pass
                time.sleep(3)

        threading.Thread(target=worker, daemon=True).start()
        from agent_fixtures import SyntheticTestKnowledgeBoundary

        app_dsn = _dsn(admin, args.db, "app")
        for k in PRIVILEGED_ENV:                        # the API process never holds privileged credentials
            os.environ.pop(k, None)
        app = create_app(dsn=app_dsn, store_factory=lambda: store,
                         agent_runtime=lambda conn: AgentRuntime(conn, "Asia/Kolkata",
                                                                 kb=SyntheticTestKnowledgeBoundary()))
        print(f"Demo Lab API on http://127.0.0.1:{args.port}; token {DEMO_TOKEN} (demo CEO)", flush=True)
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
