"""Risk model vocabulary (ARCHITECTURE.md §16.2, §16.5; DEC-18 / ADR-14, structure approved; D-15 values pending).

Only what the approved architecture documents is used here:
- the five impact criteria and their natural scales (§16.2);
- intervals for criterion values (§16.3);
- quantification states (§16.5).
NOT used, because they are owner value judgements still pending under D-15: weights, anchors, thresholds,
guardrail tiers and tie-breaks. There is no score field anywhere in this package.

`worse_when` is the direction of each natural scale as worded in §16.2 ("exposure", "proximity"). It is listed for
owner confirmation in the D-15 decision sheet.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Criterion:
    criterion_id: str
    name: str
    natural_scale: str
    worse_when: str            # "higher" | "lower"
    minimum_evidence: str


CRITERIA: dict[str, Criterion] = {c.criterion_id: c for c in (
    Criterion("C1", "External-commitment exposure",
              "Required-rate ratio toward a dated customer or board commitment", "higher",
              "A dated commitment in a record or document, an open-scope fact, a throughput window"),
    Criterion("C2", "Financial exposure", "Currency at stake, from records the principal may see", "higher",
              "A record-backed amount; amounts derivable only from protected pay are not used"),
    Criterion("C3", "Operational-continuity exposure",
              "Share of commitment-linked work or a critical service held by one person; on-call coverage "
              "concentration", "higher", "Allocation records only (tickets, rotas, requisitions)"),
    Criterion("C4", "Information-integrity exposure",
              "Discrepancy between figures reported upward and system records", "higher",
              "Two independent sources in conflict"),
    Criterion("C5", "Time proximity", "Days to the affected commitment", "lower", "A dated commitment"),
)}

# Measure states. Only "measured" values take part in a comparison. "missing" and "uncertain" are never read as
# zero, low or favourable. "not_applicable" is declared by a detector for its business-object type, and is never
# read as zero either (see compare.py).
MEASURED, MISSING, UNCERTAIN, NOT_APPLICABLE = "measured", "missing", "uncertain", "not_applicable"


@dataclass(frozen=True)
class MeasureDefinition:
    """How a criterion is measured. Two values are comparable only if their definitions are identical."""
    criterion_id: str
    definition_id: str          # e.g. "C4.absolute_discrepancy"
    version: int
    unit: str                   # e.g. "people", "INR"
    description: str

    @property
    def ref(self) -> str:
        return f"{self.definition_id}@{self.version} [{self.unit}]"


@dataclass(frozen=True)
class MeasureValue:
    state: str
    definition: MeasureDefinition | None = None
    low: float | None = None    # interval on the natural scale (equal bounds for an exact value)
    high: float | None = None
    evidence: tuple[str, ...] = ()
    note: str = ""

    def __post_init__(self):
        if self.state == MEASURED:
            if self.definition is None or self.low is None or self.high is None or self.low > self.high:
                raise ValueError("a measured value needs a definition and an interval low <= high")
        elif self.low is not None or self.high is not None:
            raise ValueError(f"a {self.state} value carries no number")


@dataclass
class Risk:
    risk_id: str
    detector: str                          # detector id@version
    business_object: str
    description: str
    measures: dict[str, MeasureValue]      # criterion id -> value (every criterion C1-C5 present)
    evidence: list[str]                    # record ids (visible at the read basis)
    time_context: dict
    unknowns: list[str] = field(default_factory=list)

    def __post_init__(self):
        if set(self.measures) != set(CRITERIA):
            raise ValueError(f"{self.risk_id}: every criterion C1-C5 must have a declared state")

    @property
    def quantification_state(self) -> str:
        """§16.5. quantified: every applicable criterion measured; unquantified: some applicable criterion missing
        or uncertain but at least one measured; insufficient: nothing measured."""
        applicable = [m for m in self.measures.values() if m.state != NOT_APPLICABLE]
        measured = [m for m in applicable if m.state == MEASURED]
        if not measured:
            return "insufficient"
        return "quantified" if len(measured) == len(applicable) else "unquantified"
