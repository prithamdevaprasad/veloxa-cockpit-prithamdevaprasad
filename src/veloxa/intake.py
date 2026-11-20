"""Company data room (migration 0018): upload → validate → review → authorise → admit → seal → project.

The existing ingestion core is reused unchanged:
- **The same code path:** the C06 limits and type checks, the catalogue `Rule` and contracts, and
  `pipeline.prepare_pack` (the same functions admission uses, over the same bytes and hashes).
- **The same admission:** `pipeline.admit_pack`, one sealed batch in the existing ledger, then the projector and C12
  identity sync.

**Who does what:**

| Step | Done by | Checked by |
|---|---|---|
| Upload | the API role (`upload`) | `intake.submit()`: department contributor or head |
| Validate, admit, seal, project | the system worker (`validate`, `admit`): ingest and projection roles | — |
| Review, authorise | humans, through `intake.decide()` | the database, which enforces separation of duties |

Uploading NEVER makes anything searchable: only `admit` (after authorisation) creates evidence.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from . import audit, projector
from .catalogue import Rule
from .db import tenant_tx
from .ingest import isolation
from .ingest.contracts import Contract
from .releases import Pack, Schedule
from .storage import ObjectStore

MEDIA = {".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
         ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         ".pdf": "application/pdf", ".eml": "message/rfc822", ".txt": "text/plain", ".csv": "text/csv",
         ".jsonl": "application/jsonl", ".md": "text/markdown"}
PREVIEW_ROWS = 3
PREVIEW_CHARS = 160


class IntakeError(Exception):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


class NotPermitted(IntakeError):
    pass


@dataclass(frozen=True)
class Upload:
    file_name: str
    data: bytes


def rule_from_row(row: dict) -> Rule:
    contract = Contract.from_toml(row["contract"]) if row["contract"] else None
    return Rule(f"{row['department_id']}/{row['source_type']}/{row['file_glob']}", row["kind"], row["system"],
                row["data_class"], row["records"], None, contract, tuple(row["key_columns"] or ()))


def authorise_source_type(conn, tenant_id, actor: str, *, source_type: str, title: str, department_id: str,
                          kind: str, system: str, data_class: str, records: str, file_glob: str,
                          contract: dict | None, key_columns: list[str], decision_record: str) -> int:
    """An admission authoriser approves a source type (catalogue rule + data class) once, with a decision record."""
    try:
        with tenant_tx(conn, tenant_id, actor):
            v = conn.execute("SELECT policy.authorise_source_type(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s) AS v",
                             (source_type, title, department_id, kind, system, data_class, records, file_glob,
                              json.dumps(contract) if contract else None, key_columns, decision_record)
                             ).fetchone()["v"]
            audit.append(conn, tenant_id, "catalogue.rule_approved", {"source_type": source_type, "version": v,
                                                                      "data_class": data_class, "by": actor})
    except psycopg.errors.InsufficientPrivilege as e:
        raise NotPermitted("not_permitted", e.diag.message_primary) from None
    except psycopg.errors.IntegrityError as e:
        raise IntakeError("invalid", e.diag.message_primary or "invalid source type") from None
    return v


def upload(conn, store: ObjectStore, tenant_id: uuid.UUID, actor: str, department_id: str, source_type: str,
           title: str, files: list[Upload], data_period: str | None = None, route: str | None = "upload"
           ) -> uuid.UUID:
    """Store the originals (write-once) and record the submission. C06 limits and type checks run BEFORE storing;
    the database then checks the contributor relation and the approved source type."""
    lim = isolation.limits()
    if not files:
        raise IntakeError("no_files", "a submission needs at least one file")
    total = 0
    for f in files:
        name = f.file_name.replace("\\", "/").rsplit("/", 1)[-1]          # no paths: the name only
        if name != f.file_name or ".." in name or not name:
            raise IntakeError("unsafe_name", "file names must not contain paths")
        isolation.check_before_read(name, len(f.data), 0, total, lim)
        isolation.check_content(name, Path(name).suffix, f.data, lim)
        total += len(f.data)
        if Path(name).suffix.lower() not in MEDIA:
            raise IntakeError("unsupported_type", f"{Path(name).suffix or 'no extension'} is not a supported format")
    sid = uuid.uuid4()
    meta = []
    for f in files:
        sha = store.put(tenant_id, f.data)
        meta.append({"file_name": f.file_name, "sha256": sha, "size_bytes": len(f.data),
                     "media_type": MEDIA[Path(f.file_name).suffix.lower()]})
    try:
        with tenant_tx(conn, tenant_id, actor):
            conn.execute("SELECT intake.submit(%s, %s, %s, %s, %s, %s::jsonb)",
                         (sid, department_id, source_type, title, data_period, json.dumps(meta)))
            from . import identity

            if route and identity.available(conn):  # connector carriers register ITEMS instead (connectors.submit)
                identity.register(conn, tenant_id, actor, [identity.from_upload(f.file_name, f.data, m["sha256"], route)
                                                           for f, m in zip(files, meta, strict=True)], sid)
            audit.append(conn, tenant_id, "intake.uploaded", {"submission": str(sid), "files": len(meta),
                                                              "department": department_id,
                                                              "source_type": source_type, "by": actor})
    except psycopg.errors.InsufficientPrivilege as e:
        raise NotPermitted("not_permitted", e.diag.message_primary) from None
    except (psycopg.errors.CheckViolation, psycopg.errors.IntegrityError) as e:
        raise IntakeError("invalid", e.diag.message_primary or "invalid submission") from None
    return sid


def decide(conn, tenant_id: uuid.UUID, actor: str, submission_id: uuid.UUID, decision: str, reason: str) -> str:
    """accept | reject (department reviewer) or authorise | decline (admission authoriser). The database enforces
    the relations and separation of duties."""
    try:
        with tenant_tx(conn, tenant_id, actor):
            state = conn.execute("SELECT intake.decide(%s, %s, %s) AS s", (submission_id, decision, reason)
                                 ).fetchone()["s"]
            audit.append(conn, tenant_id, f"intake.{decision}", {"submission": str(submission_id), "by": actor})
    except psycopg.errors.InsufficientPrivilege as e:
        raise NotPermitted("not_permitted", e.diag.message_primary) from None
    except (psycopg.errors.CheckViolation, psycopg.errors.NoDataFound) as e:
        raise IntakeError("invalid", e.diag.message_primary) from None
    return state


def _event(conn, tenant_id, sid, state, actor="system:intake", reason=None, detail=None):
    conn.execute("INSERT INTO intake.submission_event (tenant_id, submission_id, state, actor, reason, detail)"
                 " VALUES (%s, %s, %s, %s, %s, %s::jsonb)", (tenant_id, sid, state, actor, reason,
                                                             json.dumps(detail or {})))


def _load(conn, tenant_id, sid):
    with tenant_tx(conn, tenant_id, "system:intake"):
        sub = conn.execute("SELECT * FROM intake.submission_state WHERE tenant_id = %s AND submission_id = %s",
                           (tenant_id, sid)).fetchone()
        if sub is None:
            raise IntakeError("unknown_submission", "no such submission")
        rule = conn.execute("SELECT * FROM policy.catalogue_rule WHERE tenant_id = %s AND source_type = %s"
                            " AND version = %s", (tenant_id, sub["source_type"], sub["rule_version"])).fetchone()
        files = conn.execute("SELECT * FROM intake.submission_file WHERE tenant_id = %s AND submission_id = %s"
                             " ORDER BY file_name", (tenant_id, sid)).fetchall()
    return sub, rule, files


def _materialise(store, tenant_id, sub, files, root: Path, folder: str) -> Path:
    """Write the stored originals into a private pack folder: <folder>/<department>/<source_type>/<file name>.
    The relative path is stable across submissions, so a re-submitted file is a CORRECTION of its records."""
    base = root / folder / sub["department_id"] / sub["source_type"]
    base.mkdir(parents=True)
    for f in files:
        (base / f["file_name"]).write_bytes(store.get(tenant_id, f["sha256"]))       # verified read
    return root / folder


def _preview(parsed) -> dict:
    by_file: dict[str, dict] = {}
    for entry, cls, env in parsed:
        d = by_file.setdefault(entry.relpath.rsplit("/", 1)[-1], {"records": 0, "data_class": cls, "preview": [],
                                                                  "sheets": set()})
        d["records"] += 1
        if "sheet" in env.locator:
            d["sheets"].add(env.locator["sheet"])
        if len(d["preview"]) < PREVIEW_ROWS:
            d["preview"].append({"locator": env.locator_key(),
                                 "fields": {k: (str(v)[:PREVIEW_CHARS] if v is not None else None)
                                            for k, v in list(env.payload.items())[:12]}})
    for d in by_file.values():
        d["sheets"] = sorted(d["sheets"])
    return by_file


def validate(conn_ingest, store: ObjectStore, tenant_id: uuid.UUID, sid: uuid.UUID) -> dict:
    """System: the dry run. The SAME prepare_pack as admission, over the stored bytes. Writes the report and moves the
    submission to awaiting_review (valid) or validation_failed (with the reason). Nothing becomes evidence."""
    from .ingest.pipeline import prepare_pack

    sub, rule_row, files = _load(conn_ingest, tenant_id, sid)
    if sub["state"] != "uploaded":
        raise IntakeError("wrong_state", f"the submission is {sub['state']}")
    with tenant_tx(conn_ingest, tenant_id, "system:intake"):
        _event(conn_ingest, tenant_id, sid, "validating")
    rule = rule_from_row(rule_row)
    report: dict = {"source_type": sub["source_type"], "department": sub["department_id"],
                    "classification": {"kind": rule.kind, "system": rule.system, "data_class": rule.data_class,
                                       "records": rule.records, "rule_version": rule_row["version"],
                                       "business_key": list(rule.key_columns)},
                    "files": [{"file_name": f["file_name"], "sha256": f["sha256"], "size_bytes": f["size_bytes"],
                               "media_type": f["media_type"]} for f in files],
                    "errors": [], "warnings": [], "duplicates": []}
    digest = None
    tmp = Path(tempfile.mkdtemp(prefix="veloxa-intake-"))
    try:
        pack_dir = _materialise(store, tenant_id, sub, files, tmp, "dryrun")
        try:
            entries, digest, parsed = prepare_pack(pack_dir, [rule])
            report["records"] = len(parsed)
            report["by_file"] = _preview(parsed)
            if rule.contract:
                report["required_columns"] = list(rule.contract.required_columns)
        except Exception as e:  # noqa: BLE001 - every refusal is reported with its reason code, nothing is admitted
            report["errors"].append({"reason": getattr(e, "reason", type(e).__name__), "detail": str(e)[:500]})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    with tenant_tx(conn_ingest, tenant_id, "system:intake"):
        for f in files:          # identical originals in OTHER submissions, or already admitted evidence
            others = conn_ingest.execute(
                "SELECT s.submission_id FROM intake.submission_file s WHERE s.tenant_id = %s AND s.sha256 = %s"
                " AND s.submission_id <> %s", (tenant_id, f["sha256"], sid)).fetchall()
            admitted = conn_ingest.execute("SELECT 1 FROM raw_index.raw_object WHERE tenant_id = %s"
                                           " AND content_sha256 = %s LIMIT 1", (tenant_id, f["sha256"])).fetchone()
            if others or admitted:
                report["duplicates"].append({"file_name": f["file_name"],
                                             "same_file_in_submissions": [str(o["submission_id"]) for o in others],
                                             "already_admitted": admitted is not None})
        if report["duplicates"]:
            report["warnings"].append("identical files exist elsewhere; admitting them again adds no new evidence")
        ok = not report["errors"]
        conn_ingest.execute("INSERT INTO intake.validation_report (tenant_id, submission_id, manifest_sha256, ok,"
                            " report) VALUES (%s, %s, %s, %s, %s::jsonb)",
                            (tenant_id, sid, digest, ok, json.dumps(report, default=str)))
        _event(conn_ingest, tenant_id, sid, "awaiting_review" if ok else "validation_failed",
               reason=None if ok else report["errors"][0]["reason"])
        audit.append(conn_ingest, tenant_id, "intake.validated", {"submission": str(sid), "ok": ok,
                                                                  "records": report.get("records", 0)})
    return report


def admit(conn_ingest, conn_projection, store: ObjectStore, tenant_id: uuid.UUID, sid: uuid.UUID,
          now: datetime | None = None) -> dict:
    """System: admit an AUTHORISED submission through the existing admission sequencer, then project and run
    identity sync. Its knowledge time is the authorisation time (real-company semantics; the hackathon release
    schedule is untouched)."""
    from . import ledger
    from .ingest.pipeline import admit_pack
    from .resolution import integrate

    sub, rule_row, files = _load(conn_ingest, tenant_id, sid)
    if sub["state"] != "authorised":
        raise IntakeError("not_authorised", f"the submission is {sub['state']}, not authorised")
    now = now or datetime.now(UTC)
    with tenant_tx(conn_ingest, tenant_id, "system:intake"):
        _event(conn_ingest, tenant_id, sid, "admitting")
        t = conn_ingest.execute("SELECT slug, time_zone FROM directory.tenant WHERE tenant_id = %s",
                                (tenant_id,)).fetchone()
    batches = ledger.batches(conn_ingest, tenant_id)
    sealed = [b for b in batches if b["sealed"]]
    pack_id = f"S-{str(sid)[:8]}"
    pack = Pack(pack_id, max((b["batch_seq"] for b in batches), default=0) + 1, pack_id, sub["state_at"])
    schedule = Schedule(t["slug"], t["time_zone"],
                        tuple(Pack(b["pack_id"], b["batch_seq"], b["pack_id"], b["release_at"]) for b in sealed)
                        + (pack,))
    tmp = Path(tempfile.mkdtemp(prefix="veloxa-admit-"))
    try:
        _materialise(store, tenant_id, sub, files, tmp, pack_id)
        try:
            result = admit_pack(conn_ingest, tenant_id, schedule, pack_id, tmp, now, rules=[rule_from_row(rule_row)])
        except Exception as e:  # noqa: BLE001 - a failed admission commits nothing; record why
            with tenant_tx(conn_ingest, tenant_id, "system:intake"):
                _event(conn_ingest, tenant_id, sid, "admission_failed", reason=getattr(e, "reason", type(e).__name__))
            raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    with tenant_tx(conn_ingest, tenant_id, "system:intake"):
        _event(conn_ingest, tenant_id, sid, "admitted", detail={"batch_seq": result.batch_seq,
                                                               "records": result.record_count})
        _event(conn_ingest, tenant_id, sid, "sealed", detail={"batch_seq": result.batch_seq})
    projector.build(conn_projection, tenant_id)
    counts = integrate.sync_batch(conn_projection, tenant_id, result.batch_seq)
    with tenant_tx(conn_ingest, tenant_id, "system:intake"):
        _event(conn_ingest, tenant_id, sid, "projected", detail={"batch_seq": result.batch_seq})
    return {"batch_seq": result.batch_seq, "records": result.record_count, "files": result.file_count,
            "identity": counts}


def list_submissions(conn, tenant_id, actor: str, state: str | None = None) -> list[dict]:
    with tenant_tx(conn, tenant_id, actor):
        rows = conn.execute("SELECT s.*, r.ok AS validation_ok FROM intake.submission_state s LEFT JOIN"
                            " intake.validation_report r USING (tenant_id, submission_id) WHERE s.tenant_id = %s"
                            " AND (%s::text IS NULL OR s.state = %s) ORDER BY s.submitted_at DESC",
                            (tenant_id, state, state)).fetchall()
    return [dict(r) for r in rows]


def detail(conn, tenant_id, actor: str, sid: uuid.UUID) -> dict | None:
    with tenant_tx(conn, tenant_id, actor):
        s = conn.execute("SELECT * FROM intake.submission_state WHERE tenant_id = %s AND submission_id = %s",
                         (tenant_id, sid)).fetchone()
        if s is None:
            return None
        files = conn.execute("SELECT file_name, sha256, size_bytes, media_type FROM intake.submission_file"
                             " WHERE tenant_id = %s AND submission_id = %s ORDER BY file_name", (tenant_id, sid)
                             ).fetchall()
        events = conn.execute("SELECT state, actor, reason, detail, at FROM intake.submission_event"
                              " WHERE tenant_id = %s AND submission_id = %s ORDER BY at, event_id", (tenant_id, sid)
                              ).fetchall()
        rep = conn.execute("SELECT ok, report FROM intake.validation_report WHERE tenant_id = %s"
                           " AND submission_id = %s", (tenant_id, sid)).fetchone()
    return {**dict(s), "files": [dict(f) for f in files], "events": [dict(e) for e in events],
            "validation": dict(rep) if rep else None}


def work_once(conn_ingest, conn_projection, store: ObjectStore) -> dict:
    """The system worker's single pass: validate every UPLOADED submission and admit every AUTHORISED one, in
    every tenant. Runs with the ingest and projection roles (never the API role)."""
    done = {"validated": 0, "admitted": 0, "failed": 0}
    tenants = [r["tenant_id"] for r in conn_ingest.execute("SELECT tenant_id FROM directory.tenant").fetchall()]
    for tid in tenants:
        with tenant_tx(conn_ingest, tid, "system:intake"):
            todo = conn_ingest.execute("SELECT submission_id, state FROM intake.submission_state WHERE tenant_id = %s"
                                       " AND state IN ('uploaded', 'authorised') ORDER BY submitted_at",
                                       (tid,)).fetchall()
        for s in todo:
            try:
                if s["state"] == "uploaded":
                    validate(conn_ingest, store, tid, s["submission_id"])
                    done["validated"] += 1
                else:
                    admit(conn_ingest, conn_projection, store, tid, s["submission_id"])
                    done["admitted"] += 1
            except Exception:  # noqa: BLE001 - recorded on the submission (admission_failed); continue with others
                done["failed"] += 1
    return done
