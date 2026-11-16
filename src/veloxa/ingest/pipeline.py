"""Admission of one release pack as one sealed batch (ARCHITECTURE.md §7.3, §8.2).

Order of operations:
1. Decide admissibility from the release schedule and the clock, before any file is opened.
2. Read each file once, hash those bytes, and parse the same bytes in memory (fail closed on anything unknown).
3. In ONE transaction, under a per-tenant lock, RE-CHECK the admission state (a concurrent run may have admitted
   the pack meanwhile), then insert the batch (unsealed), the raw manifest and the source records, seal the
   batch, and append an audit event. Either the whole pack is admitted and sealed, or nothing is.

The four times stay distinct (ASSUMPTIONS.md A-025):
  release_at   - the pack's scheduled release (the batch's knowledge time)
  extracted_at - when the connector actually read each file's bytes
  admitted_at  - when the admission transaction started
  sealed_at    - when the batch was sealed
and release_at <= extracted_at <= admitted_at <= sealed_at is enforced by the database (migration 0002).

Logs and audit details contain counts and hashes only, never record values.
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from .. import audit
from ..catalogue import Rule, load_catalogue
from ..db import tenant_tx
from ..releases import AdmissionRefused, Pack, Schedule, check_admissible
from .contracts import validate_rows
from .manifest import build_manifest, manifest_sha256
from .parsers import parse


@dataclass
class AdmissionResult:
    pack_id: str
    batch_seq: int
    status: str  # "admitted" | "already_admitted"
    file_count: int
    record_count: int
    by_class: dict = field(default_factory=dict)
    by_kind: dict = field(default_factory=dict)


def _batch_rows(conn: psycopg.Connection, tenant_id: uuid.UUID) -> dict[int, dict]:
    rows = conn.execute(
        "SELECT batch_seq, pack_id, manifest_sha256, sealed, file_count, record_count"
        " FROM ledger.admission_batch WHERE tenant_id = %s",
        (tenant_id,),
    ).fetchall()
    return {r["batch_seq"]: r for r in rows}


def _existing_outcome(pack: Pack, prior: dict | None, digest: str) -> AdmissionResult | None:
    """Idempotency: the same sealed content is 'already_admitted'; different content is refused."""
    if prior is None:
        return None
    if prior["pack_id"] == pack.id and prior["manifest_sha256"] == digest and prior["sealed"]:
        return AdmissionResult(pack.id, pack.batch_seq, "already_admitted", prior["file_count"], prior["record_count"])
    raise AdmissionRefused(
        "manifest_changed",
        f"pack {pack.id} was already admitted with different content; corrections require new record versions",
    )


def admit_pack(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    schedule: Schedule,
    pack_id: str,
    data_root: Path,
    now: datetime,
    rules: list[Rule] | None = None,
) -> AdmissionResult:
    with tenant_tx(conn, tenant_id):
        existing = _batch_rows(conn, tenant_id)
    sealed = {seq for seq, r in existing.items() if r["sealed"]}

    # 1. Admission decision first: an unreleased pack is refused before its folder is touched.
    pack = check_admissible(schedule, pack_id, now, sealed)

    # 2. Read, hash and parse the same bytes, in memory.
    rules = rules if rules is not None else load_catalogue()
    entries = build_manifest(data_root / pack.folder, rules)
    digest = manifest_sha256(entries)

    early = _existing_outcome(pack, existing.get(pack.batch_seq), digest)  # fast path, re-checked under the lock
    if early is not None:
        return early

    parsed = []  # (entry, data class, envelope)
    for entry in entries:
        envelopes = parse(entry.data, entry.suffix, entry.rule.records)
        if entry.rule.contract is not None:  # schema and semantic checks; refuses the whole pack on failure
            validate_rows(entry.relpath, entry.rule.contract, [(e.locator, e.payload) for e in envelopes])
        for env in envelopes:
            parsed.append((entry, entry.rule.class_for_row(env.payload), env))

    # 3. One transaction: lock -> re-check -> batch -> raw manifest -> records -> seal -> audit.
    with tenant_tx(conn, tenant_id, principal_id="ingest:file-drop"):
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('ledger.admission:' || %s))", (str(tenant_id),))
        current = _batch_rows(conn, tenant_id)
        settled = _existing_outcome(pack, current.get(pack.batch_seq), digest)
        if settled is not None:
            return settled  # a concurrent run admitted this pack while we were parsing
        check_admissible(schedule, pack.id, now, {s for s, r in current.items() if r["sealed"]})

        admitted_at = datetime.now(UTC)
        conn.execute(
            "INSERT INTO ledger.admission_batch (tenant_id, batch_seq, pack_id, release_at, admitted_at,"
            " manifest_sha256, file_count, record_count) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (tenant_id, pack.batch_seq, pack.id, pack.release_at, admitted_at, digest, len(entries), len(parsed)),
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO raw_index.raw_object (tenant_id, batch_seq, source_path, content_sha256, size_bytes,"
                " kind, system, data_class, record_mode) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (tenant_id, pack.batch_seq, e.relpath, e.sha256, e.size, e.rule.kind, e.rule.system,
                     e.rule.data_class, e.rule.records)
                    for e in entries
                ],
            )
            cur.executemany(
                "INSERT INTO ledger.source_record (tenant_id, record_id, version, batch_seq, source_path,"
                " raw_object, locator, kind, system, parser_version, data_class, extracted_at, payload)"
                " VALUES (%s, %s, 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (tenant_id, f"{e.rule.system}:{e.relpath}#{env.locator_key()}", pack.batch_seq, e.relpath,
                     e.sha256, json.dumps(env.locator), e.rule.kind, e.rule.system, env.parser_version, cls,
                     e.read_at, json.dumps(env.payload))
                    for e, cls, env in parsed
                ],
            )
        conn.execute(
            "UPDATE ledger.admission_batch SET sealed = true, sealed_at = %s WHERE tenant_id = %s AND batch_seq = %s",
            (datetime.now(UTC), tenant_id, pack.batch_seq),
        )
        by_class = dict(Counter(cls for _, cls, _ in parsed))
        by_kind = dict(Counter(e.rule.kind for e, _, _ in parsed))
        audit.append(conn, tenant_id, "admission.sealed", {
            "pack": pack.id, "batch_seq": pack.batch_seq, "manifest_sha256": digest,
            "files": len(entries), "records": len(parsed), "records_by_class": by_class,
        })
    return AdmissionResult(pack.id, pack.batch_seq, "admitted", len(entries), len(parsed), by_class, by_kind)


@dataclass
class AdmissionFailure:
    pack_id: str
    error: Exception

    @property
    def reason(self) -> str:
        return getattr(self.error, "reason", type(self.error).__name__)


def admit_released(
    conn: psycopg.Connection, tenant_id: uuid.UUID, schedule: Schedule, data_root: Path, now: datetime
) -> tuple[list[AdmissionResult], AdmissionFailure | None]:
    """Admit every released pack in order. Stops at the first failure (later packs depend on it).

    A failed admission commits nothing: earlier sealed batches stay exactly as they were, and the failed pack
    is simply not admitted (owner decision, 16 Nov 2026: keep serving the latest sealed batch).
    """
    results: list[AdmissionResult] = []
    for pack in schedule.released(now):
        try:
            results.append(admit_pack(conn, tenant_id, schedule, pack.id, data_root, now))
        except Exception as exc:  # reported to the caller; nothing partial is ever committed
            return results, AdmissionFailure(pack.id, exc)
    return results, None


def record_failure(conn: psycopg.Connection, tenant_id: uuid.UUID, failure: AdmissionFailure) -> None:
    """Append an audit event for a failed admission: pack and reason code only, never file content."""
    with tenant_tx(conn, tenant_id, principal_id="ingest:file-drop"):
        audit.append(conn, tenant_id, "admission.failed", {"pack": failure.pack_id, "reason": failure.reason})
