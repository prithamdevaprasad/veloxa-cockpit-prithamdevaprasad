"""Findings Registry and human workflow (migration 0021): bounded, change-aware detection and notification.

- Event-triggered (`on_batch`): after a batch is sealed and projected, only items whose evidence comes from THAT batch
  are considered (no full re-analysis of the company for every event).
- Daily review (`daily_review`): every current item is re-assessed; existing findings are never duplicated
  (`dedup_key` is unique), and an unresolved finding whose premise no longer holds gets a `reassessed` event.
- A finding is created as `detected`, becomes `verified` only after its evidence is confirmed visible at the knowledge
  point, and `notified` only after recipients are resolved. A finding is NOT a verified business fact: it carries its
  evidence, its D-15 breakdown (or why it is unrankable), confidence and unknowns.
- Recipients: company readers / the CEO (by the HR record's title, a verified record) and the department's heads by
  their role tuples; if no head is established, the department QUEUE (`queue:<department>`). Never a guessed person.
- No external system (Jira, GitHub ...) is modified; owners update their own systems.
"""

from __future__ import annotations

import hashlib
import json
import uuid

import psycopg

from .risk import ranking, worry

STATES = ("detected", "verified", "notified", "acknowledged", "investigating", "resolved", "dismissed", "reassessed")
HUMAN_STATES = {"acknowledged", "investigating", "resolved", "dismissed"}
DEPT_ID = {"Engineering": "engineering", "Finance": "finance", "Marketing": "marketing", "People & Ops": "people",
           "Sales": "sales"}


def _fid(dedup: str) -> str:
    return "FD-" + hashlib.sha256(dedup.encode()).hexdigest()[:12]


def _dedup(item: ranking.Item) -> str:
    return item.item_id + "|" + hashlib.sha256(json.dumps(sorted(item.evidence)).encode()).hexdigest()[:16]


def _recipients(conn, tenant_id, department: str | None) -> list[tuple[str, str]]:
    out = []
    ceo = conn.execute("SELECT emp_id FROM projection.person WHERE tenant_id = %s AND title = 'CEO'"
                       " ORDER BY batch_seq DESC LIMIT 1", (tenant_id,)).fetchone()
    if ceo:
        out.append((f"person:{ceo['emp_id']}", "CEO by the HR record's title (company-wide findings)"))
    dept = DEPT_ID.get(department or "", (department or "").lower() or None)
    if dept:
        heads = conn.execute(
            "SELECT DISTINCT 'person:' || subject_id AS subject FROM policy.live_tuple WHERE tenant_id = %s"
            " AND resource_type = 'department' AND resource_id = %s AND relation = 'head' AND subject_type = 'person'"
            " AND subject_relation IS NULL", (tenant_id, dept)).fetchall()
        out += [(h["subject"], f"head of {dept} (role tuple)") for h in heads]
        if not heads:
            out.append((f"queue:{dept}", "no department head is established: department queue (no person guessed)"))
    return out


def _create(conn, tenant_id, k: int, item: ranking.Item, scored: dict | None, trigger: str, actor: str) -> str | None:
    dedup = _dedup(item)
    fid = _fid(dedup)
    if conn.execute("SELECT 1 FROM runtime.finding WHERE tenant_id = %s AND dedup_key = %s",
                    (tenant_id, dedup)).fetchone():
        return None
    dept = DEPT_ID.get(item.department or "")
    conn.execute(
        "INSERT INTO runtime.finding (tenant_id, finding_id, dedup_key, title, description, department, project,"
        " trigger, evidence, ranking, confidence, unknowns, visibility, batch_seq) VALUES"
        " (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (tenant_id, fid, dedup, item.title[:200], item.follow_up or item.title, item.department, item.project, trigger,
         json.dumps(item.evidence), json.dumps(scored) if scored else None, item.confidence,
         json.dumps(item.unknowns), json.dumps({"departments": [dept] if dept else []}), k))
    conn.execute("INSERT INTO runtime.finding_event (tenant_id, finding_id, state, actor, note) VALUES"
                 " (%s,%s,'detected',%s,%s)", (tenant_id, fid, actor, f"{trigger} analysis at K={k}"))
    visible = {r["record_id"] for r in conn.execute(
        "SELECT record_id FROM ledger.source_record WHERE tenant_id = %s AND batch_seq <= %s AND record_id = ANY(%s)",
        (tenant_id, k, item.evidence)).fetchall()}
    if not item.evidence or not set(item.evidence) <= visible:
        return fid                                   # stays `detected`: evidence not confirmed, nobody notified
    conn.execute("INSERT INTO runtime.finding_event (tenant_id, finding_id, state, actor, note) VALUES"
                 " (%s,%s,'verified',%s,'every cited record is admitted and visible at this knowledge point')",
                 (tenant_id, fid, actor))
    recips = _recipients(conn, tenant_id, item.department)
    for who, basis in recips:
        conn.execute("INSERT INTO runtime.notification (tenant_id, finding_id, recipient, basis) VALUES (%s,%s,%s,%s)",
                     (tenant_id, fid, who, basis))
    if recips:
        conn.execute("INSERT INTO runtime.finding_event (tenant_id, finding_id, state, actor, note) VALUES"
                     " (%s,%s,'notified',%s,%s)", (tenant_id, fid, actor, f"{len(recips)} recipient(s)"))
    return fid


def _items(conn, tenant_id, k: int, assessed_on, policy: dict | None):
    items = worry.build_items(conn, tenant_id, k, assessed_on)
    scored: dict[str, dict] = {}
    if policy:
        out = ranking.rank(items, policy)
        scored = {x["item_id"]: {"rank": x["rank"], "tied": x["tied"], "severity": x["severity"]}
                  for x in out["ranked"]}
        scored |= {x["item_id"]: {"unrankable": x["reasons"]} for x in out["unrankable"]}
    return items, scored


def on_batch(conn: psycopg.Connection, tenant_id: uuid.UUID, k: int, assessed_on, policy: dict | None = None,
             actor: str = "findings-worker") -> list[str]:
    """Event-triggered: consider only items whose evidence includes a record from batch k."""
    items, scored = _items(conn, tenant_id, k, assessed_on, policy)
    new_records = {r["record_id"] for r in conn.execute(
        "SELECT record_id FROM ledger.source_record WHERE tenant_id = %s AND batch_seq = %s",
        (tenant_id, k)).fetchall()}
    return [f for i in items if set(i.evidence) & new_records
            if (f := _create(conn, tenant_id, k, i, scored.get(i.item_id), "event", actor))]


def daily_review(conn: psycopg.Connection, tenant_id: uuid.UUID, k: int, assessed_on, policy: dict | None = None,
                 actor: str = "findings-worker") -> dict:
    items, scored = _items(conn, tenant_id, k, assessed_on, policy)
    created = [f for i in items if (f := _create(conn, tenant_id, k, i, scored.get(i.item_id), "daily", actor))]
    current = {_dedup(i) for i in items}
    reassessed = []
    for r in conn.execute("SELECT finding_id, dedup_key, state FROM runtime.finding_current WHERE tenant_id = %s",
                          (tenant_id,)).fetchall():
        if r["dedup_key"] not in current and r["state"] not in ("resolved", "dismissed", "reassessed"):
            conn.execute("INSERT INTO runtime.finding_event (tenant_id, finding_id, state, actor, note) VALUES"
                         " (%s,%s,'reassessed',%s,'the premise is no longer supported by the current evidence')",
                         (tenant_id, r["finding_id"], actor))
            reassessed.append(r["finding_id"])
    return {"created": created, "reassessed": reassessed, "considered": len(items)}


def record_human_event(conn, tenant_id, finding_id: str, state: str, actor: str, note: str | None = None,
                       ref: str | None = None) -> None:
    """Acknowledge / investigate / resolve / dismiss (append-only, audited by the event row)."""
    if state not in HUMAN_STATES:
        raise ValueError(f"state must be one of {sorted(HUMAN_STATES)}")
    conn.execute("INSERT INTO runtime.finding_event (tenant_id, finding_id, state, actor, note, ref) VALUES"
                 " (%s,%s,%s,%s,%s,%s)", (tenant_id, finding_id, state, actor, note, ref))
