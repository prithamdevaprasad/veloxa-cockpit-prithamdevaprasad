"""D-15 version 1 weighted severity ranking (hackathon scope; config/d15/D15_v1_hackathon.toml).

Additive multi-attribute value with swing weights, computed in INTERVALS:
- each criterion's measure (an interval, possibly a point) maps through a linear value function between its anchors
  to v in [0, 1] (clamped: at or beyond best = 0, at or beyond worst = 1);
- not applicable = best anchor (v = 0); missing / unquantified / unmapped = the WHOLE interval [0, 1] (D15-05:
  unknown is never zero, never a low score);
- severity S = [sum w_i * v_lo_i, sum w_i * v_hi_i]; it is an ORDINAL exposure score in [0, 1], never a probability;
- eligibility: at least `eligibility_min_measured_weight` of the weight is measured or not applicable, every
  evidence reference is verified, and at least one record is cited. Others are listed as unrankable, with reasons;
- order: by severity interval; items whose intervals overlap (transitively) share a RANK BAND (no arbitrary
  tie-break); within a band, display order is by (S_hi, S_lo) and is labelled as not significant;
- sensitivity: every weight moved by +/- delta (renormalised); an item whose band position changes is `sensitive`.
Confidence (evidence quality) is carried separately and never alters severity.
"""

from __future__ import annotations

import itertools
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CRITERIA = ("C1", "C2", "C3", "C4")
NO_MEASURE = "no evidence-backed measure"   # a criterion the item never declared: counted as the whole range in the
                                            # maths, but it is NOT an item-specific unknown and is not presented as one
POLICY_ENV = "VELOXA_D15_POLICY"     # path to the D-15 decision file; it stays PRIVATE (its C2 anchor derives from
                                     # board-reported figures), never in the public repository


class PolicyMissing(RuntimeError):
    """No D-15 decision file is configured: ranking is unavailable (the worry view stays unranked)."""


@dataclass(frozen=True)
class Measure:
    status: str                         # measured | not_applicable | missing | unquantified | unmapped
    low: float | None = None
    high: float | None = None
    measure: str | None = None          # which measure (primary or fallback)
    reason: str = ""

    def __post_init__(self):
        if self.status == "measured" and (self.low is None or self.high is None or self.low > self.high):
            raise ValueError("a measured criterion needs low <= high")


@dataclass
class Item:
    item_id: str
    title: str
    measures: dict[str, Measure]
    evidence: list[str]
    verified: bool = True
    confidence: str = "moderate"        # high | moderate | low | unknown | conflicting (never alters severity)
    department: str | None = None
    project: str | None = None
    unknowns: list[str] = field(default_factory=list)
    follow_up: str = ""


def load_policy(path: Path | None = None) -> dict:
    raw = path or (Path(os.environ[POLICY_ENV]) if os.environ.get(POLICY_ENV) else None)
    if raw is None or not Path(raw).is_file():
        raise PolicyMissing(f"no D-15 decision file ({POLICY_ENV} is not set to an existing file)")
    return tomllib.loads(Path(raw).read_text(encoding="utf-8"))["content"]


def _anchors(policy: dict, crit: str, measure: str | None) -> tuple[float, float]:
    a = policy["anchors"][crit]
    if measure and measure == a.get("fallback_measure"):
        return float(a["fallback_best"]), float(a["fallback_worst"])
    return float(a["best"]), float(a["worst"])


def value(x: float, best: float, worst: float) -> float:
    """Linear value between anchors; higher measure = worse (D15-06). Clamped to [0, 1]."""
    if worst == best:
        raise ValueError("anchors must differ")
    return min(1.0, max(0.0, (x - best) / (worst - best)))


def criterion_value(policy: dict, crit: str, m: Measure) -> tuple[float, float, bool]:
    """(v_lo, v_hi, counted_as_measured)."""
    if m.status == "not_applicable":
        return 0.0, 0.0, True
    if m.status != "measured":
        return 0.0, 1.0, False
    best, worst = _anchors(policy, crit, m.measure)
    return value(m.low, best, worst), value(m.high, best, worst), True


def score(policy: dict, item: Item, weights: dict[str, float] | None = None) -> dict:
    w = weights or policy["weights"]
    parts, lo, hi, measured_w = {}, 0.0, 0.0, 0.0
    for c in CRITERIA:
        m = item.measures.get(c, Measure("missing", reason=NO_MEASURE))      # maths: the whole range
        vlo, vhi, counted = criterion_value(policy, c, m)
        lo += w[c] * vlo
        hi += w[c] * vhi
        measured_w += w[c] if counted else 0.0
        parts[c] = {"status": m.status, "measure": m.measure, "low": m.low, "high": m.high, "v_low": round(vlo, 4),
                    "v_high": round(vhi, 4), "weight": w[c], "contribution_low": round(w[c] * vlo, 4),
                    "contribution_high": round(w[c] * vhi, 4), "reason": m.reason}
    return {"low": round(lo, 4), "high": round(hi, 4), "measured_weight": round(measured_w, 4), "criteria": parts}


def eligibility(policy: dict, item: Item, s: dict) -> list[str]:
    g = policy["gate_thresholds"]
    why = []
    if s["measured_weight"] + 1e-9 < g["eligibility_min_measured_weight"]:
        why.append(f"too little of its exposure can be measured from the records ({s['measured_weight']:.0%} "
                   f"measured; at least {g['eligibility_min_measured_weight']:.0%} is needed to rank it)")
    if g.get("require_verified_evidence") and not item.verified:
        why.append("its evidence did not pass verification")
    if len(item.evidence) < g.get("min_evidence_records", 1):
        why.append("no admitted record supports it")
    return why


def bands(scored: list[tuple[Item, dict]]) -> list[list[tuple[Item, dict]]]:
    """Order by (high, low) descending; consecutive items whose intervals overlap the band's lowest low join it."""
    order = sorted(scored, key=lambda p: (-p[1]["high"], -p[1]["low"], p[0].item_id))
    out: list[list] = []
    for it in order:
        if out and it[1]["high"] >= min(x[1]["low"] for x in out[-1]):
            out[-1].append(it)
        else:
            out.append([it])
    return out


def _positions(policy: dict, items: list[Item], weights: dict) -> dict[str, int]:
    scored = [(i, score(policy, i, weights)) for i in items]
    return {it.item_id: b for b, band in enumerate(bands(scored), 1) for it, _ in band}


def sensitivity(policy: dict, items: list[Item]) -> dict[str, bool]:
    """True when any +/- delta move of the weights (renormalised) changes an item's band position."""
    base = _positions(policy, items, policy["weights"])
    d = policy["gate_thresholds"]["sensitivity_weight_delta"]
    changed = {i.item_id: False for i in items}
    for signs in itertools.product((-1, 0, 1), repeat=len(CRITERIA)):
        if not any(signs):
            continue
        raw = {c: max(0.0, policy["weights"][c] + s * d) for c, s in zip(CRITERIA, signs, strict=True)}
        tot = sum(raw.values())
        pos = _positions(policy, items, {c: v / tot for c, v in raw.items()})
        for k, v in pos.items():
            if v != base[k]:
                changed[k] = True
    return changed


def rank(items: list[Item], policy: dict | None = None) -> dict:
    policy = policy or load_policy()
    if abs(sum(policy["weights"][c] for c in CRITERIA) - 1.0) > 1e-9:
        raise ValueError("D-15 weights must sum to 1")
    eligible, unrankable = [], []
    for it in items:
        s = score(policy, it)
        why = eligibility(policy, it, s)
        (unrankable if why else eligible).append((it, s, why))
    sens = sensitivity(policy, [e[0] for e in eligible]) if eligible else {}
    ranked = []
    for b, band in enumerate(bands([(e[0], e[1]) for e in eligible]), 1):
        for it, s in band:
            ranked.append({"rank": b, "tied": len(band) > 1, "item_id": it.item_id, "title": it.title,
                           "department": it.department, "project": it.project, "severity": s,
                           "confidence": it.confidence, "evidence": it.evidence, "unknowns": it.unknowns,
                           "sensitive": sens.get(it.item_id, False), "follow_up": it.follow_up,
                           "why": _why(s, b, len(band))})
    return {"policy": {"weights": policy["weights"], "method": policy["method"], "probabilities": "disabled"},
            "ranked": ranked,
            "unrankable": [{"item_id": it.item_id, "title": it.title, "severity": s, "reasons": why,
                            "evidence": it.evidence, "confidence": it.confidence, "unknowns": it.unknowns,
                            "follow_up": it.follow_up, "department": it.department, "project": it.project}
                           for it, s, why in unrankable]}


def _why(s: dict, band: int, size: int) -> str:
    top = sorted(((c, p) for c, p in s["criteria"].items()), key=lambda x: -x[1]["contribution_high"])
    drivers = ", ".join(f"{c} ({p['status'].replace('_', ' ')}, contributes {p['contribution_low']:.2f}"
                        f"–{p['contribution_high']:.2f})" for c, p in top if p["contribution_high"] > 0)
    tie = f" It shares rank {band} with {size - 1} other item(s): their severity intervals overlap." if size > 1 else ""
    return f"Severity {s['low']:.2f}–{s['high']:.2f} (ordinal, not a probability). Drivers: {drivers or 'none'}.{tie}"
