"""C2 financial exposure under the approved D15-04 rules (ARCHITECTURE.md §16.2; DECISIONS.md D-15).

D15-04 (APPROVED): rolling 12-month horizon H; incremental net impact in INR; recurring portions inside H only;
exposure beyond H reported separately; supported offsets only (uncertain offsets widen the lower bound); the
maximum of mutually exclusive outcomes; no double counting; a missing upper bound means unquantified; system
records and binding documents only; no extrapolation. D15-05: missing is never zero.

The output is the supported INR amount or interval with its calculation provenance. It is NOT normalised to 0-1:
the C2 anchors are unapproved (Q1 selects a provisional company-scale reference only, and the board figures are
never used to compute exposure).

Inputs are typed `FinancialItem`s. Each must rest on a system record or a binding document; anything else is
rejected at construction. In this engine version no admitted source yields forward financial obligations (there
are no contract, invoice-due or penalty records), so the detector reports C2 as missing evidence; the calculator
below is exercised by synthetic tests and becomes live when such a source is catalogued and projected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

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
)

DETECTOR = "c2_financial@1"
MEASURE = "C2.net_exposure_12m@1"
UNIT = "INR"
HORIZON_MONTHS = 12
ALLOWED_AUTHORITY = (Authority.SYSTEM_RECORD, Authority.BINDING_DOCUMENT)


class Recurrence(StrEnum):
    ONE_TIME = "one_time"
    RECURRING = "recurring"


class OffsetSupport(StrEnum):
    CONFIRMED = "confirmed"      # supported, amount established: reduces both bounds
    UNCERTAIN = "uncertain"      # supported but not certain to be realised: widens the lower bound only
    UNSUPPORTED = "unsupported"  # no supporting record: ignored (never reduces exposure)


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    year, month = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(year, month, min(day, d.day))
        except ValueError:
            continue
    raise ValueError(d)


def horizon(assessment: date) -> tuple[date, date]:
    """[start, end): the rolling 12 months from the assessment date."""
    return assessment, add_months(assessment, HORIZON_MONTHS)


@dataclass(frozen=True)
class Offset:
    offset_id: str
    low: int
    high: int
    support: OffsetSupport
    evidence: tuple[EvidenceRef, ...] = ()

    def __post_init__(self):
        if not (0 <= self.low <= self.high):
            raise ValueError(f"offset {self.offset_id}: need 0 <= low <= high")
        if self.support != OffsetSupport.UNSUPPORTED and not self.evidence:
            raise ValueError(f"offset {self.offset_id}: a supported offset must cite its record")


@dataclass(frozen=True)
class FinancialItem:
    """One incremental financial consequence, in INR, as a system record or binding document states it.

    ONE_TIME: `amount_low/high` once, on `due_on` (None = timing unknown).
    RECURRING: `amount_low/high` per occurrence, every `every_months`, from `first_due` to `last_due` inclusive
    (`last_due` None = the term end is not stated: counting occurrences to the horizon would extrapolate, so the
    item has no upper bound and the result is unquantified).
    `amount_high` None means the record establishes no upper bound.
    """
    item_id: str
    consequence_key: str                     # the same underlying consequence has the same key (no double count)
    authority: Authority
    evidence: tuple[EvidenceRef, ...]
    recurrence: Recurrence
    amount_low: int
    amount_high: int | None
    due_on: date | None = None
    first_due: date | None = None
    last_due: date | None = None
    every_months: int = 1
    outcome_group: str | None = None         # items in one group are mutually exclusive outcomes
    offsets: tuple[Offset, ...] = field(default_factory=tuple)

    def __post_init__(self):
        if self.authority not in ALLOWED_AUTHORITY:
            raise ValueError(f"{self.item_id}: C2 uses system records and binding documents only "
                             f"(D15-04); {self.authority} is rejected")
        if not self.evidence:
            raise ValueError(f"{self.item_id}: a financial item must cite its record")
        if self.amount_low < 0 or (self.amount_high is not None and self.amount_high < self.amount_low):
            raise ValueError(f"{self.item_id}: need 0 <= low <= high")
        if self.recurrence == Recurrence.RECURRING:
            if self.first_due is None or self.every_months < 1:
                raise ValueError(f"{self.item_id}: a recurring item needs first_due and every_months >= 1")
            if self.last_due is not None and self.last_due < self.first_due:
                raise ValueError(f"{self.item_id}: last_due precedes first_due")
        elif self.first_due or self.last_due:
            raise ValueError(f"{self.item_id}: a one-time item uses due_on only")


@dataclass
class Exposure:
    inside_low: int | None
    inside_high: int | None
    beyond_low: int | None
    beyond_high: int | None
    one_time: tuple[int, int | None]
    recurring: tuple[int, int | None]
    gross: tuple[int, int | None]
    offsets_applied: list[tuple[str, str, int, int]]
    flags: set[Flag]
    missing: list[str]
    steps: list[tuple[str, str]]


def _occurrences(item: FinancialItem, start: date, end: date) -> tuple[int, int]:
    """(occurrences inside [start, end), occurrences on or after end), for an item with a stated last_due."""
    inside, beyond, n = 0, 0, 0
    while True:
        d = add_months(item.first_due, n * item.every_months)
        if d > item.last_due:
            break
        if d >= end:
            beyond += 1
        elif d >= start:
            inside += 1
        n += 1
        if n > 100_000:
            raise ValueError("runaway recurrence")
    return inside, beyond


def _item_amounts(item: FinancialItem, start: date, end: date, flags: set[Flag], missing: list[str],
                  steps: list) -> tuple[int, int | None, int, int | None] | None:
    """(inside low, inside high, beyond low, beyond high) gross; None when it cannot be placed."""
    hi = item.amount_high
    if item.recurrence == Recurrence.ONE_TIME:
        if item.due_on is None:
            flags.add(Flag.TIMING_UNKNOWN)
            missing.append(f"{item.item_id}: the record states no date, so it cannot be placed inside or beyond "
                           "the 12-month horizon")
            return None
        if start <= item.due_on < end:
            steps.append((item.item_id, f"one-time on {item.due_on.isoformat()}, inside H: [{item.amount_low}, "
                                        f"{hi if hi is not None else 'no upper bound'}]"))
            return item.amount_low, hi, 0, 0
        steps.append((item.item_id, f"one-time on {item.due_on.isoformat()}, outside H: not counted inside"))
        if item.due_on >= end:
            flags.add(Flag.HORIZON_PARTIAL)
            return 0, 0, item.amount_low, hi
        return 0, 0, 0, 0  # already past: not a forward exposure
    if item.last_due is None:
        flags |= {Flag.UPPER_BOUND_MISSING, Flag.HORIZON_PARTIAL}
        missing.append(f"{item.item_id}: the term end is not stated; counting occurrences to the horizon would "
                       "extrapolate, so no upper bound is established")
        steps.append((item.item_id, f"recurring every {item.every_months} month(s) from "
                                    f"{item.first_due.isoformat()} with no stated end: unbounded"))
        return 0, None, 0, None
    inside, beyond = _occurrences(item, start, end)
    if beyond:
        flags.add(Flag.HORIZON_PARTIAL)
    steps.append((item.item_id, f"recurring every {item.every_months} month(s) from {item.first_due.isoformat()}"
                                f" to {item.last_due.isoformat()}: {inside} occurrence(s) inside H, {beyond} beyond,"
                                f" x [{item.amount_low}, {hi if hi is not None else 'no upper bound'}]"))
    return (inside * item.amount_low, None if hi is None else inside * hi,
            beyond * item.amount_low, None if hi is None else beyond * hi)


def _add(a: int | None, b: int | None) -> int | None:
    return None if a is None or b is None else a + b


def compute(items: list[FinancialItem], assessment: date) -> Exposure:
    start, end = horizon(assessment)
    flags: set[Flag] = set()
    missing: list[str] = []
    steps: list[tuple[str, str]] = [("horizon", f"rolling 12 months [{start.isoformat()}, {end.isoformat()})")]
    offsets_applied: list[tuple[str, str, int, int]] = []
    # 1. No double counting: one consequence, one contribution. Differing duplicates widen to their hull.
    by_key: dict[str, list[FinancialItem]] = {}
    for it in sorted(items, key=lambda i: i.item_id):
        by_key.setdefault(it.consequence_key, []).append(it)
    unique: list[FinancialItem] = []
    for key, dupes in sorted(by_key.items()):
        if len(dupes) > 1:
            flags.add(Flag.DUPLICATE_CONSEQUENCE)
            shapes = {(d.recurrence, d.due_on, d.first_due, d.last_due, d.every_months) for d in dupes}
            amounts = {(d.amount_low, d.amount_high) for d in dupes}
            if len(shapes) > 1:
                raise ValueError(f"consequence {key!r}: duplicate records disagree on timing; resolve the "
                                 "duplicate before computing (no silent choice)")
            first = dupes[0]
            if len(amounts) > 1:
                flags.add(Flag.DUPLICATE_AMOUNTS_DIFFER)
                low = min(d.amount_low for d in dupes)
                high = (None if any(d.amount_high is None for d in dupes)
                        else max(d.amount_high for d in dupes))
                first = FinancialItem(**{**first.__dict__, "amount_low": low, "amount_high": high,
                                         "evidence": tuple(e for d in dupes for e in d.evidence),
                                         "offsets": tuple(o for d in dupes for o in d.offsets)})
            steps.append((key, f"{len(dupes)} records describe one consequence; counted once"
                               f"{' (hull of differing amounts)' if len(amounts) > 1 else ''}"))
            unique.append(first)
        else:
            unique.append(dupes[0])
    # 2. Per item: gross inside/beyond H, then offsets (inside H only).
    contributions: dict[str, tuple[int, int | None, int, int | None, Recurrence]] = {}
    for it in unique:
        amt = _item_amounts(it, start, end, flags, missing, steps)
        if amt is None:
            contributions[it.item_id] = (0, None, 0, None, it.recurrence)
            continue
        il, ih, bl, bh = amt
        nl, nh = il, ih
        for o in sorted(it.offsets, key=lambda o: o.offset_id):
            if o.support == OffsetSupport.UNSUPPORTED:
                flags.add(Flag.OFFSET_UNSUPPORTED_IGNORED)
                steps.append((it.item_id, f"offset {o.offset_id} unsupported: ignored"))
                continue
            if o.support == OffsetSupport.CONFIRMED:
                nl, nh = nl - o.high, None if nh is None else nh - o.low
            else:
                flags.add(Flag.OFFSET_UNCERTAIN)
                nl = nl - o.high  # widens the lower bound only
            offsets_applied.append((it.item_id, o.offset_id, o.low, o.high))
            steps.append((it.item_id, f"offset {o.offset_id} ({o.support}) [{o.low}, {o.high}]"))
        if nl < 0 or (nh is not None and nh < 0):
            flags.add(Flag.OFFSET_EXCEEDS_GROSS)
            nl, nh = max(nl, 0), None if nh is None else max(nh, 0)
        contributions[it.item_id] = (nl, nh, bl, bh, it.recurrence)
    # 3. Mutually exclusive outcomes: the maximum, never the sum. Independent consequences add.
    groups: dict[str, list[str]] = {}
    for it in unique:
        groups.setdefault(it.outcome_group or f"independent:{it.item_id}", []).append(it.item_id)
    in_l, in_h, be_l, be_h = 0, 0, 0, 0
    one: list = [0, 0]
    rec: list = [0, 0]
    gross: list = [0, 0]
    for g, ids in sorted(groups.items()):
        cs = [contributions[i] for i in ids]
        if len(ids) > 1:
            flags.add(Flag.MUTUALLY_EXCLUSIVE)
            pick_l = max(c[0] for c in cs)
            pick_h = None if any(c[1] is None for c in cs) else max(c[1] for c in cs)
            pick_bl = max(c[2] for c in cs)
            pick_bh = None if any(c[3] is None for c in cs) else max(c[3] for c in cs)
            steps.append((g, f"{len(ids)} mutually exclusive outcomes: maximum [{pick_l}, "
                             f"{pick_h if pick_h is not None else 'no upper bound'}], not the sum"))
            kinds = {c[4] for c in cs}
            kind = kinds.pop() if len(kinds) == 1 else None
        else:
            pick_l, pick_h, pick_bl, pick_bh, kind = cs[0]
        in_l, in_h = in_l + pick_l, _add(in_h, pick_h)
        be_l, be_h = be_l + pick_bl, _add(be_h, pick_bh)
        bucket = one if kind == Recurrence.ONE_TIME else rec if kind == Recurrence.RECURRING else None
        if bucket is not None:
            bucket[0], bucket[1] = bucket[0] + pick_l, _add(bucket[1], pick_h)
        gross[0], gross[1] = gross[0] + pick_l, _add(gross[1], pick_h)
    if in_h is None:
        flags.add(Flag.UPPER_BOUND_MISSING)
        missing.append("an upper bound inside the 12-month horizon cannot be established from the records")
    return Exposure(in_l, in_h, be_l, be_h, (one[0], one[1]), (rec[0], rec[1]), (gross[0], gross[1]),
                    offsets_applied, flags, missing, steps)


def exposure_signal(subject: str, items: list[FinancialItem], assessment: date) -> Signal:
    """One C2 signal for one business object."""
    e = compute(items, assessment)
    start, end = horizon(assessment)
    evidence = tuple(sorted({ev for it in items for ev in it.evidence} |
                            {ev for it in items for o in it.offsets for ev in o.evidence},
                            key=lambda ev: (ev.record_id, ev.locator)))
    authority = (Authority.BINDING_DOCUMENT if any(i.authority == Authority.BINDING_DOCUMENT for i in items)
                 else Authority.SYSTEM_RECORD)
    calc = list(e.steps) + [
        ("one_time_inside_H", f"[{e.one_time[0]}, {e.one_time[1]}]"),
        ("recurring_inside_H", f"[{e.recurring[0]}, {e.recurring[1]}]"),
        ("beyond_H_reported_separately",
         f"[{e.beyond_low}, {e.beyond_high if e.beyond_high is not None else 'unbounded'}]"),
    ]
    if e.inside_high is None:
        return Signal(DETECTOR, Criterion.C2, SignalType.FINANCIAL_EXPOSURE, Status.UNQUANTIFIED, subject, MEASURE,
                      Value(), frozenset(e.flags), authority, evidence, missing=tuple(e.missing),
                      window_start=start, window_end=end, calculation=tuple(calc), key=f"c2|{subject}")
    kind = ValueKind.EXACT if e.inside_low == e.inside_high else ValueKind.INTERVAL
    return Signal(DETECTOR, Criterion.C2, SignalType.FINANCIAL_EXPOSURE, Status.ACTIVE, subject, MEASURE,
                  Value(kind, float(e.inside_low), float(e.inside_high), unit=UNIT), frozenset(e.flags), authority,
                  evidence, observed=tuple(f"{a}: {b}" for a, b in e.steps if a != "horizon"),
                  missing=tuple(e.missing), window_start=start, window_end=end, calculation=tuple(calc),
                  key=f"c2|{subject}")


def detect(conn, basis, context: dict) -> DetectorOutput:
    """No admitted source yields forward financial obligations in this engine version: report that explicitly."""
    out = DetectorOutput()
    out.signals.append(Signal(
        DETECTOR, Criterion.C2, SignalType.FINANCIAL_EXPOSURE, Status.MISSING, "all business objects", MEASURE,
        Value(), frozenset(), Authority.NONE,
        missing=("no admitted system record or binding document states a forward financial consequence "
                 "(contracts, invoices due, penalties, credits or refunds); C2 is missing, never zero",),
        window_start=horizon(context["assessment_date"])[0], window_end=horizon(context["assessment_date"])[1],
        key="c2|no-source"))
    out.withheld += [
        Withheld(DETECTOR, Criterion.C2, "the general-ledger extract records past spend for selected cost centres "
                 "only; it is not a forward exposure record, is not a complete company-wide record, and recurring "
                 "past spend is never extrapolated"),
        Withheld(DETECTOR, Criterion.C2, "board-reported figures are a provisional company-scale reference only "
                 "(Q1 Option A); they are never used to compute exposure, and the C2 anchors are unapproved"),
        Withheld(DETECTOR, Criterion.C2, "meeting summaries, e-mail and verbal statements are never binding "
                 "financial evidence (D15-04); protected payroll is never read"),
    ]
    return out
