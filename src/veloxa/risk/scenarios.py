"""Deterministic scenarios over admitted evidence (ARCHITECTURE.md §16.4, §16.5; Sprint 8).

A scenario never predicts. It separates four kinds of statement:
  observed    what admitted records state (with their evidence ids)
  derived     arithmetic on observed facts and the scenario input, with its calculation shown
  assumption  the scenario input itself, labelled as NOT evidence
  unknown     what the evidence does not establish, including everything the detectors report as missing
No probability, score or weight is produced (§16.4 gates are not met; D-15 is pending).

Scenario `commitment_shift`: "what if commitment X moves by N days?" over the C1 detector's signals at the read
basis (run, never recorded). A shifted date is derived ONLY from a stated commitment date that no later recorded
delay supersedes; a commitment moved without a replacement date has no base date, and the scenario says so.
The C1 measure (required-rate ratio) is never recomputed: open scope and throughput are not admitted (Q7).
"""

from __future__ import annotations

from datetime import date, timedelta

import psycopg

from ..signals import registry
from ..signals.contract import Criterion, SignalType
from ..temporal import ReadBasis

SCENARIO_VERSION = "scenarios/1"
MAX_SHIFT_DAYS = 730


class ScenarioError(ValueError):
    pass


def _c1(conn: psycopg.Connection, basis: ReadBasis, tz: str):
    result = registry.run(conn, basis, registry.context_for(basis, tz, [], []))
    return [s for s in result.signals if s.criterion == Criterion.C1]


def commitments(conn: psycopg.Connection, basis: ReadBasis, tz: str) -> list[str]:
    """Named commitments the evidence states at this basis (unresolved subjects are never offered)."""
    return sorted({s.subject for s in _c1(conn, basis, tz) if s.subject and s.subject != "unresolved"
                   and s.signal_type in (SignalType.COMMITMENT, SignalType.DELIVERY_DELAY,
                                         SignalType.COMMITMENT_STATUS)})


def _day(s) -> date | None:
    return s.recorded_on or s.valid_from


def commitment_shift(conn: psycopg.Connection, basis: ReadBasis, tz: str, business_object: str,
                     shift_days: int) -> dict:
    if not isinstance(shift_days, int) or isinstance(shift_days, bool) or abs(shift_days) > MAX_SHIFT_DAYS:
        raise ScenarioError(f"shift_days must be a whole number of days within ±{MAX_SHIFT_DAYS}")
    signals = [s for s in _c1(conn, basis, tz) if s.subject == business_object]
    if not signals:
        raise ScenarioError("no admitted evidence names this commitment at the read basis")
    observed, unknowns = [], []
    for s in sorted(signals, key=lambda s: (_day(s) or date.min, s.signal_type, s.key)):
        for o in s.observed or (s.signal_type.replace("_", " "),):
            observed.append({"statement": o, "signal_type": str(s.signal_type), "status": str(s.status),
                             "stated_date": s.event_date.isoformat() if s.event_date else None,
                             "recorded_on": _day(s).isoformat() if _day(s) else None,
                             "evidence": [e.record_id for e in s.evidence]})
        unknowns += list(s.missing)
    dated = [s for s in signals if s.signal_type == SignalType.COMMITMENT and s.event_date]
    delays = [s for s in signals if s.signal_type == SignalType.DELIVERY_DELAY]
    base = max(dated, key=lambda s: (_day(s) or date.min), default=None)
    superseding = [d for d in delays if base is not None and (_day(d) or date.min) >= (_day(base) or date.min)
                   and not d.event_date]
    derived = []
    if base is None:
        unknowns.append("no stated commitment date: a shifted date cannot be derived")
    elif superseding:
        unknowns.append(f"the date stated for {business_object} ({base.event_date.isoformat()}) was later recorded as "
                        "moved without a replacement date: there is no current date to shift")
    else:
        at = (basis.recorded_cutoff or basis.valid_at).date() if (basis.recorded_cutoff or basis.valid_at) else None
        new = base.event_date + timedelta(days=shift_days)
        derived.append({"statement": f"shifted date {new.isoformat()}",
                        "calculation": f"{base.event_date.isoformat()} + {shift_days} day(s)",
                        "evidence": [e.record_id for e in base.evidence]})
        if at:
            derived.append({"statement": f"{(new - at).days} day(s) from the assessment date {at.isoformat()} "
                                         f"(was {(base.event_date - at).days})",
                            "calculation": f"{new.isoformat()} − {at.isoformat()}", "evidence": []})
    unknowns += ["open scope and throughput are not admitted: the required rate (C1 measure) is not recomputed",
                 "no probability is given: the evidence gates of §16.4 are not met"]
    return {"scenario": "commitment_shift", "scenario_version": SCENARIO_VERSION, "business_object": business_object,
            "read_basis": {"batch_seq": basis.batch_seq, "mode": str(basis.mode), "as_of": basis.as_of,
                           "banner": basis.banner()},
            "observed": observed, "derived": derived,
            "assumptions": [f"{business_object} moves by {shift_days} day(s): a scenario input, not evidence"],
            "unknowns": list(dict.fromkeys(unknowns)), "probability": None, "ranked": False}
