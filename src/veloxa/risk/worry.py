"""'What should I worry about?': D-15 v1 items built deterministically from admitted claims at knowledge point K.

Each item states, per criterion, measured (an interval), not applicable (with the D-15 rule that makes it so), or
missing/unmapped (never zero). Nothing is invented: an item exists only when admitted records support its premise.

Item builders:
- C1 documented slip: a Jira version whose release date was moved from its original release date (Versions sheet).
  C1 = slip days (fallback measure; Q7: no supported throughput). C3 = not applicable (D15-02: commitment-work
  concentration tied to a C1 risk is a driver, not a C3 score). C4 = not applicable (no reported figure involved).
  C2 = missing (no contract or revenue record ties an amount to the slip).
- C3 on-call concentration: the share of the trailing 8 rota weeks held by the most-loaded primary responder. Rota
  presence is not qualification (Q9 Option A): the interval is [share if every rostered person is qualified,
  1.0 if only that person is]. C1 = not applicable (D15-02), C4 = not applicable, C2 = missing.
- C4 figure disagreement: an open Conflict Registry entry on a metric. No figure mapping or materiality is approved
  (D15-03a), so C4 = unmapped (missing). C1 and C3 = not applicable; C2 = missing.
- Evidence without an approved measure (hiring on hold, an employee in their notice period, incidents): listed so
  they stay visible, but every criterion is missing, so the eligibility gate keeps them UNRANKABLE with the reason.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import date, timedelta

import psycopg

from .ranking import Item, Measure

NA_D1502 = "not applicable: D15-02 (commitment-work concentration tied to a delivery risk is a driver, not C3)"


def _claims(conn, tenant_id, k, stype):
    rows = conn.execute(
        "SELECT subject, predicate, object, record_id, valid_at FROM knowledge.claim WHERE tenant_id = %s"
        " AND subject_type = %s AND batch_seq <= %s ORDER BY batch_seq, claim_id", (tenant_id, stype, k)).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        d = out.setdefault(r["subject"], {"_records": set()})
        d[r["predicate"]] = r["object"]
        d["_records"].add(r["record_id"])
    return out


def _day(s) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def build_items(conn: psycopg.Connection, tenant_id: uuid.UUID, k: int, assessed_on: date) -> list[Item]:
    items: list[Item] = []
    # C1: documented version slips
    for subj, d in _claims(conn, tenant_id, k, "version").items():
        new, orig = _day(d.get("release_date")), _day(d.get("original_release_date"))
        if not new or not orig or new <= orig:
            continue
        slip = (new - orig).days
        name = subj.split(":", 1)[1]
        items.append(Item(
            f"slip:{name}", f"Release {name} moved {slip} days later than originally planned",
            {"C1": Measure("measured", slip, slip, "C1.documented_slip_days@1",
                           f"release date moved from {orig.isoformat()} to {new.isoformat()}"),
             "C2": Measure("missing", reason="no contract or revenue record ties an amount to this slip"),
             "C3": Measure("not_applicable", reason=NA_D1502),
             "C4": Measure("not_applicable", reason="no reported figure is involved")},
            sorted(d["_records"]), confidence="high", department="Engineering", project=name,
            unknowns=["Why the date moved is not stated in the version record.",
                      "Whether remaining scope fits the new date is unknown."],
            follow_up=f"Ask the release owner what changed for {name} and whether the new date is firm."))
    # C3: on-call concentration over the trailing 8 rota weeks
    weeks = sorted(((_day(s.split(":", 1)[1]), d) for s, d in _claims(conn, tenant_id, k, "rota_week").items()),
                   key=lambda x: x[0] or date.min)
    window = [(w, d) for w, d in weeks if w and assessed_on - timedelta(weeks=8) < w <= assessed_on][-8:]
    if not window and weeks:
        window = [(w, d) for w, d in weeks if w][-8:]
    if window:
        prim = Counter(d.get("primary_source_id") for _, d in window if d.get("primary_source_id"))
        if prim:
            top, n = prim.most_common(1)[0]
            share = n / len(window)
            recs = sorted({r for _, d in window for r in d["_records"]})
            items.append(Item(
                "oncall:primary", f"Primary on-call held by one person in {n} of the last {len(window)} rota weeks",
                {"C3": Measure("measured", share, 1.0, "C3.oncall_concentration@1",
                               "the records don't show who else is qualified to cover"),
                 "C1": Measure("not_applicable", reason=NA_D1502.replace("a delivery risk", "a delivery risk;"
                                                                       " C1 is delivery exposure")),
                 "C2": Measure("missing", reason="no outage cost is recorded (D15-25)"),
                 "C4": Measure("not_applicable", reason="no reported figure is involved")},
                recs, confidence="moderate", department="Engineering", project="On-call rota",
                unknowns=["Who is qualified to respond is not documented (no qualification records).",
                          "Coverage adequacy is a different question from concentration (record §4B.3)."],
                follow_up="Confirm which engineers are qualified for primary on-call and add a documented backup."))
    # C4: open figure disagreements in the Conflict Registry
    for cf in conn.execute(
            "SELECT c.conflict_id, c.subject, c.claim_ids FROM knowledge.conflict c WHERE c.tenant_id = %s"
            " AND c.batch_seq <= %s AND c.resolution = 'needs_review' AND c.subject LIKE 'metric:%%'"
            " AND NOT EXISTS (SELECT 1 FROM knowledge.conflict_decision d WHERE d.tenant_id = c.tenant_id"
            " AND d.conflict_id = c.conflict_id AND d.preferred_claim_id IS NOT NULL)", (tenant_id, k)).fetchall():
        recs = [r["record_id"] for r in conn.execute(
            "SELECT DISTINCT record_id FROM knowledge.claim WHERE tenant_id = %s AND claim_id = ANY(%s)",
            (tenant_id, cf["claim_ids"])).fetchall()]
        label = cf["subject"].split(":", 1)[1].upper()
        items.append(Item(
            f"figure:{cf['conflict_id']}", f"Reported {label} figures disagree between sources",
            {"C4": Measure("unmapped", reason="no owner-approved figure mapping or materiality (D15-03a)"),
             "C1": Measure("not_applicable", reason="not a delivery commitment"),
             "C3": Measure("not_applicable", reason="not a service-continuity matter"),
             "C2": Measure("missing", reason="the financial consequence of the discrepancy is not recorded")},
            recs, confidence="conflicting", department="Finance", project=None,
            unknowns=["Which figure is right is unresolved: no precedence rule covers board vs spoken figures.",
                      "Whether the sources use the same definition is not confirmed."],
            follow_up=f"Ask Finance to confirm the {label} definition and the figure the board should rely on."))
    # Evidence without an approved measure: visible, never ranked
    for subj, d in _claims(conn, tenant_id, k, "requisition").items():
        if str(d.get("status", "")).lower() in ("on hold", "paused", "frozen"):
            items.append(Item(f"hiring:{subj.split(':', 1)[1]}", f"Hiring for {d.get('role', 'a role')} is on hold",
                              {}, sorted(d["_records"]), confidence="high", department=str(d.get("team") or ""),
                              unknowns=["The records don't say what this pause delays."],
                              follow_up="Ask the hiring manager what the pause delays."))
    for subj, d in _claims(conn, tenant_id, k, "person").items():
        if str(d.get("employment_status", "")).lower() == "notice period":
            title = d.get("title", "team member")
            items.append(Item(f"notice:{subj.split(':', 1)[1]}", f"A {title} is in their notice period", {},
                              sorted(d["_records"]), confidence="high", department=str(d.get("department")),
                              unknowns=["Handover plans and the work affected are not recorded."],
                              follow_up="Ask the department head for the handover plan."))
    return items
