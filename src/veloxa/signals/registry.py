"""Detector registry: run every criterion pipeline at one read basis, verify, and record (ARCHITECTURE.md §16.1).

Reproducibility: the output depends only on the admitted evidence at K, the detector versions, the configuration
(owner-approved mappings) and the assessment date. Signal ids are content hashes; a run is recorded once per
(basis, assessment date, configuration hash, output hash), so an identical rerun returns the original run.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

import psycopg

from .. import audit
from ..db import tenant_tx
from ..risk import detectors as approved
from ..temporal import Mode, ReadBasis
from . import c1_delivery, c2_financial, c3_continuity, c4_integrity
from .contract import Criterion, DetectorOutput, Signal, Withheld
from .evidence import verify


@dataclass(frozen=True)
class Detector:
    detector_id: str                 # id@version
    criterion: Criterion
    run: Callable[[psycopg.Connection, ReadBasis, dict], DetectorOutput]
    approved_basis: str              # the approved decisions this detector relies on
    pending: tuple[str, ...]         # owner decisions that limit it


REGISTRY: tuple[Detector, ...] = (
    Detector(c1_delivery.DETECTOR, Criterion.C1, c1_delivery.detect, "§16.2 C1; D15-25 (no financial consequence "
             "in C1); §16.4 (no probability without gates)", ("Q7", "D15-08")),
    Detector(c2_financial.DETECTOR, Criterion.C2, c2_financial.detect, "D15-04, D15-05", ("Q1.1-Q1.3", "D15-08")),
    Detector(c3_continuity.DETECTOR, Criterion.C3, c3_continuity.detect, "D15-02, D15-05, D15-25; §16.6",
             ("Q3", "Q9", "Q10")),
    Detector(c4_integrity.DETECTOR, Criterion.C4, c4_integrity.detect, "D15-03a-d", ("figure mapping approval",)),
)

MEASURABILITY = {
    "C1": "Commitments, delays and their status are observed from formal documents and meeting summaries. The C1 "
          "measure (required-rate ratio) is never quantified in this engine version: it needs open-scope and "
          "throughput records, and the Q7 throughput assumptions are not approved.",
    "C2": "The approved D15-04 calculation is implemented, but no catalogued source yields forward financial "
          "consequences yet, so C2 is reported as missing evidence (never zero).",
    "C3": "No catalogued source is a rota, qualification, ownership or service record yet; C3 measures are blocked "
          "pending Q3, Q9 and Q10. No guardrail exists.",
    "C4": "Same-label reported figures are checked for comparability. A reported figure is compared with a system "
          "metric only through an owner-approved mapping; without one it is unmapped. No materiality is assigned.",
    "C5": "Time context only (D15-01): commitment dates appear on C1 signals; there is no C5 detector.",
}


def assessment_date(basis: ReadBasis, tz: str) -> date:
    instant = basis.valid_at or basis.recorded_cutoff
    if instant is None:
        raise ValueError("the read basis has no assessment instant")
    return instant.astimezone(ZoneInfo(tz)).date()


def context_for(basis: ReadBasis, tz: str, mappings: list | None = None, proposed: list | None = None) -> dict:
    return {"assessment_date": assessment_date(basis, tz),
            "mappings": approved.load_mappings() if mappings is None else mappings,
            "proposed_mappings": approved.load_proposed() if proposed is None else proposed}


def _config_hash(ctx: dict) -> str:
    cfg = {"detectors": [d.detector_id for d in REGISTRY],
           "mappings": sorted(json.dumps(asdict(m), sort_keys=True, default=str) for m in ctx["mappings"]),
           "proposed": sorted(json.dumps(p, sort_keys=True, default=str) for p in ctx["proposed_mappings"])}
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()


@dataclass
class RunResult:
    basis: ReadBasis
    assessment_date: date
    signals: list[Signal]
    withheld: list[Withheld]
    config_sha256: str

    @property
    def output_sha256(self) -> str:
        body = {"signals": [s.body() | {"signal_id": s.signal_id} for s in self.signals],
                "withheld": [asdict(w) for w in self.withheld]}
        return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def run(conn: psycopg.Connection, basis: ReadBasis, ctx: dict) -> RunResult:
    """Run every registered detector. Fails closed: a detector that cites inadmissible evidence aborts the run."""
    signals: list[Signal] = []
    withheld: list[Withheld] = []
    if basis.batch_seq == 0:
        withheld.append(Withheld("registry@1", Criterion.C1, "nothing had been admitted at this knowledge point"))
        return RunResult(basis, ctx["assessment_date"], [], withheld, _config_hash(ctx))
    if basis.mode in (Mode.RECORDED_BY, Mode.VALID_AT):
        for d in REGISTRY:
            withheld.append(Withheld(d.detector_id, d.criterion,
                                     f"{basis.mode} needs recorded-time and valid-time columns that the detector "
                                     "projections do not carry; not run (never silently unfiltered)"))
        return RunResult(basis, ctx["assessment_date"], [], withheld, _config_hash(ctx))
    for d in REGISTRY:
        out = d.run(conn, basis, ctx)
        for s in out.signals:
            if s.detector != d.detector_id or s.criterion != d.criterion:
                raise ValueError(f"{d.detector_id} emitted a signal for {s.detector}/{s.criterion}")
        signals += verify(conn, basis, out.signals)
        withheld += out.withheld
    seen: dict[str, Signal] = {}
    for s in signals:
        seen.setdefault(s.signal_id, s)  # duplicate evidence yields one signal
    ordered = sorted(seen.values(), key=lambda s: (s.criterion, s.signal_type, s.subject, s.signal_id))
    return RunResult(basis, ctx["assessment_date"], ordered, withheld, _config_hash(ctx))


def record(conn: psycopg.Connection, result: RunResult, now: datetime) -> dict:
    """Record the run and its signals (or return the identical run already recorded)."""
    b = result.basis
    run_id = str(uuid.uuid4())
    out_hash = result.output_sha256
    withheld = json.dumps([asdict(w) for w in result.withheld], default=str)
    with tenant_tx(conn, b.tenant_id, batch_seq=b.batch_seq):
        inserted = conn.execute(
            "INSERT INTO runtime.detector_run (tenant_id, run_id, detected_at, batch_seq, mode, as_of,"
            " assessment_date, detectors, config_sha256, output_sha256, signal_count, withheld)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (b.tenant_id, run_id, now, b.batch_seq, str(b.mode), b.as_of, result.assessment_date,
             json.dumps([d.detector_id for d in REGISTRY]), result.config_sha256, out_hash,
             len(result.signals), withheld)).rowcount
        if inserted:
            for s in result.signals:
                conn.execute(
                    "INSERT INTO runtime.risk_signal (tenant_id, run_id, signal_id, detector, criterion, signal_type,"
                    " status, lineage_batch_seq, evidence, payload) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (b.tenant_id, run_id, s.signal_id, s.detector, s.criterion.value, s.signal_type.value,
                     s.status.value, s.lineage_batch_seq, list(s.record_ids),
                     json.dumps(s.body() | {"signal_id": s.signal_id}, default=str)))
            audit.append(conn, b.tenant_id, "detector.run", {
                "run_id": run_id, "batch_seq": b.batch_seq, "mode": str(b.mode), "signals": len(result.signals),
                "detectors": [d.detector_id for d in REGISTRY]})
            row = {"run_id": run_id, "detected_at": now}
        else:
            row = conn.execute(
                "SELECT run_id, detected_at FROM runtime.detector_run WHERE tenant_id = %s AND batch_seq = %s"
                " AND mode = %s AND coalesce(as_of, '') = %s AND assessment_date = %s AND config_sha256 = %s"
                " AND output_sha256 = %s",
                (b.tenant_id, b.batch_seq, str(b.mode), b.as_of or "", result.assessment_date,
                 result.config_sha256, out_hash)).fetchone()
    return {"run_id": str(row["run_id"]), "detected_at": row["detected_at"].isoformat()
            if hasattr(row["detected_at"], "isoformat") else str(row["detected_at"]),
            "detectors": [d.detector_id for d in REGISTRY], "output_sha256": out_hash,
            "config_sha256": result.config_sha256}


def fetch_signals(conn: psycopg.Connection, tenant_id: uuid.UUID, run_id: str) -> list[dict]:
    with tenant_tx(conn, tenant_id):
        rows = conn.execute("SELECT payload FROM runtime.risk_signal WHERE tenant_id = %s AND run_id = %s"
                            " ORDER BY signal_id", (tenant_id, run_id)).fetchall()
    return [r["payload"] for r in rows]
