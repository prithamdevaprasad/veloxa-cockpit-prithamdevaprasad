"""Command line: veloxa init | migrate | ingest | project | status | selfcheck | serve | policy.

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
from .ingest.pipeline import AdmissionFailure, admit_pack, record_failure
from .migrate import migrate
from .releases import (
    AdmissionRefused,
    current_time,
    load_authorisations,
    load_schedule,
    require_authorised,
)


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
    """Admit packs: an EXPLICIT administrative operation. Only released packs listed in
    config/admission_authorisations.toml are admitted; anything else is refused before a file is opened.
    Exit 0 if everything requested is admitted (or simply not released yet), 1 if an admission failed or was
    refused (recorded as an audit event; nothing partial is committed), 2 if the data folder is missing."""
    root = _data_root()
    if root is None:
        return EXIT_DATA_MISSING
    schedule = load_schedule()
    tenant_id = config.tenant_uuid(schedule.tenant_slug)
    now = current_time()
    authorised = load_authorisations()
    with connect(_dsn("ingest")) as conn:
        results, failure = [], None
        requested = [p.id for p in schedule.released(now)] if args.pack == "released" else [args.pack]
        for pack_id in requested:
            try:
                require_authorised(pack_id, authorised)
                results.append(admit_pack(conn, tenant_id, schedule, pack_id, root, now))
            except Exception as exc:  # nothing partial is ever committed
                failure = AdmissionFailure(pack_id, exc)
                break
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


def cmd_workflow_pack(args: argparse.Namespace) -> int:
    """Run the durable pack pipeline (admit, project, identity sync) for one pack through DBOS (C26). Needs the
    ingest and projection credentials and VELOXA_WORKFLOW_DATABASE_URL. Idempotent per --key."""
    from .workflows.catalogue import CATALOGUE
    from .workflows.runner import DBOSActivityRunner

    schedule = load_schedule()
    tenant_id = config.tenant_uuid(schedule.tenant_slug)
    runner = DBOSActivityRunner(CATALOGUE)
    runner.launch()
    try:
        h = runner.start("veloxa.pack_pipeline.v1", {"tenant_id": str(tenant_id), "pack_id": args.pack},
                         args.key or f"pack-{args.pack}-{tenant_id}")
        out = runner.result(h.workflow_id)
        print(f"workflow {h.workflow_id}: batch {out['batch_seq']}, projected rows {out['projected_rows']}")
    finally:
        runner.shutdown()
    return 0


def cmd_workflow_status(args: argparse.Namespace) -> int:
    """Show a workflow's status (ids and status only; never inputs or outputs)."""
    from dbos import DBOSClient

    from .workflows.runner import system_database_url

    client = DBOSClient(system_database_url=system_database_url())
    try:
        found = client.list_workflows(workflow_ids=[args.workflow_id], load_input=False, load_output=False)
    finally:
        client.destroy()
    if not found:
        print("no such workflow")
        return 1
    s = found[0]
    print(f"{s.workflow_id}: {s.status} (workflow {s.name}, version {s.app_version})")
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    """C31: a verified logical backup of the configured database (read-only on the source)."""
    from pathlib import Path

    from .ops import backup as ops

    tools = ops.PgTools.from_env()
    if tools is None:
        print("PostgreSQL client tools (>= 18) not found: set VELOXA_PG_TOOLS (a bin directory or docker:<container>)")
        return 2
    m = ops.backup(tools, _admin_dsn(), config.db_settings().database, Path(args.out))
    print(f"backup {m['file']}: {m['bytes']} bytes, sha256 {m['sha256'][:16]}..., audit chains valid "
          f"{m['fingerprint']['audit_chains_valid']}")
    return 0


def cmd_restore_drill(args: argparse.Namespace) -> int:
    """C31: restore a backup into a NEW disposable database, compare fingerprints, drop the copy."""
    import json
    from pathlib import Path

    from .ops import backup as ops

    tools = ops.PgTools.from_env()
    if tools is None:
        print("PostgreSQL client tools (>= 18) not found: set VELOXA_PG_TOOLS")
        return 2
    dump = Path(args.dump)
    manifest = json.loads(dump.with_name(dump.name.replace(".dump", ".manifest.json")).read_text(encoding="utf-8"))
    r = ops.restore_drill(tools, _admin_dsn(), dump, manifest)
    print(f"restore drill: {'PASSED' if r['ok'] else 'FAILED'} in {r['seconds']:.1f}s"
          + ("" if r["ok"] else f" ({'; '.join(r['differences'])})"))
    return 0 if r["ok"] else 1


def cmd_agents_seed(_: argparse.Namespace) -> int:
    """Publish the 14 proposed agent slots, unassigned and blocked (configuration role). Idempotent."""
    from .agents import registry

    schedule = load_schedule()
    tenant_id = config.tenant_uuid(schedule.tenant_slug)
    with connect(_dsn("projection")) as conn:
        created = registry.seed_slots(conn, tenant_id)
    print(f"agent slots published: {len(created)} (all unassigned and blocked)")
    return 0


def cmd_identity_sync(_: argparse.Namespace) -> int:
    """Register C12 source identities and candidates from every sealed batch (projection role). Decides nothing:
    release-pack candidates stay unresolved."""
    from .resolution import integrate

    schedule = load_schedule()
    tenant_id = config.tenant_uuid(schedule.tenant_slug)
    with connect(_dsn("projection")) as conn:
        sealed = [b["batch_seq"] for b in ledger.batches(conn, tenant_id) if b["sealed"]]
        for k in sealed:
            counts = integrate.sync_batch(conn, tenant_id, k)
            print(f"identity sync batch {k}: " + ", ".join(f"{n}={v}" for n, v in counts.items()))
    if not sealed:
        print("identity sync: no sealed batch")
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


def cmd_signals(args: argparse.Namespace) -> int:
    """Run the C1-C4 detectors at the current basis (application role) and print COUNTS only: no subject, excerpt
    or value is printed, so the output is safe for logs. With --record, the run is recorded (migration 0006)."""
    from collections import Counter

    from .signals import registry
    from .temporal import Mode, resolve_basis

    schedule = load_schedule()
    tenant_id = config.tenant_uuid(schedule.tenant_slug)
    now = current_time()
    with connect(_dsn("app")) as conn:
        basis = resolve_basis(tenant_id, ledger.sealed_releases(conn, tenant_id), Mode.CURRENT, None,
                              schedule.time_zone, now)
        result = registry.run(conn, basis, registry.context_for(basis, schedule.time_zone))
        ref = registry.record(conn, result, now) if args.record and basis.batch_seq else None
    print(f"read basis K = {basis.batch_seq}; assessment date {result.assessment_date.isoformat()}")
    counts = Counter((s.criterion.value, s.signal_type.value, s.status.value) for s in result.signals)
    for (crit, stype, status), n in sorted(counts.items()):
        print(f"  {crit} {stype:<20} {status:<34} {n}")
    print(f"  withheld notes: {len(result.withheld)}; output sha256 {result.output_sha256[:16]}")
    if ref:
        print(f"  recorded run {ref['run_id']}")
    return 0


def cmd_selfcheck(args: argparse.Namespace) -> int:
    with connect(_dsn(args.role)) as conn:
        failures = selfcheck.run(conn)
    for f in failures:
        print(f"FAIL: {f}")
    print("self-check: " + ("PASS" if not failures else "FAIL") + f" (role veloxa_{args.role})")
    return 0 if not failures else 1


def cmd_policy_record_d15(args: argparse.Namespace) -> int:
    """Privileged: record an OWNER-APPROVED D-15 decision (risk weights, anchors, guardrail tiers, gate thresholds)
    as the next policy version. The API cannot do this. Recording a decision does not enable weighted ranking in
    this engine version; that needs a separate, explicitly approved implementation."""
    from pathlib import Path

    from .risk import governance

    schedule = load_schedule()
    try:
        with connect(_admin_dsn()) as conn:
            version = governance.record_decision(conn, config.tenant_uuid(schedule.tenant_slug), Path(args.file))
    except ValueError as e:
        print(f"ERROR: D-15 decision not recorded: {e}", file=sys.stderr)
        return 1
    print(f"D-15 decision recorded as policy version {version}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .api import create_app

    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    """Privileged one-shot start-up step: wait for the database, migrate, then self-check. It NEVER admits a later
    release pack: admission is an explicit operation (`veloxa ingest --pack <id>`).

    Only a FRESH database (no sealed batch at all) is bootstrapped, with the authorised baseline pack(s) from
    config/admission_authorisations.toml. Migrations are committed before any admission is attempted, so a failed
    admission can never roll back or corrupt a migrated schema. Availability (owner decision, 16 Nov 2026): with a
    sealed baseline the API serves it; init fails only if a migration or the self-check fails, or if a fresh
    database has no data folder or its baseline admission fails (nothing to serve).
    """
    _wait_for_db()
    cmd_migrate(args)
    schedule = load_schedule()
    tenant_id = config.tenant_uuid(schedule.tenant_slug)
    with connect(_dsn("ingest")) as conn:
        k = ledger.sealed_watermark(conn, tenant_id)
    if k == 0:
        baseline = [a.pack_id for a in load_authorisations().values() if a.baseline]
        if not baseline:
            print("init: FAILED: the database has no sealed batch and no authorised baseline pack",
                  file=sys.stderr)
            return EXIT_NO_SEALED_BASELINE
        for pack_id in sorted(baseline, key=lambda i: schedule.pack(i).batch_seq):
            rc = cmd_ingest(argparse.Namespace(pack=pack_id))
            if rc == EXIT_DATA_MISSING:
                return rc
        with connect(_dsn("ingest")) as conn:
            k = ledger.sealed_watermark(conn, tenant_id)
        if k == 0:
            print("init: FAILED: no sealed baseline batch exists, so there is nothing to serve; the API will not "
                  "start", file=sys.stderr)
            return EXIT_NO_SEALED_BASELINE
    with connect(_dsn("ingest")) as conn:
        admitted = {b["pack_id"] for b in ledger.batches(conn, tenant_id)}
    for p in schedule.released(current_time()):
        if p.id not in admitted:
            print(f"init: {p.id} is released but not admitted; init never admits a later pack. Admission is an "
                  f"explicit, authorised operation (veloxa ingest --pack {p.id}). Serving K = {k}.",
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
    p_sig = sub.add_parser("signals", help="run the C1-C4 evidence detectors and print counts (application role)")
    p_sig.add_argument("--record", action="store_true", help="record the run (append-only)")
    p_sig.set_defaults(func=cmd_signals)
    p_prj = sub.add_parser("project", help="build projections for sealed batches")
    p_prj.add_argument("--rebuild", action="store_true", help="drop and rebuild all projections from the ledger")
    p_prj.set_defaults(func=cmd_project)
    p_wf = sub.add_parser("workflow-pack", help="C26: run the durable pack pipeline for one pack (DBOS)")
    p_wf.add_argument("--pack", required=True)
    p_wf.add_argument("--key", default=None, help="idempotency key (default: pack and tenant)")
    p_wf.set_defaults(func=cmd_workflow_pack)
    p_ws = sub.add_parser("workflow-status", help="C26: show a workflow's status")
    p_ws.add_argument("workflow_id")
    p_ws.set_defaults(func=cmd_workflow_status)
    p_bk = sub.add_parser("backup", help="C31: verified logical backup of the configured database (privileged)")
    p_bk.add_argument("--out", required=True)
    p_bk.set_defaults(func=cmd_backup)
    p_rd = sub.add_parser("restore-drill", help="C31: restore a backup into a disposable database and compare")
    p_rd.add_argument("--dump", required=True)
    p_rd.set_defaults(func=cmd_restore_drill)
    sub.add_parser("agents-seed", help="publish the 14 proposed agent slots (unassigned, blocked)"
                   ).set_defaults(func=cmd_agents_seed)
    sub.add_parser("identity-sync", help="C12: register identities and candidates from sealed batches (counts only)"
                   ).set_defaults(func=cmd_identity_sync)
    p_sc = sub.add_parser("selfcheck", help="run startup self-checks for a role")
    p_sc.add_argument("--role", choices=("app", "ingest"), default="app")
    p_sc.set_defaults(func=cmd_selfcheck)
    p_init = sub.add_parser("init", help="privileged one-shot: migrate; bootstrap a fresh database only")
    p_init.set_defaults(func=cmd_init)
    p_pol = sub.add_parser("policy", help="owner decisions (privileged)")
    pol_sub = p_pol.add_subparsers(dest="policy_command", required=True)
    p_d15 = pol_sub.add_parser("record-d15", help="record an owner-approved D-15 decision file as a new version")
    p_d15.add_argument("--file", required=True)
    p_d15.set_defaults(func=cmd_policy_record_d15)
    p_srv = sub.add_parser("serve", help="run the API (application role only)")
    p_srv.add_argument("--host", default="0.0.0.0")
    p_srv.add_argument("--port", type=int, default=8000)
    p_srv.set_defaults(func=cmd_serve)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
