"""Common detector output contract (ARCHITECTURE.md §16.1 step 1, §16.5, §8.4 T5).

A detector emits `Signal`s: typed, evidence-backed observations about one criterion (C1-C4). A signal is NOT a
score: it carries the observed facts, any number the approved rules support (in its natural unit), and an explicit
status saying how far the criterion can be established today. C5 is time context only (D15-01) and has no
detector; dates appear on C1 signals as temporal fields.

Invariants enforced here (constructing a signal that breaks one raises `ContractError`):
- statuses, flags, signal types and source authorities are controlled enums, never free model text;
- a signal whose status says the value is missing, unmapped, blocked or unquantified carries NO number: missing is
  never zero (D15-05);
- an active or provisional observation cites at least one admitted record;
- observed facts and assumptions are separate fields;
- the signal id is a deterministic hash of the content, so identical evidence, detector version and configuration
  reproduce the identical signal.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import date
from enum import StrEnum


class ContractError(ValueError):
    """A detector tried to emit a signal that breaks the output contract (a programming error, fail closed)."""


class Criterion(StrEnum):
    C1 = "C1"
    C2 = "C2"
    C3 = "C3"
    C4 = "C4"


class Status(StrEnum):
    ACTIVE = "active"                                  # an evidence-backed observation under approved rules
    PROVISIONAL = "provisional"                        # evidence-backed, but its definition awaits an owner decision
    UNQUANTIFIED = "unquantified"                      # valid input; no number is supported under approved rules
    UNMAPPED = "unmapped_requires_owner_approval"      # needs an owner-approved mapping before it can be compared
    BLOCKED = "blocked_owner_decision"                 # needs an owner decision before it can be measured
    MISSING = "missing_evidence"                       # the evidence the measure needs is not admitted


NUMBERLESS = frozenset({Status.UNQUANTIFIED, Status.UNMAPPED, Status.BLOCKED, Status.MISSING})
NEEDS_EVIDENCE = frozenset({Status.ACTIVE, Status.PROVISIONAL})


class SignalType(StrEnum):
    # C1 delivery exposure
    COMMITMENT = "commitment"                          # a commitment or milestone and its stated date
    COMMITMENT_STATUS = "commitment_status"            # current status derived from dated statements
    DELIVERY_DELAY = "delivery_delay"                  # a documented delay or missed milestone
    DELIVERY_EXPOSURE = "delivery_exposure"            # the C1 measure itself (required-rate ratio)
    # C2 financial exposure
    FINANCIAL_EXPOSURE = "financial_exposure"          # rolling 12-month incremental net INR
    # C3 continuity
    CONTINUITY_EVIDENCE = "continuity_evidence"        # rota, qualification or ownership evidence state
    CONTINUITY_MEASURE = "continuity_measure"          # the C3 measure itself
    # C4 integrity of figures reported upward
    FIGURE_DISCREPANCY = "figure_discrepancy"          # two sources, apparently one metric, different values
    FIGURE_COMPARISON = "figure_comparison"            # an attempted comparison that could not be made
    FIGURE_UNMAPPED = "figure_unmapped"                # a reported figure with no approved system pairing


class Authority(StrEnum):
    """What kind of source an observation rests on. Assigned by the detector from the source catalogue's kind and
    system, never by a model."""
    SYSTEM_RECORD = "system_record"                    # an export from a system of record
    BINDING_DOCUMENT = "binding_document"              # a contract, invoice or similar binding instrument
    FORMAL_REPORTED = "formal_reported"                # a formal document; figures are reported, not verified
    TRANSCRIPT_SUMMARY = "transcript_summary"          # an auto-generated meeting summary; never binding
    CONFIGURATION = "configuration"                    # an owner-approved configuration entry
    NONE = "none"                                      # no source (e.g. the evidence is missing)


class Flag(StrEnum):
    # temporal and date quality
    YEAR_INFERRED = "year_inferred"                    # the source states day and month only
    DATE_IMPRECISE = "date_imprecise"                  # e.g. "by the festive season", "by March"
    DATE_MISSING = "date_missing"
    DATE_SUPERSEDED = "date_superseded"                # a later record moved or withdrew the stated date
    NEW_DATE_NOT_STATED = "new_date_not_stated"
    RECORDED_DATE_UNKNOWN = "recorded_date_unknown"    # the source itself carries no date
    STATUS_ORDER_UNKNOWN = "status_order_unknown"      # undated statements cannot be ordered
    STATUS_CONFLICT = "status_conflict"                # same-date statements disagree
    STATUS_CONCERN_REPORTED = "status_concern_reported"
    # identity and source quality
    SUBJECT_UNRESOLVED = "subject_unresolved"          # the thing committed to is not named
    SUBJECT_FROM_CONTEXT = "subject_from_context"      # named in the preceding sentence, not this one
    LINK_LEXICAL = "link_lexical"                      # linked by an identical name only (no entity resolution)
    SOURCE_STATES_AMBIGUITY = "source_states_ambiguity"
    NON_BINDING_SOURCE = "non_binding_source"
    REPORTED_NOT_VERIFIED = "reported_not_verified"
    # C1 measure
    THROUGHPUT_NOT_APPROVED = "throughput_assumptions_not_approved"
    OPEN_SCOPE_MISSING = "open_scope_missing"
    # C2
    HORIZON_PARTIAL = "horizon_partial"                # part of the impact falls beyond the 12-month horizon
    OFFSET_UNCERTAIN = "offset_uncertain"
    OFFSET_UNSUPPORTED_IGNORED = "offset_unsupported_ignored"
    OFFSET_EXCEEDS_GROSS = "offset_exceeds_gross"
    MUTUALLY_EXCLUSIVE = "mutually_exclusive_outcomes"
    DUPLICATE_CONSEQUENCE = "duplicate_consequence"
    DUPLICATE_AMOUNTS_DIFFER = "duplicate_amounts_differ"
    UPPER_BOUND_MISSING = "upper_bound_missing"
    TIMING_UNKNOWN = "timing_unknown"
    # C3
    QUALIFICATION_MISSING = "qualification_evidence_missing"
    QUALIFICATION_INCOMPLETE = "qualification_evidence_incomplete"
    CURRENCY_UNKNOWN = "currency_unknown"
    RECORD_SUPERSEDED = "record_superseded"
    RECORD_INVALIDATED = "record_invalidated"
    CONFIRMED_ABSENCE = "confirmed_absence"
    ABSENCE_EVIDENCE_INSUFFICIENT = "absence_evidence_insufficient"
    OWNERSHIP_MISSING = "ownership_evidence_missing"
    GAP_SIZE_UNKNOWN = "gap_size_unknown"
    CRITICALITY_NOT_DESIGNATED = "criticality_not_designated"
    GUARDRAIL_NOT_ACTIVE = "guardrail_not_active"
    # C4
    UNIT_MISMATCH = "unit_mismatch"
    PERIOD_MISMATCH = "period_mismatch"
    METRIC_IDENTITY_UNDEFINED = "metric_identity_undefined"
    METRIC_IDENTITY_LABEL_ONLY = "metric_identity_label_only"
    VALUE_FORMAT_UNPARSED = "value_format_unparsed"
    MAPPING_PROPOSED_NOT_APPROVED = "mapping_proposed_not_approved"
    ROUNDING_COULD_EXPLAIN = "rounding_could_explain"
    MATERIALITY_NOT_ASSIGNED = "materiality_not_assigned"


class ValueKind(StrEnum):
    NONE = "none"
    EXACT = "exact"
    INTERVAL = "interval"
    CATEGORY = "category"


@dataclass(frozen=True)
class Value:
    kind: ValueKind = ValueKind.NONE
    low: float | None = None
    high: float | None = None
    category: str | None = None
    unit: str | None = None

    def __post_init__(self):
        numeric = self.kind in (ValueKind.EXACT, ValueKind.INTERVAL)
        if numeric:
            if self.low is None or self.high is None or not self.unit:
                raise ContractError("a numeric value needs low, high and a unit")
            if not (math.isfinite(self.low) and math.isfinite(self.high)) or self.low > self.high:
                raise ContractError(f"invalid interval [{self.low}, {self.high}]")
            if self.kind == ValueKind.EXACT and self.low != self.high:
                raise ContractError("an exact value has low == high")
        elif self.low is not None or self.high is not None:
            raise ContractError(f"a {self.kind} value carries no number")
        if self.kind == ValueKind.CATEGORY and not self.category:
            raise ContractError("a categorical value needs a category")

    @property
    def numeric(self) -> bool:
        return self.kind in (ValueKind.EXACT, ValueKind.INTERVAL)


@dataclass(frozen=True)
class EvidenceRef:
    record_id: str
    locator: str = ""            # where in the record (a sentence, a table row, a figure name)
    excerpt: str | None = None   # the short passage the observation rests on, as written


@dataclass(frozen=True)
class Signal:
    detector: str                           # detector id@version
    criterion: Criterion
    signal_type: SignalType
    status: Status
    subject: str                            # the business object, as named by the evidence (or "unresolved")
    measure: str                            # signal or measure identifier, e.g. "C2.net_exposure_12m@1"
    value: Value = Value()
    flags: frozenset[Flag] = frozenset()
    authority: Authority = Authority.NONE
    evidence: tuple[EvidenceRef, ...] = ()
    observed: tuple[str, ...] = ()          # facts as the sources state them
    assumptions: tuple[str, ...] = ()       # explicitly NOT observed
    missing: tuple[str, ...] = ()           # missing-data and uncertainty reasons
    decisions_pending: tuple[str, ...] = () # owner decisions that block or limit this signal (e.g. "Q7")
    window_start: date | None = None        # assessment window
    window_end: date | None = None
    event_date: date | None = None          # V: when the stated event happens or happened
    effective_from: date | None = None
    effective_to: date | None = None
    recorded_on: date | None = None         # R: when the source recorded it, if the source says
    related: tuple[str, ...] = ()           # keys of duplicate or correlated signals
    calculation: tuple[tuple[str, str], ...] = ()  # ordered provenance of any arithmetic
    key: str = ""                           # stable identity within the detector (for relating signals)
    lineage_batch_seq: int = 0              # T5: max batch_seq of the cited evidence (set on verification)

    def __post_init__(self):
        for name, enum in (("criterion", Criterion), ("signal_type", SignalType), ("status", Status),
                           ("authority", Authority)):
            if not isinstance(getattr(self, name), enum):
                raise ContractError(f"{name} must be a {enum.__name__}, got {getattr(self, name)!r}")
        if any(not isinstance(f, Flag) for f in self.flags):
            raise ContractError("flags must be Flag members")
        if "@" not in self.detector:
            raise ContractError("detector must be id@version")
        if self.status in NUMBERLESS and self.value.numeric:
            raise ContractError(f"a {self.status} signal carries no number (missing is never zero)")
        if self.status in NEEDS_EVIDENCE and not self.evidence:
            raise ContractError(f"a {self.status} signal must cite admitted evidence")
        if self.status in NEEDS_EVIDENCE and self.authority == Authority.NONE:
            raise ContractError("an evidence-backed signal must state its source authority")
        if not self.subject.strip() or not self.measure.strip():
            raise ContractError("subject and measure are required")
        if self.window_start and self.window_end and self.window_start > self.window_end:
            raise ContractError("assessment window ends before it starts")

    def body(self) -> dict:
        """JSON-ready content (deterministic: sorted flags, ISO dates)."""
        d = asdict(self)
        d["flags"] = sorted(f.value for f in self.flags)
        for k in ("criterion", "signal_type", "status", "authority"):
            d[k] = getattr(self, k).value
        d["value"] = {k: (v.value if isinstance(v, StrEnum) else v) for k, v in d["value"].items()}
        for k in ("window_start", "window_end", "event_date", "effective_from", "effective_to", "recorded_on"):
            d[k] = d[k].isoformat() if d[k] else None
        d["calculation"] = [list(p) for p in self.calculation]
        return d

    @property
    def signal_id(self) -> str:
        content = {k: v for k, v in self.body().items() if k != "lineage_batch_seq"}
        digest = hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return "S-" + digest[:16]

    @property
    def record_ids(self) -> tuple[str, ...]:
        return tuple(sorted({e.record_id for e in self.evidence}))


@dataclass(frozen=True)
class Withheld:
    """Why a detector did NOT emit something it looked for (provenance of a withheld signal)."""
    detector: str
    criterion: Criterion
    reason: str
    record_ids: tuple[str, ...] = ()


@dataclass
class DetectorOutput:
    signals: list[Signal] = field(default_factory=list)
    withheld: list[Withheld] = field(default_factory=list)
