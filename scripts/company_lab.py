"""Veloxa Company Lab: the supplied company (the hackathon release packs) in an isolated, allowlisted database.

    python scripts/company_lab.py --db veloxa_company --create          # new database, migrated (never reused)
    python scripts/company_lab.py --db veloxa_company --admit           # admit every RELEASED + AUTHORISED pack
    python scripts/company_lab.py --dry-run D3                          # parse + contracts + K-13 check, no database
    python scripts/company_lab.py --inventory out.json                  # deterministic source inventory (no DB)
    python scripts/company_lab.py --db veloxa_company --serve --port 8780

**Safety:**
- **Database name:** only `veloxa_company` or `veloxa_company_<a-z0-9>`. The real `veloxa` database and every test
  database are refused before connecting. Nothing is ever dropped.
- **Release and authorisation:** admission uses the real clock and `config/release_schedule.toml`; a pack is
  admitted only when released AND authorised source by source (`config/admission_authorisations.toml`, K-13).
  There is no clock override: an embargoed pack (e.g. D4 before 2026-11-19 10:00 IST) is never opened for parsing.
- **Same pipeline as production:** `ingest.pipeline.admit_pack` (C06-isolated parsing, contracts, sealing, audit),
  then the projector. The original files are read only, never modified or copied into the repository.
- **Data path:** `VELOXA_DATA_PATH` (the folder that contains `Day1_Mon_16Nov` ...).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
ALLOW = re.compile(r"^veloxa_company(_[a-z0-9]{1,16})?$")
ROLE_PASSWORDS = {"MIGRATOR": "local-dev-migrator", "APP": "local-dev-app", "INGEST": "local-dev-ingest",
                  "PROJECTION": "local-dev-projection"}
LAB_TOKEN = "company-lab-local-fixture-token-0123456789"  # noqa: S105  (local fixture for the isolated lab only)


def _dsn(admin: str, db: str, role: str | None = None) -> str:
    p = urlsplit(admin)
    netloc = p.netloc
    if role is not None:
        pw = os.environ.get(f"VELOXA_{role.upper()}_PASSWORD", ROLE_PASSWORDS[role.upper()])
        netloc = f"veloxa_{role}:{pw}@{p.netloc.rsplit('@', 1)[-1]}"
    return urlunsplit((p.scheme, netloc, f"/{db}", p.query, p.fragment))


def check_name(name: str) -> None:
    if not ALLOW.fullmatch(name):
        raise SystemExit(f"refused: {name!r} is not an allowlisted Company Lab database (veloxa_company[_x])")


def data_root() -> Path:
    raw = os.environ.get("VELOXA_DATA_PATH")
    if not raw or not (Path(raw) / "Day1_Mon_16Nov").is_dir():
        raise SystemExit("VELOXA_DATA_PATH must point at the folder containing Day1_Mon_16Nov")
    return Path(raw)


def inventory(root: Path, now: datetime, admitted: dict[str, dict] | None = None) -> list[dict]:
    """Every file in every Day folder: hash, size, catalogue rule, release state. Embargoed files are hashed only
    (never parsed). Deterministic order: pack, then path."""
    from veloxa.catalogue import classify, load_catalogue
    from veloxa.releases import load_authorisations, load_schedule

    schedule, rules, auth = load_schedule(), load_catalogue(), load_authorisations()
    out = []
    for pack in schedule.packs:
        folder = root / pack.folder
        released = pack.release_at <= now
        for f in sorted(p for p in folder.rglob("*") if p.is_file()):
            rel = f.relative_to(folder).as_posix()
            try:
                rule = classify(rules, rel)
                rule_d = {"kind": rule.kind, "system": rule.system, "data_class": rule.data_class,
                          "records": rule.records}
            except Exception:
                rule_d = None
            a = auth.get(pack.id)
            source_ok = bool(a) and (a.baseline or any(__import__("fnmatch").fnmatchcase(rel, g) for g in a.sources))
            state = ("embargoed" if not released else "admitted" if admitted and pack.id in admitted
                     else "authorised" if source_ok and rule_d else "awaiting_authorisation")
            out.append({
                "source_id": f"{pack.id}:{rel}", "pack": pack.id, "day_folder": pack.folder, "file": rel,
                "sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "bytes": f.stat().st_size,
                "source_type": rule_d["kind"] if rule_d else None, "system": rule_d["system"] if rule_d else None,
                "classification": rule_d["data_class"] if rule_d else None,
                "release_at": pack.release_at.isoformat(), "released": released,
                "admitted_batch": (admitted or {}).get(pack.id, {}).get("batch_seq"),
                "admitted_at": (admitted or {}).get(pack.id, {}).get("admitted_at"),
                "status": state,
            })
    return out


def dry_run(pack_id: str) -> None:
    from collections import Counter

    from veloxa.catalogue import load_catalogue
    from veloxa.ingest.pipeline import prepare_pack, record_id
    from veloxa.releases import AdmissionRefused, check_admissible, load_authorisations, load_schedule

    schedule, now = load_schedule(), datetime.now(UTC)
    pack = schedule.pack(pack_id)
    try:
        check_admissible(schedule, pack_id, now, {p.batch_seq for p in schedule.packs if p.batch_seq < pack.batch_seq})
    except AdmissionRefused as e:
        raise SystemExit(f"{pack_id}: {e.reason}: not opened") from None
    entries, digest, parsed = prepare_pack(data_root() / pack.folder, load_catalogue())
    auth = load_authorisations()
    if pack_id not in auth:
        raise SystemExit(f"{pack_id}: not authorised")
    auth[pack_id].check_sources([e.relpath for e in entries])
    ids = Counter(record_id(e, env) for e, _, env in parsed)
    dups = [r for r, n in ids.items() if n > 1]
    if dups:
        raise SystemExit(f"{pack_id}: {len(dups)} duplicate record identities (e.g. system {dups[0].split(':')[0]})")
    print(f"{pack_id}: {len(entries)} files, {len(parsed)} records, by class "
          f"{dict(Counter(c for _, c, _ in parsed))}, manifest {digest[:12]} (dry run: nothing admitted)")


def create(admin: str, name: str) -> None:
    import psycopg

    from veloxa.migrate import migrate

    check_name(name)
    with psycopg.connect(admin, autocommit=True) as c:
        if c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
            raise SystemExit(f"refused: {name} already exists (never reused or overwritten)")
        c.execute(f'CREATE DATABASE "{name}"')
    for role, pw in ROLE_PASSWORDS.items():
        os.environ.setdefault(f"VELOXA_{role}_PASSWORD", pw)
    migrate(_dsn(admin, name), _dsn(admin, name, "migrator"), name)
    print(f"{name}: created and migrated")


def admit(admin: str, name: str, args_review: bool = False) -> list:
    from veloxa import config, ledger, projector
    from veloxa.agents import registry
    from veloxa.db import connect
    from veloxa.ingest.pipeline import admit_pack
    from veloxa.releases import AdmissionRefused, load_authorisations, load_schedule

    check_name(name)
    schedule, auth, root = load_schedule(), load_authorisations(), data_root()
    tid = config.tenant_uuid(schedule.tenant_slug)
    now = datetime.now(UTC)
    results = []
    with connect(_dsn(admin, name, "ingest")) as conn:
        for pack in schedule.packs:
            if pack.release_at > now:
                print(f"{pack.id}: embargoed until {pack.release_at.isoformat()}; not opened")
                break
            if pack.id not in auth:
                print(f"{pack.id}: released but not authorised (K-13); not admitted")
                break
            try:
                r = admit_pack(conn, tid, schedule, pack.id, root, now, authorisation=auth[pack.id])
            except AdmissionRefused as e:
                print(f"{pack.id}: refused ({e.reason}): {e}")
                break
            results.append(r)
            print(f"{r.pack_id}: {r.status} as batch {r.batch_seq}: {r.file_count} files, {r.record_count} records")
    from veloxa import knowledge

    with connect(_dsn(admin, name, "projection")) as conn:
        projector.build(conn, tid)
        print("knowledge claims by batch:", knowledge.backfill(conn, tid))
    with connect(_dsn(admin, name)) as a:
        registry.seed_slots(a, tid)
        batches = ledger.batches(a, tid)
        k = max((b["batch_seq"] for b in batches if b["sealed"]), default=0)
        if k:
            from veloxa.agents import people

            print("agents grounded in HR records:", people.ground(a, tid, k) or "already grounded")
            from veloxa import tenancy
            from veloxa.db import tenant_tx as _tt

            with _tt(a, tid):
                ceo = a.execute("SELECT emp_id FROM projection.person WHERE title = 'CEO' ORDER BY batch_seq DESC"
                                " LIMIT 1").fetchone()
                has = ceo and a.execute("SELECT 1 FROM policy.live_tuple WHERE resource_type = 'tenant'"
                                        " AND relation = 'company_reader' AND subject_id = %s",
                                        (ceo["emp_id"],)).fetchone()
                if ceo and not has:      # the pre-workspace tenant's CEO already reads tenant-wide; make it explicit
                    tenancy.grant_as_operator(a, tid, "tenant", str(tid), "company_reader", f"person:{ceo['emp_id']}",
                                              "CEO company-wide read (pre-workspace tenant; lab only)")
            if args_review:
                from veloxa import findings
                from veloxa.risk import governance
                from veloxa.signals.registry import assessment_date
                from veloxa.temporal import Mode, resolve_basis

                pol = governance.current_policy(a, tid)
                with _tt(a, tid):
                    content = a.execute("SELECT content FROM policy.ranking_decision WHERE version = %s",
                                        (pol.version,)).fetchone()["content"] if pol.status == "approved" else None
                    basis = resolve_basis(tid, ledger.sealed_releases(a, tid), Mode.CURRENT, None, "Asia/Kolkata",
                                          datetime.now(UTC))
                    print("daily review:", findings.daily_review(a, tid, k, assessment_date(basis, "Asia/Kolkata"),
                                                                 content))
    print("sealed batches:", [(b["pack_id"], b["batch_seq"]) for b in batches if b["sealed"]])
    return results


def admitted_batches(admin: str, name: str) -> dict[str, dict]:
    from veloxa import config, ledger
    from veloxa.db import connect
    from veloxa.releases import load_schedule

    check_name(name)
    tid = config.tenant_uuid(load_schedule().tenant_slug)
    with connect(_dsn(admin, name)) as a:
        return {b["pack_id"]: {"batch_seq": b["batch_seq"], "admitted_at": b["admitted_at"].astimezone(UTC).isoformat()}
                for b in ledger.batches(a, tid) if b["sealed"]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db")
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--admit", action="store_true")
    ap.add_argument("--review", action="store_true", help="run the findings daily review after admission")
    ap.add_argument("--migrate", action="store_true", help="apply new migrations to THIS lab database only")
    ap.add_argument("--dry-run", metavar="PACK")
    ap.add_argument("--inventory", metavar="JSON")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--port", type=int, default=8780)
    args = ap.parse_args()
    admin = os.environ.get("VELOXA_TEST_ADMIN_DSN", "")
    if args.dry_run:
        dry_run(args.dry_run)
        return 0
    if args.inventory:
        adm = admitted_batches(admin, args.db) if args.db else None
        inv = inventory(data_root(), datetime.now(UTC), adm)
        Path(args.inventory).write_text(json.dumps(inv, indent=2), encoding="utf-8")
        print(f"{len(inv)} files; by status {dict(__import__('collections').Counter(i['status'] for i in inv))}")
        return 0
    if not args.db or not admin:
        raise SystemExit("--db and VELOXA_TEST_ADMIN_DSN are required")
    check_name(args.db)
    if args.create:
        create(admin, args.db)
    if args.migrate:
        from veloxa.migrate import migrate

        for role, pw in ROLE_PASSWORDS.items():
            os.environ.setdefault(f"VELOXA_{role}_PASSWORD", pw)
        migrate(_dsn(admin, args.db), _dsn(admin, args.db, "migrator"), args.db)
        print(f"{args.db}: migrated")
    if args.admit or args.review:
        admit(admin, args.db, args.review)
    if args.serve:
        import uvicorn

        from veloxa.api import PRIVILEGED_ENV, create_app

        app_dsn = _dsn(admin, args.db, "app")
        for k in PRIVILEGED_ENV + ("VELOXA_TEST_ADMIN_DSN",):
            os.environ.pop(k, None)
        os.environ.update({"VELOXA_DEV_AUTH": "1", "VELOXA_DEV_TOKEN": LAB_TOKEN,
                           "VELOXA_DEV_PRINCIPAL": os.environ.get("VELOXA_LAB_PRINCIPAL", "T001")})
        print(f"Company Lab API on http://127.0.0.1:{args.port}; local fixture token {LAB_TOKEN}", flush=True)
        uvicorn.run(create_app(dsn=app_dsn), host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
