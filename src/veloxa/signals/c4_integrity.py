"""C4 integrity of figures reported upward: discrepancy evidence capture (ARCHITECTURE.md §16.2; D15-03a-d).

Approved: materiality is per mapped metric and has NO values without an approved mapping (D15-03a); qualitative
factors stay unquantified (D15-03c); conservative rounding intervals, and no signal from a difference that rounding
could explain (D15-03d). No mapping of a reported figure to a system metric is approved yet.

So this detector:
- compares a reported figure with a governed system metric ONLY through an owner-approved, versioned mapping
  (`config/figure_mappings.toml`, via `risk.detectors`). A proposed or absent mapping is inactive, and the figure is
  reported as UNMAPPED_REQUIRES_OWNER_APPROVAL with its values preserved;
- compares figures that carry the SAME label in different formal documents, preserving both values, units, periods
  and provenance, and stating whether they are comparable at all (unit, period, parseable value). Identity by label
  is flagged as label-only, never as an established metric identity;
- never says which source is true, never assigns materiality, never emits a C4 score.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

import psycopg
from psycopg import sql as psql

from ..db import tenant_tx
from ..risk import detectors as approved
from ..temporal import Mode, ReadBasis, predicate
from . import timebasis
from .contract import (
    Authority,
    Criterion,
    DetectorOutput,
    EvidenceRef,
    Flag,
    Signal,
    SignalType,
    Status,
    Value,
    ValueKind,
    Withheld,
    timed_ref,
)

DETECTOR = "c4_integrity@2"  # @2: per-fact recorded/valid time and source versions
_SCALES = {"cr": Decimal(10) ** 7, "crore": Decimal(10) ** 7, "l": Decimal(10) ** 5, "lakh": Decimal(10) ** 5,
           "lakhs": Decimal(10) ** 5}
_VALUE = re.compile(r"^(?P<cur>Rs\.?\s*)?(?P<num>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(?P<suffix>[A-Za-z]+)?$")


@dataclass(frozen=True)
class Reported:
    record_id: str
    label: str
    value_text: str
    period: str | None
    issued_on: str | None
    fact_time: timebasis.FactTime | None = None


@dataclass(frozen=True)
class Parsed:
    low: Decimal      # conservative rounding interval implied by the digits as written (D15-03d)
    high: Decimal
    unit: str


def parse(text: str) -> Parsed | None:
    """A reported value as an interval in a declared unit, or None when its unit or format is uncertain."""
    m = _VALUE.match(text.strip())
    if not m:
        return None
    num, suffix, cur = m["num"].replace(",", ""), (m["suffix"] or "").lower(), bool(m["cur"])
    value = Decimal(num)
    decimals = len(num.split(".")[1]) if "." in num else 0
    half = Decimal(5) * Decimal(10) ** (-(decimals + 1))
    low, high = value - half, value + half
    if cur:
        scale = _SCALES.get(suffix, Decimal(1) if not suffix else None)
        if scale is None:
            return None
        return Parsed(low * scale, high * scale, "INR")
    if suffix in ("month", "months"):
        return Parsed(low, high, "months")
    if suffix:
        return None
    return Parsed(low, high, "count (unit not stated)")


def compare_reported(rows: list[Reported]) -> DetectorOutput:
    """Pure: same-label figures across different formal documents."""
    out = DetectorOutput()
    by_label: dict[str, list[Reported]] = {}
    for r in rows:
        by_label.setdefault(r.label, []).append(r)
    for label in sorted(by_label):
        group = sorted(by_label[label], key=lambda r: r.record_id)
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if a.record_id == b.record_id:
                    continue
                pa, pb = parse(a.value_text), parse(b.value_text)
                ev = (timed_ref(a.record_id, f"key numbers: {label}", a.value_text, a.fact_time),
                      timed_ref(b.record_id, f"key numbers: {label}", b.value_text, b.fact_time))
                observed = (f"{a.record_id} reports {label!r} = {a.value_text!r} for period {a.period!r}",
                            f"{b.record_id} reports {label!r} = {b.value_text!r} for period {b.period!r}")
                flags = {Flag.METRIC_IDENTITY_LABEL_ONLY, Flag.MATERIALITY_NOT_ASSIGNED, Flag.REPORTED_NOT_VERIFIED}
                reason = None
                if pa is None or pb is None:
                    flags.add(Flag.VALUE_FORMAT_UNPARSED)
                    reason = "a value is not written in a parseable number and unit"
                elif pa.unit != pb.unit:
                    flags.add(Flag.UNIT_MISMATCH)
                    reason = f"units differ ({pa.unit} vs {pb.unit})"
                elif (a.period or "") != (b.period or "") or not a.period:
                    flags.add(Flag.PERIOD_MISMATCH)
                    reason = "the figures are for different (or unstated) periods, so a difference is expected"
                key = f"c4|label|{label}|{a.record_id}|{b.record_id}"
                if reason:
                    out.signals.append(Signal(
                        DETECTOR, Criterion.C4, SignalType.FIGURE_COMPARISON, Status.UNQUANTIFIED, label,
                        "C4.reported_vs_reported@1", Value(), frozenset(flags), Authority.FORMAL_REPORTED, ev,
                        observed=observed, missing=(f"not comparable: {reason}",),
                        decisions_pending=("D15-03a",), key=key))
                    continue
                if pa.high >= pb.low and pb.high >= pa.low:
                    out.withheld.append(Withheld(DETECTOR, Criterion.C4,
                                                 f"{label!r}: the figures agree within their rounding intervals "
                                                 "(D15-03d: no signal)", (a.record_id, b.record_id)))
                    continue
                out.signals.append(Signal(
                    DETECTOR, Criterion.C4, SignalType.FIGURE_DISCREPANCY, Status.PROVISIONAL, label,
                    "C4.reported_vs_reported@1", Value(ValueKind.CATEGORY, category="values_differ_beyond_rounding"),
                    frozenset(flags), Authority.FORMAL_REPORTED, ev, observed=observed,
                    assumptions=("the two figures are taken to measure the same thing because they carry the same "
                                 "label for the same period; the metric definition is not established",),
                    missing=("no materiality is assigned (D15-03a); this does not say which source is correct",),
                    decisions_pending=("D15-03a",),
                    calculation=(("interval_a", f"[{pa.low}, {pa.high}] {pa.unit}"),
                                 ("interval_b", f"[{pb.low}, {pb.high}] {pb.unit}")), key=key))
    return out


def load_reported(conn: psycopg.Connection, basis: ReadBasis, tz: str = "UTC") -> list[Reported]:
    """Reported figures visible at K (latest version at K), each with its FactTime: R from the document's own
    date, V from its stated period (unknown when the period cannot be read)."""
    p = predicate(basis, "r", k_only=True)
    with tenant_tx(conn, basis.tenant_id, batch_seq=basis.batch_seq):
        rows = conn.execute(psql.SQL(
            'SELECT DISTINCT ON ("r"."source_record_id", "r"."metric") "r"."source_record_id", "r"."metric",'
            ' "r"."value_text", "r"."period", "r"."issued_on", "s"."extracted_at"'
            ' FROM projection.reported_figure "r" JOIN ledger.source_record "s" ON "s"."tenant_id" = "r"."tenant_id"'
            ' AND "s"."record_id" = "r"."source_record_id" AND "s"."version" = "r"."source_version" WHERE {}'
            ' AND "r"."data_class" <> ALL(%(protected)s)'
            ' ORDER BY "r"."source_record_id", "r"."metric", "r"."batch_seq" DESC').format(p.clause),
            {**p.params, "protected": ["sensitive_personal", "payroll_aggregate"]}).fetchall()
    return [Reported(r["source_record_id"], r["metric"], r["value_text"], r["period"],
                     r["issued_on"].isoformat() if r["issued_on"] else None,
                     timebasis.figure_time(r["issued_on"], r["period"], r["extracted_at"], tz)) for r in rows]


def unmapped_signals(rows: list[Reported], active: list[approved.FigureMapping],
                     proposed: list[dict]) -> list[Signal]:
    mapped = {m.reported_figure for m in active}
    pending = {p.get("reported_figure") for p in proposed}
    out = []
    by_label: dict[str, list[Reported]] = {}
    for r in rows:
        by_label.setdefault(r.label, []).append(r)
    for label in sorted(set(by_label) - mapped):
        group = sorted(by_label[label], key=lambda r: r.record_id)
        flags = {Flag.METRIC_IDENTITY_UNDEFINED, Flag.MATERIALITY_NOT_ASSIGNED, Flag.REPORTED_NOT_VERIFIED}
        if label in pending:
            flags.add(Flag.MAPPING_PROPOSED_NOT_APPROVED)
        out.append(Signal(
            DETECTOR, Criterion.C4, SignalType.FIGURE_UNMAPPED, Status.UNMAPPED, label, "C4.reported_vs_system@1",
            Value(), frozenset(flags), Authority.FORMAL_REPORTED,
            tuple(timed_ref(r.record_id, f"key numbers: {label}", r.value_text, r.fact_time) for r in group),
            observed=tuple(f"{r.record_id} reports {label!r} = {r.value_text!r} for period {r.period!r}"
                           for r in group),
            missing=("no owner-approved mapping pairs this reported figure with a system metric; it is not compared"
                     + (" (a proposed mapping exists but is not approved)" if label in pending else ""),),
            decisions_pending=("D15-03a", "figure mapping approval"), key=f"c4|unmapped|{label}"))
    return out


def approved_signals(conn: psycopg.Connection, basis: ReadBasis, active: list[approved.FigureMapping],
                     ) -> tuple[list[Signal], list[Withheld]]:
    """Discrepancies through owner-approved mappings (the existing reported_vs_system rule), as signals."""
    risks, notes = approved.reported_vs_system(conn, basis, active)
    by_fig = {m.reported_figure: m for m in active}
    out = []
    for r in risks:
        c4 = r.measures["C4"]
        fig = next((f for f in by_fig if f"{f!r}" in r.business_object), None)
        m = by_fig.get(fig)
        out.append(Signal(
            DETECTOR, Criterion.C4, SignalType.FIGURE_DISCREPANCY, Status.ACTIVE, r.business_object,
            c4.definition.ref, Value(ValueKind.EXACT, c4.low, c4.high, unit=c4.definition.unit),
            frozenset({Flag.MATERIALITY_NOT_ASSIGNED, Flag.REPORTED_NOT_VERIFIED}), Authority.FORMAL_REPORTED,
            tuple(EvidenceRef(e) for e in r.evidence), observed=(c4.note,), missing=tuple(r.unknowns),
            calculation=(("mapping", f"{m.reported_figure} -> {m.metric} v{m.version} approved by {m.approved_by} "
                                     f"on {m.approved_on.isoformat()} ({m.decision_record})" if m else "approved"),),
            decisions_pending=("D15-03a",), key=f"c4|approved|{r.risk_id}"))
    withheld = [Withheld(DETECTOR, Criterion.C4, n) for n in notes if "no owner-approved pairing" not in n]
    return out, withheld


def detect(conn: psycopg.Connection, basis: ReadBasis, context: dict) -> DetectorOutput:
    active, proposed = context["mappings"], context.get("proposed_mappings", [])
    tz = context["time_zone"]
    rows, withheld = [], []
    for r in load_reported(conn, basis, tz):
        reason = timebasis.excluded(r.fact_time, basis, tz)
        if reason:
            withheld.append(Withheld(DETECTOR, Criterion.C4, f"{r.label!r} is not used at this read basis: {reason}",
                                     (r.record_id,)))
        else:
            rows.append(r)
    out = compare_reported(rows)
    out.signals += unmapped_signals(rows, active, proposed)
    out.withheld += withheld
    if basis.mode in (Mode.RECORDED_BY, Mode.VALID_AT) and active:
        out.withheld.append(Withheld(DETECTOR, Criterion.C4, f"approved reported-vs-system comparisons are not "
                                     f"run in {basis.mode}: governed system metrics carry no recorded or valid "
                                     "time yet (never silently unfiltered)"))
    else:
        sigs, notes = approved_signals(conn, basis, active)
        out.signals += sigs
        out.withheld += notes
    out.withheld.append(Withheld(DETECTOR, Criterion.C4, "figures spoken in meeting summaries are not extracted as "
                                 "reported figures (prose with uncertain units); they are not compared"))
    if not active:
        out.withheld.append(Withheld(DETECTOR, Criterion.C4, "no owner-approved pairing of a reported figure with a "
                                     "governed metric is recorded (config/figure_mappings.toml), so no "
                                     "reported-vs-system discrepancy is checked"))
    return out
