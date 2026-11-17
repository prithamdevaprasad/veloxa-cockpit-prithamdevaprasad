"""Risk lifecycle across knowledge points (ARCHITECTURE.md §16.1 step 5, §16.7, §8.4 T5; Sprint 8).

A risk's history is DERIVED, never stored: the briefing body is built at each sealed K (KNOWN_AT that K, nothing
recorded) and consecutive bodies are compared. Every event is therefore reproducible from its evidence basis.

Items followed: watchlist risks (by `risk_id`) and adverse evidence-signal items of the Today view (by business
object). For each item and K:
  opened    first K at which it appears
  changed   still present, and its evidence, measures, quantification state or unknowns differ (fields listed)
  resolved  present at the previous K, absent now (the evidence no longer supports it at this K)
  reopened  absent at the previous K after having been present
No score, weight, probability or priority is attached (D-15 is pending); the lifecycle only says what changed.
"""

from __future__ import annotations

import hashlib
import json
import uuid

import psycopg

from .. import ledger, tools
from ..temporal import Mode, ReadBasis
from . import briefing, detectors, governance

LIFECYCLE_VERSION = "risk-lifecycle/1"
_FIELDS = ("evidence", "measures", "quantification_state", "unknowns")


def items(body: dict) -> dict[str, dict]:
    """The followed items of one briefing body, keyed by a stable id, with the fields that define a change."""
    out = {}
    for r in body.get("watchlist", []):
        out[f"risk:{r['risk_id']}"] = {
            "kind": "watchlist_risk", "title": r["description"], "business_object": r["business_object"],
            "evidence": sorted(e["record_id"] for e in r["evidence"]),
            "measures": r["measures"], "quantification_state": r["quantification_state"],
            "unknowns": sorted(r["unknowns"])}
    for it in body.get("today", {}).get("items", []):
        if it["source"] == "risk_watchlist":
            continue
        out[f"signal:{it['business_object']}"] = {
            "kind": "evidence_signal", "title": it["title"], "business_object": it["business_object"],
            "evidence": sorted(it["evidence"]), "measures": {"why_shown": it["why_shown"]},
            "quantification_state": "not_applicable", "unknowns": sorted(it["unknowns"])}
    return out


def _digest(v) -> str:
    return hashlib.sha256(json.dumps(v, sort_keys=True, default=str).encode()).hexdigest()[:16]


def history(bodies: list[tuple[int, dict]]) -> list[dict]:
    """Pure. `bodies`: (K, briefing body) in ascending K. Returns one record per item with its events."""
    seen: dict[str, dict] = {}
    prev: dict[str, dict] = {}
    for k, body in bodies:
        cur = items(body)
        for key in sorted(set(cur) | set(prev) | {s for s in seen if seen[s]["state"] == "resolved"}):
            rec = seen.setdefault(key, {"item_id": key, "events": [], "state": None})
            now, before = cur.get(key), prev.get(key)
            if now and not before:
                ev = "reopened" if rec["state"] == "resolved" else "opened"
                rec["events"].append({"batch_seq": k, "event": ev,
                                      "fingerprint": _digest({f: now[f] for f in _FIELDS})})
                rec["state"] = "open"
            elif before and not now:
                rec["events"].append({"batch_seq": k, "event": "resolved",
                                      "reason": "no longer supported by the evidence visible at this K"})
                rec["state"] = "resolved"
            elif now and before:
                changed = [f for f in _FIELDS if _digest(now[f]) != _digest(before[f])]
                if changed:
                    rec["events"].append({"batch_seq": k, "event": "changed", "fields": changed,
                                          "fingerprint": _digest({f: now[f] for f in _FIELDS})})
            if now:
                rec.update({"kind": now["kind"], "title": now["title"], "business_object": now["business_object"],
                            "evidence": now["evidence"], "unknowns": now["unknowns"]})
        prev = cur
    return [r for r in sorted(seen.values(), key=lambda r: r["item_id"]) if r["events"]]


def compute(conn: psycopg.Connection, tenant_id: uuid.UUID, principal: tools.Principal,
            mappings: list | None = None) -> dict:
    """Build the briefing at every sealed K (nothing is recorded) and derive the lifecycle. `mappings` defaults to
    the owner-approved figure mappings (a code parameter for tests, never request input)."""
    policy = governance.current_policy(conn, tenant_id)
    mappings = detectors.load_mappings() if mappings is None else mappings
    releases = sorted(ledger.sealed_releases(conn, tenant_id))
    ks = [k for k, _ in releases]
    bodies = []
    for k, released_at in releases:                     # the same basis KNOWN_AT(release of K) resolves to
        basis = ReadBasis(tenant_id, k, Mode.KNOWN_AT, released_at, released_at, released_at.isoformat())
        bodies.append((k, briefing.build(conn, principal, basis, mappings, policy)))
    return {"lifecycle_version": LIFECYCLE_VERSION, "engine_version": governance.ENGINE_VERSION,
            "knowledge_points": ks, "ranked": False, "items": history(bodies)}
