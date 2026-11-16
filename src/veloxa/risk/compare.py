"""Unweighted partial-order comparison of risks (ARCHITECTURE.md §16.3, without the D-15 values).

Risk A DOMINATES risk B only when, on every criterion that applies to either risk:
- both risks have a MEASURED value with an IDENTICAL measure definition (same definition, version and unit), and
- A is demonstrably at least as bad as B: A's whole interval is at least as bad as B's whole interval
  (for "higher is worse": A.low >= B.high; for "lower is worse": A.high <= B.low),
and A is demonstrably strictly worse on at least one criterion.

Anything else is NOT forced into an order:
- trade_off: each risk is demonstrably worse on a different criterion;
- indistinguishable: no demonstrable difference in either direction ("not distinguishable on current evidence");
- not_comparable: a criterion is missing or uncertain for either risk, applies to one risk but not the other,
  or the two values use different measure definitions. Missing evidence is never read as zero or favourable,
  and "not applicable" for one risk is never read as zero against a measured value of the other.

No weights, scores, anchors, thresholds, guardrail tiers or tie-breaks are used. Dominance defined this way is
transitive (interval bounds chain), so the result is a genuine partial order, never completed into a ranking.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import CRITERIA, MEASURED, NOT_APPLICABLE, Risk

DOMINATES, DOMINATED_BY, TRADE_OFF, INDISTINGUISHABLE, NOT_COMPARABLE = (
    "dominates", "dominated_by", "trade_off", "indistinguishable", "not_comparable")


@dataclass
class PairOutcome:
    a: str
    b: str
    relation: str                       # from a's point of view
    explanation: str
    criteria: list[dict] = field(default_factory=list)


def _at_least_as_bad(worse_when: str, x: tuple[float, float], y: tuple[float, float]) -> tuple[bool, bool]:
    """(x demonstrably at least as bad as y, x demonstrably strictly worse than y), on whole intervals."""
    if worse_when == "higher":
        return x[0] >= y[1], x[0] > y[1]
    return x[1] <= y[0], x[1] < y[0]


def compare(a: Risk, b: Risk) -> PairOutcome:
    rows: list[dict] = []
    blockers: list[str] = []
    a_ge_all, b_ge_all, a_strict, b_strict = True, True, [], []
    for cid, crit in CRITERIA.items():
        ma, mb = a.measures[cid], b.measures[cid]
        if ma.state == NOT_APPLICABLE and mb.state == NOT_APPLICABLE:
            continue
        if ma.state != MEASURED or mb.state != MEASURED:
            for risk, m in ((a, ma), (b, mb)):
                if m.state != MEASURED:
                    blockers.append(f"{cid} is {m.state.replace('_', ' ')} for {risk.risk_id}")
            rows.append({"criterion": cid, "comparable": False, "reason": "not measured for both risks"})
            continue
        if ma.definition != mb.definition:
            blockers.append(f"{cid} uses different measure definitions ({ma.definition.ref} vs {mb.definition.ref})")
            rows.append({"criterion": cid, "comparable": False, "reason": "incompatible measure definitions"})
            continue
        x, y = (ma.low, ma.high), (mb.low, mb.high)
        a_ge, a_gt = _at_least_as_bad(crit.worse_when, x, y)
        b_ge, b_gt = _at_least_as_bad(crit.worse_when, y, x)
        a_ge_all &= a_ge
        b_ge_all &= b_ge
        if a_gt:
            a_strict.append(cid)
        if b_gt:
            b_strict.append(cid)
        rows.append({"criterion": cid, "comparable": True, "definition": ma.definition.ref,
                     "worse_when": crit.worse_when, a.risk_id: list(x), b.risk_id: list(y),
                     "difference": (f"{a.risk_id} worse" if a_gt else f"{b.risk_id} worse" if b_gt
                                    else "equal" if x == y else "intervals overlap")})
    if blockers:
        return PairOutcome(a.risk_id, b.risk_id, NOT_COMPARABLE,
                           "Not compared: " + "; ".join(dict.fromkeys(blockers)) + ".", rows)
    if not rows:
        return PairOutcome(a.risk_id, b.risk_id, NOT_COMPARABLE, "Not compared: no criterion applies to either.", rows)
    if a_ge_all and a_strict:
        return PairOutcome(a.risk_id, b.risk_id, DOMINATES,
                           f"{a.risk_id} is at least as bad on every applicable criterion and worse on "
                           f"{', '.join(a_strict)}.", rows)
    if b_ge_all and b_strict:
        return PairOutcome(a.risk_id, b.risk_id, DOMINATED_BY,
                           f"{b.risk_id} is at least as bad on every applicable criterion and worse on "
                           f"{', '.join(b_strict)}.", rows)
    if a_strict and b_strict:
        return PairOutcome(a.risk_id, b.risk_id, TRADE_OFF,
                           f"Trade-off: {a.risk_id} is worse on {', '.join(a_strict)}, {b.risk_id} on "
                           f"{', '.join(b_strict)}. Ordering them needs the D-15 weights.", rows)
    return PairOutcome(a.risk_id, b.risk_id, INDISTINGUISHABLE,
                       "Not distinguishable on current evidence (equal values or overlapping intervals); "
                       "no tie-break is applied.", rows)


_FLIP = {DOMINATES: DOMINATED_BY, DOMINATED_BY: DOMINATES}


def partial_order(risks: list[Risk]) -> dict:
    """Pairwise outcomes, plus per risk: which risks demonstrably dominate it and which it dominates.
    Risks in the `insufficient` quantification state take no part (they are listed as signals needing data)."""
    comparable = [r for r in risks if r.quantification_state != "insufficient"]
    pairs: list[PairOutcome] = []
    per: dict[str, dict] = {r.risk_id: {"dominates": [], "dominated_by": [], "trade_off_with": [],
                                        "indistinguishable_from": [], "not_comparable_with": []} for r in comparable}
    key = {DOMINATES: "dominates", DOMINATED_BY: "dominated_by", TRADE_OFF: "trade_off_with",
           INDISTINGUISHABLE: "indistinguishable_from", NOT_COMPARABLE: "not_comparable_with"}
    for i, a in enumerate(comparable):
        for b in comparable[i + 1:]:
            o = compare(a, b)
            pairs.append(o)
            per[a.risk_id][key[o.relation]].append(b.risk_id)
            per[b.risk_id][key[_FLIP.get(o.relation, o.relation)]].append(a.risk_id)
    return {"pairs": pairs, "per_risk": per}
