"""Evidence dependency graph and early warnings (ARCHITECTURE.md §16.2, §16.7; Sprint 8). Derived, never stored.

**What the graph is built from:**
- Only the C1–C4 detector signals at one read basis (K).
- Each signal survives only if ALL its cited records are visible through the evidence view at K and none is
  sensitive personal or payroll data. This is the same gate the briefing uses. A signal that fails the gate is
  dropped and noted, never shown in part.

**Nodes:**
- `commitment`: a named C1 business object. Unresolved subjects are never nodes.
- `record`: an admitted source record that a surviving signal cites.

**Edges:**
- `stated_in`: commitment → record. Carries the signal type and status.
- `conflict`: record ↔ record, from a C4 figure discrepancy.

**Warnings.** Each warning is a fact about the evidence, with its records:
- `no_stated_date`: no statement gives the commitment a date.
- `moved_without_replacement_date`: a delay recorded on or after the latest dated statement carries no new date.
- `single_source`: every statement about the commitment comes from one record.
- `shared_evidence`: one record supports several commitments, so a change to it affects them all.
- `conflicted_evidence`: a record the commitment rests on is in a figure conflict.

There is no score, weight, threshold, probability or ranking (D-15 is pending). The Jira, rota and incident
graph waits for the D3 admission.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

import psycopg

from .. import tools
from ..signals import registry
from ..signals.contract import Criterion, SignalType
from ..temporal import ReadBasis

GRAPH_VERSION = "evidence-graph/1"
HIDDEN = ("sensitive_personal", "payroll_aggregate")
C1_TYPES = (SignalType.COMMITMENT, SignalType.COMMITMENT_STATUS, SignalType.DELIVERY_DELAY)


def _day(s) -> date:
    return s.recorded_on or s.valid_from or date.min


def build(conn: psycopg.Connection, basis: ReadBasis, tz: str) -> dict:
    notes: list[str] = []
    signals = []
    if basis.batch_seq:
        result = registry.run(conn, basis, registry.context_for(basis, tz, [], []))
        for s in result.signals:
            ids = [e.record_id for e in s.evidence]
            rows = {e.record_id: e for e in tools._evidence(conn, basis, ids)} if ids else {}
            if set(ids) - set(rows) or any(e.data_class in HIDDEN for e in rows.values()):
                notes.append(f"a {s.criterion} signal was dropped: part of its evidence is not visible at this basis")
                continue
            signals.append((s, rows))
    else:
        notes.append("nothing had been admitted at this knowledge point")

    by_commitment: dict[str, list] = defaultdict(list)
    records: dict[str, dict] = {}
    edges, conflicts = [], []
    for s, rows in signals:
        for e in rows.values():
            records.setdefault(e.record_id, {"id": e.record_id, "type": "record", "kind": e.kind,
                                             "system": e.system, "data_class": e.data_class,
                                             "batch_seq": e.batch_seq, "source_path": e.source_path})
        if s.criterion == Criterion.C1 and s.signal_type in C1_TYPES and s.subject and s.subject != "unresolved":
            by_commitment[s.subject].append(s)
            for rid in rows:
                edges.append({"from": f"commitment:{s.subject}", "to": rid, "relation": "stated_in",
                              "signal_type": str(s.signal_type), "status": str(s.status),
                              "stated_date": s.event_date.isoformat() if s.event_date else None})
        elif s.signal_type == SignalType.FIGURE_DISCREPANCY and len(rows) >= 2:
            a, b = sorted(rows)[:2]
            conflicts.append({"from": a, "to": b, "relation": "conflict", "subject": s.subject})

    in_conflict = {c["from"] for c in conflicts} | {c["to"] for c in conflicts}
    supports: dict[str, set[str]] = defaultdict(set)
    for e in edges:
        supports[e["to"]].add(e["from"].removeprefix("commitment:"))

    warnings = []
    for name in sorted(by_commitment):
        ss = by_commitment[name]
        cited = sorted({e.record_id for s in ss for e in s.evidence})
        dated = [s for s in ss if s.signal_type == SignalType.COMMITMENT and s.event_date]
        latest = max(dated, key=_day, default=None)
        delays = [s for s in ss if s.signal_type == SignalType.DELIVERY_DELAY and not s.event_date]

        def warn(kind, recs, text, _name=name):
            warnings.append({"kind": kind, "commitment": _name, "statement": text, "evidence": sorted(recs)})
        if not dated:
            warn("no_stated_date", cited, f"No admitted statement gives {name} a date.")
        elif any(_day(d) >= _day(latest) for d in delays):
            moved = [e.record_id for d in delays if _day(d) >= _day(latest) for e in d.evidence]
            warn("moved_without_replacement_date", moved + [e.record_id for e in latest.evidence],
                 f"{name} was stated for {latest.event_date.isoformat()} and later recorded as moved, "
                 "with no replacement date.")
        if len(cited) == 1:
            warn("single_source", cited, f"Every admitted statement about {name} comes from one record.")
        shared = [r for r in cited if len(supports[r]) > 1]
        if shared:
            others = sorted(set().union(*(supports[r] for r in shared)) - {name})
            warn("shared_evidence", shared, f"{name} rests on evidence that also supports: {', '.join(others)}.")
        if in_conflict & set(cited):
            warn("conflicted_evidence", sorted(in_conflict & set(cited)),
                 f"{name} rests on a record that is in an open figure conflict.")

    nodes = [{"id": f"commitment:{n}", "type": "commitment", "label": n} for n in sorted(by_commitment)]
    nodes += [records[r] for r in sorted(records)]
    return {"graph_version": GRAPH_VERSION, "nodes": nodes, "edges": edges + conflicts, "warnings": warnings,
            "ranked": False, "probability": None, "notes": list(dict.fromkeys(notes)),
            "unknowns": ["owners, dependencies between commitments, services and on-call coverage are not in the "
                         "admitted evidence (the D3 sources are not admitted)"],
            "read_basis": {"batch_seq": basis.batch_seq, "mode": str(basis.mode), "as_of": basis.as_of,
                           "banner": basis.banner()}}
