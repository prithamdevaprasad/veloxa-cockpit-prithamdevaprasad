"""C3 service and critical-system continuity: evidence normalisation and validation (ARCHITECTURE.md §16.2, §16.6).

Approved: D15-02 (C3 covers service and critical-system continuity; commitment-work concentration is a C1
driver, not a C3 score), D15-05 (missing is never zero), D15-25 (outage costs are C2 only), §16.6 (formal
allocation records only: rotas, tickets, requisitions; never leave, activity timing or communication content).

NOT approved, so NOT implemented as production policy here:
- the detailed C3 measure definitions (Q3), the zero-coverage treatment and continuity conditions (Q9), and the
  record-currency rules (Q10). This module therefore computes NO concentration number and NO C3 score. Its measure
  signal is BLOCKED pending those decisions;
- any guardrail: no service is owner-designated as critical (Q9.6), so a confirmed absence is reported as an
  evidence state, never as a guardrail;
- any freshness interval: currency is whatever the record itself establishes (a recorded invalidating event, or
  an explicit effective period); otherwise it is CURRENCY UNKNOWN, which is neither current nor invalid.

What it does do (generic validation, independent of those decisions):
- rota entries: exact effective periods, with later versions superseding earlier ones for overlapping slots;
- one combined status per responder: counted as qualified only when qualification AND currency are confirmed;
- contradiction prevention: asserting confirmed absence while any responder with coverage is qualified or
  unresolved is INVALID INPUT (an error), not an observation;
- absence evidence must name its record and state that it is current, applies to the scope, covers the window and
  is authoritative; anything less is valid input that proves nothing (ABSENCE_EVIDENCE_INSUFFICIENT);
- records derived from leave or protected absence data are refused outright.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
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

DETECTOR = "c3_continuity@1"
MEASURE = "C3.continuity@1"
PENDING = ("Q3", "Q9", "Q10")
FORBIDDEN_SOURCES = frozenset({"leave", "absence", "sickness", "medical"})  # matched as words in the source kind


class InvalidC3Input(ValueError):
    """Contradictory or malformed C3 evidence: rejected, never turned into an observation."""


class ProtectedSourceError(InvalidC3Input):
    """A C3 input derives from leave or protected absence data (§16.6)."""


class Qualification(StrEnum):
    QUALIFIED = "qualified"          # a record confirms the qualification
    UNKNOWN = "unknown"              # a record exists, but does not establish qualification
    DISQUALIFIED = "disqualified"    # a record states the person is not qualified
    NO_RECORD = "no_record"


class Currency(StrEnum):
    CURRENT = "current"              # the record itself establishes it holds for the window
    INVALIDATED = "invalidated"      # a recorded, correctly scoped event ended it
    UNKNOWN = "currency_unknown"     # neither can be established (never treated as current or as invalid)


def _refuse_protected(source_kind: str) -> None:
    if FORBIDDEN_SOURCES & set(re.split(r"[^a-z]+", source_kind.lower())):
        raise ProtectedSourceError("C3 never uses leave or protected absence data (§16.6)")


@dataclass(frozen=True)
class RotaEntry:
    service: str
    responder: str                   # an opaque responder reference from the rota record
    start: date                      # effective period [start, end)
    end: date
    coverage: float                  # coverage units in the window (e.g. shifts)
    record: EvidenceRef
    version: int = 1                 # a later version supersedes earlier ones for the same service and slot
    source_kind: str = "rota"

    def __post_init__(self):
        _refuse_protected(self.source_kind)
        if not self.service.strip() or not self.responder.strip():
            raise InvalidC3Input("rota entry needs a service and a responder")
        if self.end <= self.start:
            raise InvalidC3Input("rota period must end after it starts")
        if not (self.coverage >= 0) or self.coverage != self.coverage or self.coverage == float("inf"):
            raise InvalidC3Input("coverage must be a finite non-negative number")
        if self.version < 1:
            raise InvalidC3Input("version must be >= 1")


@dataclass(frozen=True)
class QualificationRecord:
    responder: str
    scope: str                       # the service or system the qualification is for
    qualification: Qualification
    currency: Currency
    record: EvidenceRef
    source_kind: str = "qualification"

    def __post_init__(self):
        _refuse_protected(self.source_kind)
        if self.qualification == Qualification.NO_RECORD:
            raise InvalidC3Input("a qualification record cannot state 'no_record'; omit it instead")


@dataclass(frozen=True)
class AbsenceEvidence:
    record: EvidenceRef
    current: bool
    applies_to_scope: bool
    covers_window: bool
    authoritative: bool

    def __post_init__(self):
        if not self.record.record_id.strip():
            raise InvalidC3Input("absence evidence must name its record")
        for name in ("current", "applies_to_scope", "covers_window", "authoritative"):
            if not isinstance(getattr(self, name), bool):
                raise InvalidC3Input(f"absence evidence flag {name} must be a boolean")

    @property
    def sufficient(self) -> bool:
        return self.current and self.applies_to_scope and self.covers_window and self.authoritative


@dataclass(frozen=True)
class OwnershipRecord:
    system: str
    owner: str
    currency: Currency
    record: EvidenceRef


def effective_rota(entries: list[RotaEntry], window: tuple[date, date]) -> tuple[list[RotaEntry], list[RotaEntry]]:
    """(effective, superseded) entries overlapping the window. For one service, an entry is superseded when a
    higher version covers an overlapping period (the later rota revision replaces the affected slots)."""
    ws, we = window
    live = [e for e in entries if e.start < we and e.end > ws]
    superseded = [e for e in live if any(o.service == e.service and o.version > e.version
                                         and o.start < e.end and o.end > e.start for o in live)]
    return [e for e in live if e not in superseded], superseded


def responder_status(responder: str, scope: str, quals: list[QualificationRecord]) -> tuple[str, set[Flag]]:
    """One combined status per responder: qualified only when qualification and currency are both confirmed."""
    mine = [q for q in quals if q.responder == responder and q.scope == scope]
    if not mine:
        return "no_record", {Flag.QUALIFICATION_MISSING}
    if any(q.qualification == Qualification.DISQUALIFIED or q.currency == Currency.INVALIDATED for q in mine) and \
            not any(q.qualification == Qualification.QUALIFIED and q.currency == Currency.CURRENT for q in mine):
        flags = {Flag.RECORD_INVALIDATED} if any(q.currency == Currency.INVALIDATED for q in mine) else set()
        return "disqualified", flags
    if any(q.qualification == Qualification.QUALIFIED and q.currency == Currency.CURRENT for q in mine):
        return "qualified", set()
    flags = {Flag.QUALIFICATION_INCOMPLETE}
    if any(q.currency == Currency.UNKNOWN for q in mine):
        flags.add(Flag.CURRENCY_UNKNOWN)
    return "unknown", flags


def service_signals(service: str, window: tuple[date, date], rota: list[RotaEntry],
                    quals: list[QualificationRecord], absence: AbsenceEvidence | None = None,
                    gap_slots_known: bool = True) -> list[Signal]:
    """Evidence-state observations for one service and window. Raises InvalidC3Input on contradictions."""
    effective, superseded = effective_rota([e for e in rota if e.service == service], window)
    statuses: dict[str, tuple[str, set[Flag]]] = {}
    for e in effective:
        if e.coverage > 0:
            statuses[e.responder] = responder_status(e.responder, service, quals)
    by_status: dict[str, int] = {}
    flags: set[Flag] = {Flag.CRITICALITY_NOT_DESIGNATED, Flag.GUARDRAIL_NOT_ACTIVE}
    for s, f in statuses.values():
        by_status[s] = by_status.get(s, 0) + 1
        flags |= f
    if superseded:
        flags.add(Flag.RECORD_SUPERSEDED)
    if not gap_slots_known:
        flags.add(Flag.GAP_SIZE_UNKNOWN)
    unresolved = by_status.get("unknown", 0) + by_status.get("no_record", 0)
    if absence is not None:
        if by_status.get("qualified", 0) or unresolved:
            raise InvalidC3Input(f"{service}: confirmed absence of qualified coverage contradicts responders who "
                                 "are qualified or whose qualification is unresolved")
        if absence.sufficient:
            flags.add(Flag.CONFIRMED_ABSENCE)
        else:
            flags.add(Flag.ABSENCE_EVIDENCE_INSUFFICIENT)
    records = sorted({e.record for e in effective + superseded} | {q.record for q in quals if q.scope == service}
                     | ({absence.record} if absence else set()), key=lambda r: (r.record_id, r.locator))
    ws, we = window
    observed = tuple(f"{n} rostered responder(s) with coverage whose combined status is {s}"
                     for s, n in sorted(by_status.items()))
    if Flag.CONFIRMED_ABSENCE in flags:
        category = "confirmed_no_qualified_coverage"
    elif not effective:
        category = "no_effective_rota"
    elif unresolved:
        category = "qualification_unresolved"
    else:
        category = "evidence_resolved"
    out = []
    if records:
        out.append(Signal(
            DETECTOR, Criterion.C3, SignalType.CONTINUITY_EVIDENCE, Status.PROVISIONAL, service,
            "C3.oncall_evidence_state@1", Value(ValueKind.CATEGORY, category=category), frozenset(flags),
            Authority.SYSTEM_RECORD, tuple(records), observed=observed,
            assumptions=("the service is not owner-designated as critical; no continuity condition or guardrail "
                         "applies (Q9.6)",),
            missing=tuple(m for m in (
                "qualification records are missing for some rostered responders"
                if Flag.QUALIFICATION_MISSING in flags else None,
                "the currency of some qualification records cannot be established"
                if Flag.CURRENCY_UNKNOWN in flags else None,
                "the size of an undocumented period cannot be established" if Flag.GAP_SIZE_UNKNOWN in flags
                else None,
                "absence is asserted, but the evidence is not current, in scope, window-covering and authoritative"
                if Flag.ABSENCE_EVIDENCE_INSUFFICIENT in flags else None) if m),
            decisions_pending=PENDING, window_start=ws, window_end=we, key=f"c3|{service}|evidence"))
    out.append(Signal(
        DETECTOR, Criterion.C3, SignalType.CONTINUITY_MEASURE, Status.BLOCKED, service, MEASURE, Value(),
        frozenset({Flag.GUARDRAIL_NOT_ACTIVE}), Authority.NONE, (),
        missing=("the C3 measure definitions (Q3), the zero-coverage and continuity treatment (Q9) and the "
                 "record-currency rules (Q10) are owner decisions not yet made; no C3 number is produced",),
        decisions_pending=PENDING, window_start=ws, window_end=we, key=f"c3|{service}|measure"))
    return out


def ownership_signals(system: str, records: list[OwnershipRecord], absence: AbsenceEvidence | None = None,
                      ) -> list[Signal]:
    counts: dict[Currency, int] = {}
    for r in records:
        counts[r.currency] = counts.get(r.currency, 0) + 1
    flags: set[Flag] = {Flag.CRITICALITY_NOT_DESIGNATED, Flag.GUARDRAIL_NOT_ACTIVE}
    if absence is not None:
        if counts.get(Currency.CURRENT) or counts.get(Currency.UNKNOWN):
            raise InvalidC3Input(f"{system}: confirmed absence of an owner contradicts current or "
                                 "currency-unknown owner records")
        flags.add(Flag.CONFIRMED_ABSENCE if absence.sufficient else Flag.ABSENCE_EVIDENCE_INSUFFICIENT)
    if not records and absence is None:
        flags.add(Flag.OWNERSHIP_MISSING)
    if counts.get(Currency.UNKNOWN):
        flags.add(Flag.CURRENCY_UNKNOWN)
    if counts.get(Currency.INVALIDATED):
        flags.add(Flag.RECORD_INVALIDATED)
    refs = tuple(sorted({r.record for r in records} | ({absence.record} if absence else set()),
                        key=lambda r: (r.record_id, r.locator)))
    if Flag.CONFIRMED_ABSENCE in flags:
        category = "confirmed_no_owner"
    elif not records:
        category = "ownership_evidence_missing"
    elif counts.get(Currency.CURRENT):
        category = "current_owner_recorded"
    else:
        category = "owner_currency_unknown"
    status = Status.PROVISIONAL if refs else Status.MISSING
    return [Signal(
        DETECTOR, Criterion.C3, SignalType.CONTINUITY_EVIDENCE, status, system, "C3.ownership_evidence_state@1",
        Value(ValueKind.CATEGORY, category=category), frozenset(flags),
        Authority.SYSTEM_RECORD if refs else Authority.NONE, refs,
        observed=tuple(f"{n} owner record(s) with currency {c}" for c, n in sorted(counts.items())),
        missing=("no ownership record is admitted; that does not show the system is unowned",) if not records
        else (), decisions_pending=PENDING, key=f"c3|{system}|ownership")]


def detect(conn, basis, context: dict) -> DetectorOutput:
    """No admitted source is a rota, qualification, ownership or service-catalogue record in this engine version."""
    out = DetectorOutput()
    for what, key in (("on-call rota records", "rota"), ("qualification records", "qualification"),
                      ("system-ownership and named-backup records", "ownership"),
                      ("a service and critical-system catalogue", "services")):
        out.signals.append(Signal(
            DETECTOR, Criterion.C3, SignalType.CONTINUITY_EVIDENCE, Status.MISSING, "all services and systems",
            f"C3.{key}_evidence@1", Value(), frozenset({Flag.CRITICALITY_NOT_DESIGNATED}), Authority.NONE,
            missing=(f"no {what} are admitted; a missing record proves neither absence nor presence",),
            decisions_pending=PENDING, key=f"c3|missing|{key}"))
    out.signals.append(Signal(
        DETECTOR, Criterion.C3, SignalType.CONTINUITY_MEASURE, Status.BLOCKED, "all services and systems", MEASURE,
        Value(), frozenset({Flag.GUARDRAIL_NOT_ACTIVE}), Authority.NONE,
        missing=("C3 measure definitions (Q3), continuity treatment (Q9) and currency rules (Q10) are pending owner "
                 "decisions; no service is designated critical (Q9.6)",),
        decisions_pending=PENDING, key="c3|measure"))
    out.withheld += [
        Withheld(DETECTOR, Criterion.C3, "the HR export is not an allocation record: it is not used to infer "
                 "ownership, qualification or coverage (§16.6)"),
        Withheld(DETECTOR, Criterion.C3, "ticket counts are never a substitute for qualified ownership; no ticket "
                 "records are admitted"),
        Withheld(DETECTOR, Criterion.C3, "leave and protected absence data are never used to infer availability"),
    ]
    return out
