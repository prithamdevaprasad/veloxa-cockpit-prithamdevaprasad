"""Interim Today briefing (ARCHITECTURE.md §16.1, §19.3 `GET /v1/briefing`, journey J1) while D-15 is pending.

The briefing is an UNRANKED watchlist:
- risks from the detectors, each with evidence, time context, criterion values (intervals) and unknowns;
- the unweighted partial-order comparison (compare.py): which risks demonstrably dominate which, which trade off,
  which cannot be compared, and why;
- `top_three` is null: a ranked shortlist needs the owner's D-15 weights and guardrail tiers;
- display order is risk_id ascending, a stable TECHNICAL order that carries no priority.

The briefing also carries `evidence_signals`: the C1-C4 detector observations (src/veloxa/signals), grouped by
criterion with each signal's status (active, provisional, unquantified, unmapped, blocked or missing evidence). They
are NOT risks, scores or priorities, they are never sorted by importance, and a criterion with nothing measurable
says so explicitly. The detector run is recorded separately (migration 0006) and referenced from the snapshot.

Every briefing is recorded as an append-only snapshot (migration 0005) with its read basis, engine version, the
D-15 policy state/version/hash in force and an input hash (§16.1 step 5). An identical request at the same basis
and policy returns the same snapshot; a later policy produces a new snapshot and never rewrites an earlier one.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime

import psycopg

from .. import audit, tools
from ..db import tenant_tx
from ..signals import registry
from ..signals.contract import Status
from ..temporal import ReadBasis
from . import detectors, governance
from .compare import partial_order
from .model import CRITERIA, MEASURED, Risk

DISPLAY_ORDER = "risk_id ascending: a stable technical order for display only; it carries NO priority"
TOP_THREE_EXPLANATION = (
    "No ranked shortlist is produced (top_three is null). Ranking risks needs the owner's D-15 decision "
    "(weights, anchors, guardrail "
    "tiers, gate thresholds), which is pending. Until it is recorded, risks are shown as an unranked watchlist with "
    "a weight-free comparison: a risk is reported as dominating another only when it is demonstrably at least as "
    "bad on every applicable criterion and worse on at least one.")
COMPARISON_BASIS = {
    "method": "unweighted interval dominance (Pareto) over the approved impact criteria",
    "rule": "A dominates B only if every criterion that applies to either risk is measured for both with the "
            "identical measure definition, A's interval is at least as bad as B's on each, and strictly worse on "
            "at least one. Missing, uncertain, one-sided or differently defined measures make a pair not "
            "comparable; they are never read as zero or favourable.",
    "groups": "'not dominated' and 'dominated' describe this comparison only. They are NOT approved priority "
              "tiers, and the order within a group means nothing.",
    "not_used_pending_d15": ["weights", "anchors", "thresholds", "guardrail tiers", "tie-breaks",
                             "numeric scores"],
    "source": "ARCHITECTURE.md §16.2-§16.5 (structure approved, DEC-18 / ADR-14); D-15 values pending",
}


def _measure_json(m) -> dict:
    out = {"state": m.state, "note": m.note or None}
    if m.state == MEASURED:
        out |= {"definition": m.definition.ref, "unit": m.definition.unit,
                "definition_text": m.definition.description, "interval": [m.low, m.high],
                "evidence": list(m.evidence)}
    return out


SIGNALS_EXPLANATION = (
    "Evidence observations from the C1-C4 detectors. They are not risks, scores or priorities, and their order "
    "(criterion, then signal type, then subject) is technical only. Each says how far its criterion can be "
    "established today; no number is shown where the approved rules do not support one.")
STATUS_LEGEND = {
    Status.ACTIVE.value: "evidence-backed observation under approved rules",
    Status.PROVISIONAL.value: "evidence-backed, but its definition or source quality awaits an owner decision",
    Status.UNQUANTIFIED.value: "valid evidence; no number is supported under the approved rules",
    Status.UNMAPPED.value: "needs an owner-approved mapping before any comparison",
    Status.BLOCKED.value: "needs an owner decision before it can be measured",
    Status.MISSING.value: "the evidence the measure needs is not admitted (never read as zero)",
}


TODAY_LIMIT = 3
TODAY_RULE = (
    "Shown without a question (voice note T6a). An item appears only if the evidence itself supports attention: a "
    "risk that no other risk demonstrably dominates (unweighted comparison), or an active, formally evidenced adverse "
    "signal (a recorded delivery delay or delayed status, a confirmed figure discrepancy, a confirmed absence of "
    "continuity coverage), grouped by business object. At most three. Items are NOT ranked: choosing three out of "
    "more needs the owner's D-15 decision, so in that case none is picked and every candidate is named instead.")


def _adverse(sig: dict) -> bool:
    if sig["status"] != "active":
        return False
    t, cat = sig["signal_type"], (sig["value"] or {}).get("category") or ""
    if t == "delivery_delay" or (t == "commitment_status" and cat.startswith(("delayed", "conflicting"))):
        return True
    if t == "figure_discrepancy":
        return True
    return t == "continuity_evidence" and "confirmed_absence" in sig["flags"]


def today_section(body: dict) -> dict:
    """At most three evidence-backed attention items, derived only from the briefing body (pure, deterministic)."""
    candidates: dict[str, dict] = {}
    for r in body["watchlist"]:
        if r["comparison"]["group"] == "not_dominated":
            candidates[f"risk:{r['risk_id']}"] = {
                "business_object": r["business_object"], "source": "risk_watchlist", "title": r["description"],
                "why_shown": "no other risk demonstrably dominates it on the approved criteria",
                "evidence": [e["record_id"] for e in r["evidence"]], "unknowns": r["unknowns"],
                "time_context": r["time_context"]}
    for crit, sec in body["evidence_signals"]["by_criterion"].items():
        for s in sec["signals"]:
            if not _adverse(s):
                continue
            key = f"{crit}:{s['subject']}"
            item = candidates.setdefault(key, {
                "business_object": s["subject"], "source": f"{crit} evidence signals", "title": None,
                "why_shown": None, "evidence": [], "unknowns": [], "time_context": {}})
            facts = "; ".join(s["observed"]) if s["observed"] else s["signal_type"]
            if s["signal_type"] == "commitment_status" or item["title"] is None:
                item["title"] = f"{s['subject']}: {(s['value'] or {}).get('category', s['signal_type'])}"
                item["why_shown"] = f"{crit} {s['signal_type'].replace('_', ' ')} ({s['status']}): {facts}"
            item["evidence"] = sorted(set(item["evidence"]) | {e["record_id"] for e in s["evidence_records"]})
            item["unknowns"] = list(dict.fromkeys(item["unknowns"] + list(s["missing"])))
            item["time_context"] = {k: v for k, v in (("recorded_on", s["recorded_on"]),
                                                      ("event_date", s["event_date"])) if v} or item["time_context"]
    items = [v for _, v in sorted(candidates.items())]
    unmeasurable = [f"{c}: {sec['measurability']}" for c, sec in body["evidence_signals"]["by_criterion"].items()
                    if not any(_adverse(s) for s in sec["signals"])]
    if len(items) > TODAY_LIMIT:
        return {"items": [], "ranked": False, "rule": TODAY_RULE, "candidate_count": len(items),
                "candidates": [i["business_object"] for i in items],
                "explanation": f"{len(items)} items qualify. Choosing the top three needs the owner's D-15 ranking "
                               "decision, which is not recorded, so none is picked; all candidates are named.",
                "not_shown_because_unmeasurable": unmeasurable}
    return {"items": items, "ranked": False, "rule": TODAY_RULE, "candidate_count": len(items),
            "explanation": ("Every qualifying item is shown." if items else "No item qualifies on current evidence.")
            + (" Fewer than three: the evidence supports no more, and nothing is added to fill the list."
               if len(items) < TODAY_LIMIT else ""),
            "not_shown_because_unmeasurable": unmeasurable}



def tenant_time_zone(conn: psycopg.Connection, tenant_id) -> str:
    with tenant_tx(conn, tenant_id):
        row = conn.execute("SELECT time_zone FROM directory.tenant WHERE tenant_id = %s", (tenant_id,)).fetchone()
    return row["time_zone"]


def run_detectors(conn: psycopg.Connection, basis: ReadBasis, mappings: list[detectors.FigureMapping],
                  ) -> registry.RunResult:
    return registry.run(conn, basis, registry.context_for(basis, tenant_time_zone(conn, basis.tenant_id),
                                                          mappings))


def _signals_section(conn: psycopg.Connection, basis: ReadBasis, result: registry.RunResult) -> dict:
    ids = sorted({rid for s in result.signals for rid in s.record_ids})
    records = {e.record_id: {"record_id": e.record_id, "kind": e.kind, "system": e.system,
                             "data_class": e.data_class, "batch_seq": e.batch_seq, "source_path": e.source_path}
               for e in tools._evidence(conn, basis, ids)}
    by: dict[str, dict] = {}
    for c in ("C1", "C2", "C3", "C4", "C5"):
        sigs = [s for s in result.signals if s.criterion.value == c]
        counts: dict[str, int] = {}
        for s in sigs:
            counts[s.status.value] = counts.get(s.status.value, 0) + 1
        by[c] = {
            "measurability": registry.MEASURABILITY[c],
            "counts": counts,
            "signals": [s.body() | {"signal_id": s.signal_id,
                                    "evidence_records": [records[r] for r in s.record_ids if r in records]}
                        for s in sigs],
            "withheld": [w.reason for w in result.withheld if w.criterion.value == c],
        }
    return {"explanation": SIGNALS_EXPLANATION, "status_legend": STATUS_LEGEND,
            "assessment_date": result.assessment_date.isoformat(),
            "detectors": [{"detector": d.detector_id, "criterion": d.criterion.value,
                           "approved_basis": d.approved_basis, "owner_decisions_pending": list(d.pending)}
                          for d in registry.REGISTRY],
            "config_sha256": result.config_sha256, "output_sha256": result.output_sha256, "by_criterion": by}


def build(conn: psycopg.Connection, principal: tools.Principal, basis: ReadBasis,
          mappings: list[detectors.FigureMapping], policy: governance.PolicyState,
          run_result: registry.RunResult | None = None) -> dict:
    """The briefing body for one read basis (not yet recorded)."""
    run_result = run_result if run_result is not None else run_detectors(conn, basis, mappings)
    risks: list[Risk] = []
    notes: list[str] = []
    if basis.batch_seq == 0:
        notes.append("nothing had been admitted at this knowledge point")
    else:
        found, notes = detectors.reported_vs_system(conn, basis, mappings)
        risks += found
    # Evidence: re-read through the visibility view at K; a risk whose evidence is not all visible is dropped.
    shown: list[Risk] = []
    evidence: dict[str, dict] = {}
    for r in risks:
        rows = {e.record_id: e for e in tools._evidence(conn, basis, r.evidence)}
        if set(r.evidence) - set(rows) or any(e.data_class in ("sensitive_personal", "payroll_aggregate")
                                              for e in rows.values()):
            notes.append(f"{r.risk_id}: dropped, because part of its evidence is not visible at this basis")
            continue
        shown.append(r)
        for e in rows.values():
            evidence[e.record_id] = {"record_id": e.record_id, "kind": e.kind, "system": e.system,
                                     "data_class": e.data_class, "batch_seq": e.batch_seq,
                                     "source_path": e.source_path, "locator": e.locator}
    insufficient = [r for r in shown if r.quantification_state == "insufficient"]
    order = partial_order(shown)
    per = order["per_risk"]
    watch = []
    for r in sorted((r for r in shown if r not in insufficient), key=lambda x: x.risk_id):
        rel = per[r.risk_id]
        group = "dominated" if rel["dominated_by"] else "not_dominated"
        watch.append({
            "risk_id": r.risk_id, "description": r.description, "detector": r.detector,
            "business_object": r.business_object, "quantification_state": r.quantification_state,
            "evidence": [evidence[i] for i in r.evidence],
            "time_context": {**r.time_context, "read_basis": basis.banner()},
            "measures": {cid: _measure_json(m) for cid, m in r.measures.items()},
            "unknowns": r.unknowns + [f"{cid} {m.state.replace('_', ' ')}: {m.note}" for cid, m in r.measures.items()
                                      if m.state not in (MEASURED, "not_applicable")],
            "comparison": {
                "group": group, **rel,
                "explanation": ("Demonstrably dominated by " + ", ".join(rel["dominated_by"]) + "."
                                if rel["dominated_by"] else
                                "Not dominated by any other risk on current evidence. This is not a priority tier."),
            },
        })
    needing = list(dict.fromkeys(notes + [f"{c}: {registry.MEASURABILITY[c]}" for c in ("C1", "C2", "C3", "C4",
                                                                                        "C5")]
                                 + [f"{r.risk_id}: insufficient (no criterion measured)" for r in insufficient]))
    body = {
        "briefing_kind": "interim_unranked_watchlist",
        "ranking_mode": governance.ranking_mode(policy),
        "owner_decision_status": governance.owner_decision_status(policy),
        "policy": policy.as_json(),
        "engine_version": governance.ENGINE_VERSION,
        "top_three": None,
        "top_three_explanation": TOP_THREE_EXPLANATION,
        "comparison_basis": COMPARISON_BASIS | {"criteria": [
            {"criterion": c.criterion_id, "name": c.name, "natural_scale": c.natural_scale,
             "worse_when": c.worse_when} for c in CRITERIA.values()]},
        "display_order": DISPLAY_ORDER,
        "watchlist": watch,
        "comparisons": [{"a": p.a, "b": p.b, "relation": p.relation, "explanation": p.explanation,
                         "criteria": p.criteria} for p in order["pairs"]],
        "signals_needing_data": needing,
        "evidence_signals": _signals_section(conn, basis, run_result),
        "read_basis": {"batch_seq": basis.batch_seq, "mode": str(basis.mode), "as_of": basis.as_of,
                       "banner": basis.banner(), "notes": list(basis.notes)},
    }
    body["today"] = today_section(body)
    return body


def _hash(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def serve(conn: psycopg.Connection, tenant_id: uuid.UUID, principal: tools.Principal, basis: ReadBasis,
          now: datetime, mappings: list[detectors.FigureMapping] | None = None) -> dict:
    """Build the briefing and record it as a snapshot (or return the identical existing snapshot)."""
    policy = governance.current_policy(conn, tenant_id)
    mappings = detectors.load_mappings() if mappings is None else mappings
    result = run_detectors(conn, basis, mappings)
    run_ref = registry.record(conn, result, now) if basis.batch_seq > 0 else None
    body = build(conn, principal, basis, mappings, policy, run_result=result)
    if body["ranking_mode"] != "unweighted_partial_order":  # cannot happen in this engine version; fail closed
        governance.weighted_ranking(policy, [])
    input_hash = _hash(body)
    key = (tenant_id, principal.principal_id, basis.batch_seq, str(basis.mode), basis.as_of or "",
           governance.ENGINE_VERSION, policy.version or 0, input_hash)
    snapshot_id = str(uuid.uuid4())
    payload = {**body, "snapshot_id": snapshot_id, "created_at": now.isoformat(), "input_hash": input_hash,
               "detector_run": run_ref}
    with tenant_tx(conn, tenant_id, principal.principal_id, basis.batch_seq):
        inserted = conn.execute(
            "INSERT INTO runtime.briefing_snapshot (tenant_id, snapshot_id, created_at, principal_id, batch_seq, mode,"
            " as_of, engine_version, ranking_mode, policy_status, policy_version, policy_sha256, input_hash, payload)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (tenant_id, snapshot_id, now, principal.principal_id, basis.batch_seq, str(basis.mode), basis.as_of,
             governance.ENGINE_VERSION, body["ranking_mode"], policy.status, policy.version, policy.sha256,
             input_hash, json.dumps(payload, default=str))).rowcount
        if inserted:
            audit.append(conn, tenant_id, "briefing.snapshot", {
                "snapshot_id": snapshot_id, "batch_seq": basis.batch_seq, "mode": str(basis.mode),
                "ranking_mode": body["ranking_mode"], "policy_status": policy.status,
                "policy_version": policy.version, "risks": len(body["watchlist"])})
            return json.loads(json.dumps(payload, default=str))
        row = conn.execute(
            "SELECT payload FROM runtime.briefing_snapshot WHERE tenant_id = %s AND principal_id = %s"
            " AND batch_seq = %s AND mode = %s AND coalesce(as_of, '') = %s AND engine_version = %s"
            " AND coalesce(policy_version, 0) = %s AND input_hash = %s", key).fetchone()
    return row["payload"]


def fetch(conn: psycopg.Connection, tenant_id: uuid.UUID, principal: tools.Principal, snapshot_id: str) -> dict | None:
    """A recorded briefing exactly as it was served, to the principal it was served to."""
    with tenant_tx(conn, tenant_id, principal.principal_id):
        row = conn.execute("SELECT payload FROM runtime.briefing_snapshot WHERE tenant_id = %s AND snapshot_id = %s"
                           " AND principal_id = %s", (tenant_id, snapshot_id, principal.principal_id)).fetchone()
    return row["payload"] if row else None
