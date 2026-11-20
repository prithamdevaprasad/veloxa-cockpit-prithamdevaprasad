"""Synthetic Company Lab: Orbitra Labs (fictional, ~180 employees) in its OWN isolated database and tenant.

    python scripts/synthetic_lab.py --db veloxa_synthco --create
    python scripts/synthetic_lab.py --db veloxa_synthco --serve --port 8790

Separation (Phase 5): only database names matching `veloxa_synthco` or `veloxa_synthco_<a-z0-9>` are accepted, so
the supplied company (`veloxa`, `veloxa_company_*`) and the Lumora demo lab (`veloxa_demolab*`) can never be targeted.
The tenant is `orbitra`, marked synthetic; every record says SYNTHETIC; the release packs are never read. It reuses
the Demo Lab's governed build path (data room upload, validation, review, authorisation, admission, sealing,
projection, the shared agent runtime). Create never drops or reuses a database.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests"), str(ROOT / "scripts")]
import demo_lab as dl  # noqa: E402

ALLOW = re.compile(r"^veloxa_synthco(_[a-z0-9]{1,16})?$")
TOKEN = "synthetic-orbitra-lab-fixture-token-0123456789"  # noqa: S105  (documented local synthetic fixture)
SLUG = "orbitra"
AGENTS = [  # 14 synthetic agents (5 engineering, 3 finance, 3 people operations, 3 marketing); Orbitra topics only
    ("syn-eng-core", "engineering", "Orbit Core delivery", "What is the status of Orbit Core 3.0?", "Orbit Core"),
    ("syn-eng-pulse", "engineering", "Pulse delivery", "When did Pulse 1.5 ship?", "Pulse"),
    ("syn-eng-vega", "engineering", "Vega delivery", "When is Vega 3.0 due?", "Vega"),
    ("syn-eng-reliability", "engineering", "Reliability and incidents", "Which incidents affected Lattice?",
     "Lattice"),
    ("syn-eng-comet", "engineering", "Comet delivery", "What is the Comet experience work?", "Comet"),
    ("syn-fin-revenue", "finance", "Revenue reporting", "What is the ledger spend?", None),
    ("syn-fin-vendors", "finance", "Vendor spend", "What is the spend by vendor?", None),
    ("syn-fin-cash", "finance", "Cash and runway", "What is the ledger spend by cost centre?", None),
    ("syn-ppl-headcount", "people_ops", "Workforce", "What is our headcount by department?", None),
    ("syn-ppl-org", "people_ops", "Organisation", "Who is the Chief Executive Officer?", None),
    ("syn-ppl-hiring", "people_ops", "Hiring", "What is our headcount?", None),
    ("syn-mkt-campaigns", "marketing", "Campaigns", "Which campaigns depend on the Orbit Core release?",
     "Orbit Core"),
    ("syn-mkt-launch", "marketing", "Launch readiness", "What is the Pulse launch wave?", "Pulse"),
    ("syn-mkt-pipeline", "marketing", "Attributed pipeline", "What does the Vega launch wave cover?", "Vega"),
]


def check_name(name: str) -> None:
    if not ALLOW.fullmatch(name):
        raise SystemExit(f"refused: {name!r} is not an allowlisted Synthetic Company Lab database (veloxa_synthco[_x])")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--originals", default=None)
    args = ap.parse_args()
    check_name(args.db)
    admin = os.environ.get("VELOXA_TEST_ADMIN_DSN")
    if not admin:
        print("VELOXA_TEST_ADMIN_DSN is required (a PostgreSQL 18 administrator DSN)")
        return 2
    for role, pw in dl.ROLE_PASSWORDS.items():
        os.environ.setdefault(f"VELOXA_{role}_PASSWORD", pw)
    from synthetic_company import orbitra

    from veloxa.storage import LocalContentStore

    originals = Path(args.originals or Path(tempfile.gettempdir()) / "veloxa-synthco-originals")
    os.environ["VELOXA_ORIGINALS_DIR"] = str(originals)
    store = LocalContentStore(originals)
    if args.create:
        dl.ALLOW = ALLOW                                  # the shared creator checks THIS lab's allowlist
        dl.create_database(admin, args.db)
        dsns = {k: dl._dsn(admin, args.db, None if k == "admin" else k)
                for k in ("admin", "app", "ingest", "projection")}
        s = dl.populate(dsns, store, Path(tempfile.mkdtemp(prefix="orbitra-")), slug=SLUG, gen=orbitra,
                        company="Orbitra Labs (SYNTHETIC)", domain=orbitra.DOMAIN, agents=AGENTS)
        print(f"Synthetic Company Lab ready in {args.db}: tenant {s['slug']}, {s['files']} files admitted, "
              f"{s['facts']['headcount_total']} employees, {s['agents']} agents, {s['updates']} agent updates")
    if args.serve:
        import uvicorn

        from veloxa.agents.runtime import AgentRuntime
        from veloxa.api import PRIVILEGED_ENV, create_app

        os.environ.update({"VELOXA_DEV_AUTH": "1", "VELOXA_DEV_TOKEN": TOKEN, "VELOXA_DEV_PRINCIPAL": dl.CEO,
                           "VELOXA_DEV_TENANT": SLUG})
        os.environ.setdefault("VELOXA_RATE_LIMIT_PER_MIN", "100000")
        os.environ.setdefault("VELOXA_RATE_LIMIT_EXPENSIVE_PER_MIN", "100000")
        from agent_fixtures import SyntheticTestKnowledgeBoundary

        app_dsn = dl._dsn(admin, args.db, "app")
        for k in PRIVILEGED_ENV:
            os.environ.pop(k, None)
        app = create_app(dsn=app_dsn, store_factory=lambda: store,
                         agent_runtime=lambda conn: AgentRuntime(conn, "Asia/Kolkata",
                                                                 kb=SyntheticTestKnowledgeBoundary()))
        print(f"Synthetic Company Lab API on http://127.0.0.1:{args.port}; token {TOKEN} (synthetic CEO)", flush=True)
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning",
                    workers=1, limit_concurrency=int(os.environ.get("VELOXA_LIMIT_CONCURRENCY", "200")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
