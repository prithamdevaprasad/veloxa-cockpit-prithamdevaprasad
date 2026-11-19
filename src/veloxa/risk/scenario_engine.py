"""What-if scenarios over every release and named commitment in the admitted evidence (read-only, isolated).

- Subjects are discovered at the read basis: releases from structured tracker/release records (with their recorded
  date and original date) and named commitments from the C1 delivery signals. Nothing is hard-coded.
- A scenario is one or more changes ("move Orbit Core 3.0 by +21 days and Pulse 1.5 by -5 days"), given as
  structured changes or free text. For each change: the baseline, the assumption, the new date, the open and blocked
  tracker issues that target the subject, the recorded dependencies (what blocks it), and the calculations the
  records support (days moved, total distance from the original date, order against other changed subjects).
- No probability, ranking or forecast. Nothing is written: not to company records, not to Jira.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, timedelta

from ..db import tenant_tx
from .graph import UNNAMED

MAX_SHIFT_DAYS = 730
_UNIT = {"day": 1, "days": 1, "week": 7, "weeks": 7, "month": 30, "months": 30}


class ScenarioError(ValueError):
    pass


def _claims(conn, tenant_id, k, stype):
    out: dict[str, dict] = {}
    for r in conn.execute("SELECT subject, predicate, object, record_id FROM knowledge.claim WHERE tenant_id = %s"
                          " AND subject_type = %s AND batch_seq <= %s ORDER BY batch_seq, claim_id",
                          (tenant_id, stype, k)).fetchall():
        d = out.setdefault(r["subject"], {"_records": set()})
        d[r["predicate"]] = r["object"]
        d["_records"].add(r["record_id"])
    return out


def _day(v) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _human(d: date | None) -> str:
    return f"{d.day} {d.strftime('%B %Y')}" if d else "no recorded date"


def subjects(conn, basis, tz: str) -> list[dict]:
    """Every release and named commitment at the basis, with what the records say about its date."""
    k = basis.batch_seq
    with tenant_tx(conn, basis.tenant_id, batch_seq=k):
        versions = _claims(conn, basis.tenant_id, k, "version")
    out = {}
    for s, d in versions.items():
        name = s.split(":", 1)[1]
        out[name.lower()] = {"name": name, "kind": "release", "date": d.get("release_date"),
                             "original_date": d.get("original_release_date"), "project": d.get("project"),
                             "status": d.get("release_status"), "evidence": sorted(d["_records"])}
    from . import scenarios

    for c in scenarios.commitments(conn, basis, tz):
        if c in UNNAMED or any(c.lower() in n or n.startswith(c.lower()) for n in out):
            continue                                  # "Atlas" is already covered by the release "Atlas GA"
        out[c.lower()] = {"name": c, "kind": "commitment", "date": None, "original_date": None, "project": None,
                          "status": None, "evidence": []}
    return sorted(out.values(), key=lambda x: (x["kind"], x["name"].lower()))


def parse(text: str, names: list[str]) -> list[dict]:
    """Free text -> changes. Each change names a known subject and a signed shift ("by 3 weeks", "earlier by 5
    days", "-10 days", "a month later"). Unknown subjects are never invented."""
    low = text.lower()
    changes = []
    firsts = [n.split()[0].lower() for n in names]
    for name in sorted(names, key=len, reverse=True):
        i, used = low.find(name.lower()), name
        first = name.split()[0]
        if i < 0 and len(first) >= 3 and firsts.count(first.lower()) == 1:      # "Atlas" for "Atlas GA" if unique
            m0 = re.search(rf"\b{re.escape(first.lower())}\b", low)
            i, used = (m0.start(), first) if m0 else (-1, name)
        if i < 0 or any(c["subject"].lower() in name.lower() or name.lower() in c["subject"].lower()
                        for c in changes):
            continue
        tail = low[i + len(used): i + len(used) + 60]
        m = re.search(r"(earlier|sooner|forward|back|later|delay(?:ed)?|slip(?:s|ped)?)?\s*(?:by\s+)?"
                      r"([+-]?\d+|a|an|one)\s*(day|days|week|weeks|month|months)\b\s*"
                      r"(earlier|sooner|later|forward|back)?", tail)
        if not m:
            continue
        n = 1 if m.group(2) in ("a", "an", "one") else int(m.group(2))
        days = n * _UNIT[m.group(3)]
        direction = (m.group(1) or "") + " " + (m.group(4) or "")
        if re.search(r"earlier|sooner|forward", direction) and days > 0:
            days = -days
        changes.append({"subject": name, "shift_days": days})
    return changes


def run(conn, basis, tz: str, changes: list[dict]) -> dict:
    if not changes:
        raise ScenarioError("no change was recognised; name a release or commitment and a shift, e.g. "
                            "“move Orbit Core 3.0 by 3 weeks”")
    if len(changes) > 6:
        raise ScenarioError("at most six simultaneous changes")
    known = {s["name"].lower(): s for s in subjects(conn, basis, tz)}
    k = basis.batch_seq
    with tenant_tx(conn, basis.tenant_id, batch_seq=k):
        issues = _claims(conn, basis.tenant_id, k, "issue")
    results, unknowns = [], []
    for ch in changes:
        s = known.get(str(ch.get("subject", "")).lower())
        days = ch.get("shift_days")
        if s is None:
            raise ScenarioError(f"{ch.get('subject')!r} is not a release or commitment in the admitted evidence")
        if not isinstance(days, int) or isinstance(days, bool) or abs(days) > MAX_SHIFT_DAYS:
            raise ScenarioError(f"shift_days must be a whole number within ±{MAX_SHIFT_DAYS}")
        base, orig = _day(s["date"]), _day(s["original_date"])
        new = base + timedelta(days=days) if base else None
        mine = {i: d for i, d in issues.items() if d.get("fix_version") == s["name"]
                or (s["project"] and not d.get("fix_version") and d.get("project") == s["project"])}
        status = Counter(str(d.get("status_at_export")) for d in mine.values())
        open_ = [d for d in mine.values() if str(d.get("status_at_export", "")).lower() not in ("done", "closed")]
        blocked = Counter(str(d.get("blocked_by")) for d in mine.values() if d.get("blocked_by"))
        calc = []
        if base:
            calc.append(f"New date {_human(new)} ({'+' if days >= 0 else ''}{days} days from {_human(base)}).")
            if orig and orig != base:
                calc.append(f"That is {(new - orig).days} days from the original date {_human(orig)}.")
        else:
            unknowns.append(f"{s['name']}: no recorded date, so the new date cannot be calculated.")
        results.append({
            "subject": s["name"], "kind": s["kind"], "baseline": {"date": base.isoformat() if base else None,
                                                                  "original_date": orig.isoformat() if orig else None,
                                                                  "status": s["status"]},
            "assumption": f"{s['name']} moves by {days:+d} days (a hypothesis, not a plan).",
            "new_date": new.isoformat() if new else None, "calculations": calc,
            "affected_issues": {"total": len(mine), "open": len(open_), "by_status": dict(status)},
            "dependencies": [{"blocked_by": b, "issues": n} for b, n in blocked.most_common()],
            "evidence": s["evidence"]})
    dated = [r for r in results if r["new_date"]]
    if len(dated) > 1:
        order = sorted(dated, key=lambda r: r["new_date"])
        results_order = " then ".join(f"{r['subject']} ({_human(_day(r['new_date']))})" for r in order)
    else:
        results_order = None
    unknowns += ["Whether the remaining work fits the new dates: no supported throughput measure exists.",
                 "Effects on customers, revenue or contracts: no record ties them to these dates.",
                 "This scenario is not saved to company records or Jira."]
    return {"changes": results, "order_after_changes": results_order, "unknowns": unknowns,
            "read_basis": {"batch_seq": k}, "isolated": True}
