"""Risk detectors v0 (ARCHITECTURE.md §16.1 step 1): deterministic rules over facts and governed metrics.

Only one detector exists in this engine version, for C4 (information-integrity exposure: "discrepancy between
figures reported upward and system records", §16.2). It runs ONLY on pairings of a reported figure with a
governed metric that the OWNER has approved in `config/figure_mappings.toml`: deciding that two figures measure
the same thing is a judgement, not something the system infers. With no approved pairing, it produces no signal.

A signal says the two sources differ. It never says which source is correct (that is the owner's conclusion,
§7.6, TRANSCRIPT_NOTES.md). The system value comes from a governed metric; the reported value is the record value
exactly as written, used only if it is a plain whole number.

Detectors for C1-C4 evidence (including this rule's output as signals) live in src/veloxa/signals; C5 is time
context only (D15-01).
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import psycopg
from psycopg import sql as psql

from .. import config, metrics
from ..db import tenant_tx
from ..temporal import ReadBasis, predicate
from .model import MEASURED, MISSING, NOT_APPLICABLE, MeasureDefinition, MeasureValue, Risk

DETECTOR = "reported_vs_system@1"
CURRENCY_UNITS = frozenset({"INR"})
C4_ABS = "C4.absolute_discrepancy"


@dataclass(frozen=True)
class FigureMapping:
    reported_figure: str      # the figure name exactly as written in a key-numbers table
    metric: str               # a governed metric name (config/metrics.toml)
    unit: str
    approved_by: str
    approved_on: date
    decision_record: str
    version: int = 1          # a changed pairing is a new version; earlier signals keep the version they used


def _raw_mappings(path: Path | None) -> list[dict]:
    path = path or (config.config_dir() / "figure_mappings.toml")
    return tomllib.loads(path.read_text(encoding="utf-8")).get("mapping", [])


def load_proposed(path: Path | None = None) -> list[dict]:
    """Entries marked status = "proposed": recorded for owner review, never active."""
    return [m for m in _raw_mappings(path) if m.get("status") == "proposed"]


def load_mappings(path: Path | None = None) -> list[FigureMapping]:
    """Active (owner-approved) mappings only. A "proposed" entry is inactive; any other entry must carry complete
    approval fields or the whole file is refused (fail closed)."""
    out = []
    for m in _raw_mappings(path):
        status = m.get("status", "approved")
        if status == "proposed":
            continue
        if status != "approved":
            raise ValueError(f"figure mapping {m!r}: status must be 'approved' or 'proposed'")
        for k in ("reported_figure", "metric", "unit", "approved_by", "decision_record"):
            if not str(m.get(k, "")).strip():
                raise ValueError(f"figure mapping {m!r}: {k} is required (owner approval is mandatory)")
        if not isinstance(m.get("approved_on"), date):
            raise ValueError(f"figure mapping {m!r}: approved_on must be a date")
        version = m.get("version", 1)
        if not isinstance(version, int) or version < 1:
            raise ValueError(f"figure mapping {m!r}: version must be an integer >= 1")
        out.append(FigureMapping(m["reported_figure"], m["metric"], m["unit"], m["approved_by"], m["approved_on"],
                                 m["decision_record"], version))
    return out


def _definition(unit: str) -> MeasureDefinition:
    return MeasureDefinition("C4", C4_ABS, 1, unit,
                             "Absolute difference between a figure as reported in a formal document and the "
                             "owner-paired governed metric at the same read basis, in the metric's unit")


def _risk_id(*parts: str) -> str:
    return "R-" + hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:10]


def reported_vs_system(conn: psycopg.Connection, basis: ReadBasis, mappings: list[FigureMapping],
                       ) -> tuple[list[Risk], list[str]]:
    """Returns (risks, notes for signals needing data)."""
    if not mappings:
        return [], ["C4 (information integrity): no owner-approved pairing of a reported figure with a governed "
                    "metric is recorded (config/figure_mappings.toml), so no discrepancy is checked"]
    defs = metrics.load_definitions()
    risks: list[Risk] = []
    notes: list[str] = []
    for m in mappings:
        d = defs.get(m.metric)
        if d is None or d.unit != m.unit:
            notes.append(f"C4: pairing for {m.reported_figure!r} names an unknown metric or a different unit; skipped")
            continue
        try:
            system = metrics.run(conn, d, basis)
        except metrics.MetricError as e:
            notes.append(f"C4: {d.ref} unavailable for {m.reported_figure!r} ({e.reason}); not checked")
            continue
        if len(system.rows) != 1:
            notes.append(f"C4: {d.ref} has no value at this knowledge point; {m.reported_figure!r} not checked")
            continue
        sys_row = system.rows[0]
        p = predicate(basis, "r")
        with tenant_tx(conn, basis.tenant_id, batch_seq=basis.batch_seq):
            reported = conn.execute(psql.SQL(
                'SELECT "r"."source_record_id", "r"."period", "r"."issued_on", "r"."value_text"'
                ' FROM projection.reported_figure "r" WHERE {} AND "r"."metric" = %(fig)s'
                ' AND "r"."data_class" <> ALL(%(protected)s) ORDER BY "r"."source_record_id"').format(p.clause),
                {**p.params, "fig": m.reported_figure, "protected": list(metrics.PROTECTED_CLASSES)}).fetchall()
        if not reported:
            notes.append(f"C4: no admitted document reports {m.reported_figure!r} at this knowledge point")
            continue
        for r in reported:
            text = r["value_text"].strip()
            plain = re.fullmatch(r"\d{1,3}(?:,\d{3})+|\d+", text)
            if not plain:
                notes.append(f"C4: {m.reported_figure!r} for {r['period']} is written as {text!r}, not a plain "
                             f"number in {m.unit}; not compared (uncertain unit or format)")
                continue
            value = int(text.replace(",", ""))
            if value == sys_row.value:
                continue  # the sources agree: no signal
            diff = abs(value - int(sys_row.value))
            evidence = tuple(sorted({r["source_record_id"], *sys_row.inputs}))
            c4 = MeasureValue(MEASURED, _definition(m.unit), diff, diff, evidence,
                              f"reported {text} vs {d.ref} = {sys_row.value} {m.unit}")
            c2 = (MeasureValue(MISSING, note="no record-backed amount at stake is assessed for a reporting "
                                             "discrepancy") if m.unit in CURRENCY_UNITS
                  else MeasureValue(NOT_APPLICABLE, note="the paired figures are not currency"))
            na = MeasureValue(NOT_APPLICABLE, note="a reporting discrepancy concerns no dated commitment or allocation")
            risks.append(Risk(
                _risk_id(DETECTOR, m.reported_figure, m.metric, str(r["period"]), r["source_record_id"]),
                DETECTOR, f"reported figure {m.reported_figure!r} ({r['period']})",
                f"The figure {m.reported_figure!r} as reported for {r['period']} ({text}) differs from the governed "
                f"metric {d.ref} at this knowledge point ({sys_row.value} {m.unit}). This is a signal for owner "
                "review; it does not say which source is correct.",
                {"C1": na, "C2": c2, "C3": na, "C4": c4, "C5": na},
                list(evidence),
                {"reported_period": r["period"],
                 "reported_issued_on": r["issued_on"].isoformat() if r["issued_on"] else None,
                 "system_value_as_at": "; ".join(system.qualifiers) or "the read basis",
                 "pairing_approved_on": m.approved_on.isoformat()},
                ["the reported figure and the system snapshot may refer to different dates"],
            ))
    return risks, notes
