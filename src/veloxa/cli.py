"""Command line: veloxa init | migrate | ingest | status | selfcheck | serve.

`init` is the privileged one-shot step (migrations and admission). `serve` runs the API with the
application role only and refuses privileged credentials.

Output is limited to counts, hashes and release metadata; record values are never printed.
"""

from __future__ import annotations

import argparse
import sys
import time

from . import config, ledger, projector, selfcheck
from .db import connect
from .ingest.pipeline import AdmissionFailure, admit_pack, admit_released, record_failure
from .migrate import migrate
from .releases import AdmissionRefused, current_time, load_schedule


def _dsn(role: str) -> str:
    return config.db_settings().dsn(f"veloxa_{role}", config.role_password(role))


def _admin_dsn() -> str:
    user, password = config.admin_credentials()
    return config.db_settings().dsn(user, password)


def _wait_for_db(timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            with connect(_admin_dsn()):
                return
        except Exception:
            if time.monotonic() > deadline:
                raise
            time.sleep(1.0)


def cmd_migrate(_: argparse.Namespace) -> int:
    applied = migrate(_admin_dsn(), _dsn("migrator"), config.db_settings().database)
    print(f"migrations applied: {', '.join(applied) if applied else 'none (up to date)'}")
    return 0


def _data_root():
    root = config.data_path()
    if root is None or not root.is_dir():
        print(
            "ERROR: challenge data not found. Set VELOXA_DATA_PATH to the extracted release-pack folder "
            "(the folder that contains Day1_Mon_16Nov). The data is never stored in this repository.",
            file=sys.stderr,
        )
        return None
    return root


EXIT_OK, EXIT_ADMISSION_FAILED, EXIT_DATA_MISSING, EXIT_NO_SEALED_BASELINE = 0, 1, 2, 3


def cmd_ingest(args: argparse.Namespace) -> int:
    """Admit packs. Exit 0 if everything requested is admitted (or simply not released yet), 1 if an admission
    failed (recorded as an audit event; nothing partial is committed), 2 if the data folder is missing."""
    root = _data_root()
    if root is None:
        return EXIT_DATA_MISSING
    schedule = load_schedule()
    tenant_id = config.tenant_uuid(schedule.tenant_slug)
    now = current_time()
    with connect(_dsn("ingest")) as conn:
        if args.pack == "released":
            results, failure = admit_released(conn, tenant_id, schedule, root, now)
        else:
            try:
                results, failure = [admit_pack(conn, tenant_id, schedule, args.pack, root, now)], None
            except Exception as exc:
                results, failure = [], AdmissionFailure(args.pack, exc)
        for r in results:
            print(f"{r.pack_id}: {r.status} as batch {r.batch_seq}: {r.file_count} files, {r.record_count} records"
                  + (f", by class {r.by_class}" if r.by_class else ""))
        if failure is None:
            return EXIT_OK
        if isinstance(failure.error, AdmissionRefused) and failure.reason == "not_yet_released":
            print(f"{failure.pack_id}: not released yet; not admitted", file=sys.stderr)
            return EXIT_OK
        record_failure(conn, tenant_id, failure)
    print(f"{failure.pack_id}: admission FAILED ({failure.reason}): {failure.error}. Nothing from this pack was "
          f"admitted; the latest sealed batch is unchanged.", file=sys.stderr)
    return EXIT_ADMISSION_FAILED


def cmd_project(args: argparse.Namespace) -> int:
    """Build projections for every sealed batch not yet projected (or rebuild them all with --rebuild)."""
    schedule = load_schedule()
    tenant_id = config.tenant_uuid(schedule.tenant_slug)
    with connect(_dsn("projection")) as conn:
        built = (projector.rebuild if getattr(args, "rebuild", False) else projector.build)(conn, tenant_id)
    for k, rows in built:
        print(f"projections built for batch {k}: {rows} rows")
    if not built:
        print("projections: up to date")
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    schedule = load_schedule()
    tenant_id = config.tenant_uuid(schedule.tenant_slug)
    with connect(_dsn("app")) as conn:
        rows = {b["pack_id"]: b for b in ledger.batches(conn, tenant_id)}
        k = ledger.sealed_watermark(conn, tenant_id)
    now = current_time()
    print(f"clock: {now.isoformat()}  sealed watermark K = {k}")
    for p in schedule.packs:
        b = rows.get(p.id)
        state = ("sealed" if b and b["sealed"]
                 else "released, pending admission" if p.release_at <= now else "not released")
        counts = f"{b['file_count']} files, {b['record_count']} records" if b else "-"
        print(f"  {p.id} (batch {p.batch_seq}) release {p.release_at.isoformat()}: {state}; {counts}")
    return 0


def cmd_selfcheck(args: argparse.Namespace) -> int:
    with connect(_dsn(args.role)) as conn:
        failures = selfcheck.run(conn)
    for f in failures:
        print(f"FAIL: {f}")
    print("self-check: " + ("PASS" if not failures else "FAIL") + f" (role veloxa_{args.role})")
    return 0 if not failures else 1


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .api import create_app

    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    """Privileged one-shot step: wait for the database, migrate, admit released packs, self-check ingest.

    Availability (owner decisions, 16 Nov 2026): if a LATER pack fails admission, init still succeeds so the API
    keeps running on the latest SEALED batch. The failure is logged and audited; nothing partial is committed,
    and unsealed data is never visible. init fails (and the API does not start) if the data folder is missing,
    a migration fails, the security self-check fails, or there is NO sealed baseline batch at all (for example
    a fresh database whose first pack failed admission).
    """
    _wait_for_db()
    cmd_migrate(args)
    ingest_rc = cmd_ingest(argparse.Namespace(pack="released"))
    if ingest_rc == EXIT_DATA_MISSING:
        return ingest_rc
    schedule = load_schedule()
    with connect(_dsn("ingest")) as conn:
        k = ledger.sealed_watermark(conn, config.tenant_uuid(schedule.tenant_slug))
    if k == 0:
        print("init: FAILED: no sealed baseline batch exists, so there is nothing to serve; the API will not start",
              file=sys.stderr)
        return EXIT_NO_SEALED_BASELINE
    if ingest_rc == EXIT_ADMISSION_FAILED:
        print(f"init: continuing; the API will serve the latest sealed batch (K = {k}) and report the pending pack",
              file=sys.stderr)
    cmd_project(argparse.Namespace(rebuild=False))
    return cmd_selfcheck(argparse.Namespace(role="ingest"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="veloxa", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="create roles/extensions and apply migrations").set_defaults(func=cmd_migrate)
    p_ing = sub.add_parser("ingest", help="admit release packs from VELOXA_DATA_PATH")
    p_ing.add_argument("--pack", default="released", help="pack id (e.g. D1) or 'released' (default)")
    p_ing.set_defaults(func=cmd_ingest)
    sub.add_parser("status", help="show admission status").set_defaults(func=cmd_status)
    p_prj = sub.add_parser("project", help="build projections for sealed batches")
    p_prj.add_argument("--rebuild", action="store_true", help="drop and rebuild all projections from the ledger")
    p_prj.set_defaults(func=cmd_project)
    p_sc = sub.add_parser("selfcheck", help="run startup self-checks for a role")
    p_sc.add_argument("--role", choices=("app", "ingest"), default="app")
    p_sc.set_defaults(func=cmd_selfcheck)
    sub.add_parser("init", help="privileged one-shot: migrate and admit released packs").set_defaults(func=cmd_init)
    p_srv = sub.add_parser("serve", help="run the API (application role only)")
    p_srv.add_argument("--host", default="0.0.0.0")
    p_srv.add_argument("--port", type=int, default=8000)
    p_srv.set_defaults(func=cmd_serve)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
