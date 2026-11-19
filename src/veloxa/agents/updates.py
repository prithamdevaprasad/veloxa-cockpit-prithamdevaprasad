"""`work.updates`: department-aware updates and recorded constraints for an agent, from admitted structured records.

Every fact cites the records it rests on and keeps three dates apart:
- the EVENT date (when something happened or is due, as the record states it);
- the RECORD date (the export or document date, when the source carries one);
- the ADMISSION date (the release in which Veloxa received the record).
People are never named or judged here: counts and work-level facts only. Missing sources are said to be missing.
"""

from __future__ import annotations

from collections import Counter
from datetime import date

from .. import ledger
from .. import tools as vt
from ..db import tenant_tx

# The systems a department's own work is recorded in: the only records `work.updates` may present as department
# context. They still pass the interviewer's authorization and the interview data-class policy.
DEPT_SYSTEMS = {"engineering": {"jira", "git", "incident_log", "oncall_rota", "hiring_tracker"},
                "people": {"keka_hrms", "hiring_tracker", "pulse_survey"},
                "finance": {"zoho_books", "documents"},
                "marketing": {"documents", "hiring_tracker"}}
DEPT_FOCUS = {"engineering": "engineering", "finance": "finance", "people": "people", "people & ops": "people",
              "people_ops": "people", "hr": "people", "marketing": "marketing"}
MAX_CITED = 30                          # records cited per fact (the count itself uses every record)
OPEN = ("to do", "in progress", "open", "blocked", "in review")


def _day(v) -> str:
    try:
        d = date.fromisoformat(str(v)[:10])
        return f"{d.day} {d.strftime('%b %Y')}"
    except (TypeError, ValueError):
        return str(v)


def _claims(conn, tenant_id, k, stype):
    out: dict[str, dict] = {}
    for r in conn.execute("SELECT subject, predicate, object, record_id, batch_seq FROM knowledge.claim WHERE"
                          " tenant_id = %s AND subject_type = %s AND batch_seq <= %s ORDER BY batch_seq, claim_id",
                          (tenant_id, stype, k)).fetchall():
        d = out.setdefault(r["subject"], {"_records": set(), "_batch": r["batch_seq"]})
        d[r["predicate"]] = r["object"]
        d["_records"].add(r["record_id"])
        d["_batch"] = max(d["_batch"], r["batch_seq"])
    return out


def build(ctx, focus: str = "updates") -> dict:
    out = {"tool": "work.updates", "status": "no_data", "facts": [], "evidence": [], "gaps": [], "reason": None}
    if not ctx.basis.batch_seq:
        out["gaps"] = ["nothing has been admitted at this knowledge point"]
        return out
    released = {k: v for k, v in ledger.sealed_releases(ctx.conn, ctx.tenant_id)}
    admitted = lambda b: (f"admitted with the {_day(released[b])} release" if released.get(b)  # noqa: E731
                          else "admitted")
    k = ctx.basis.batch_seq
    with tenant_tx(ctx.conn, ctx.tenant_id, batch_seq=k):
        dept = None
        if ctx.agent_id:
            r = ctx.conn.execute("SELECT department FROM runtime.agent_person WHERE agent_id = %s ORDER BY version"
                                 " DESC LIMIT 1", (ctx.agent_id,)).fetchone()
            dept = DEPT_FOCUS.get(str(r["department"]).lower()) if r else None
        get = lambda t: _claims(ctx.conn, ctx.tenant_id, k, t)  # noqa: E731
        versions, issues, incidents = get("version"), get("issue"), get("incident")
        reqs, people, teams, metrics = get("requisition"), get("person"), get("team"), get("metric")

    facts: list[tuple[str, str, set]] = []

    def add(fid, text, d_or_recs, batch=None):
        recs = set(d_or_recs["_records"]) if isinstance(d_or_recs, dict) else set(d_or_recs)
        b = batch or (d_or_recs.get("_batch") if isinstance(d_or_recs, dict) else None)
        cited = set(sorted(recs)[:MAX_CITED])            # bounded work per fact; the text says when it is a sample
        note = f"; {len(cited)} of {len(recs)} records cited" if len(recs) > MAX_CITED else ""
        facts.append((fid, text + (f" ({admitted(b)}{note})" if b else (f" ({note[2:]})" if note else "")), cited))

    eng = dept in (None, "engineering")
    if eng:
        for s, d in versions.items():
            name = s.split(":", 1)[1]
            moved = d.get("original_release_date") and d.get("release_date") != d.get("original_release_date")
            add(f"upd.version:{name}", f"{name} is due {_day(d.get('release_date'))}" + (
                f"; it was originally due {_day(d.get('original_release_date'))}"
                + (f" and the change was recorded on {_day(d.get('last_changed'))}" if d.get("last_changed") else "")
                if moved else ""), d)
        op = [d for d in issues.values() if str(d.get("status_at_export", "")).lower() in OPEN]
        if issues:
            pts = sum(float(d.get("story_points") or 0) for d in op)
            add("upd.issues", f"The tracker export shows {len(op)} of {len(issues)} issues still open ({pts:g} story "
                "points); it is a snapshot, so later progress is not recorded",
                {r for d in issues.values() for r in d["_records"]}, max(d["_batch"] for d in issues.values()))
        if incidents:
            last = max(incidents.values(), key=lambda d: str(d.get("opened")))
            sev = Counter(str(d.get("severity")) for d in incidents.values())
            add("upd.incidents", f"{len(incidents)} incidents are recorded ("
                + ", ".join(f"{n} {s_}" for s_, n in sorted(sev.items()))
                + f"); the most recent was opened on {_day(last.get('opened'))}",
                {r for d in incidents.values() for r in d["_records"]}, max(d["_batch"] for d in incidents.values()))
    mine = [(s, d) for s, d in reqs.items()
            if dept is None or dept in str(d.get("team", "")).lower() or dept == "people"]
    if mine:
        st = Counter(str(d.get("status")) for _, d in mine)
        add("upd.hiring", f"The hiring tracker lists {len(mine)} requisition{'s' if len(mine) != 1 else ''}"
            + ("" if dept in (None, "people") else " for this team") + ": "
            + ", ".join(f"{n} {s_.lower()}" for s_, n in st.most_common()),
            {r for _, d in mine for r in d["_records"]}, max(d["_batch"] for _, d in mine))
    if dept in (None, "people") and people:
        st = Counter(str(d.get("employment_status")) for d in people.values())
        add("upd.status", "The HR export lists " + ", ".join(f"{n} {s_.lower()}" for s_, n in st.most_common())
            + " (as at the export date; later changes are not recorded)",
            {r for d in people.values() for r in d["_records"]}, max(d["_batch"] for d in people.values()))
    if dept in (None, "people") and teams:
        latest = max(str(s).split(":")[-1] for s in teams)
        add("upd.pulse", "The pulse survey reports team-level engagement only (comments are suppressed); the "
            f"latest period recorded is {latest}", {r for d in teams.values() for r in d["_records"]},
            max(d["_batch"] for d in teams.values()))
    metrics = {s: d for s, d in metrics.items() if all(r.startswith("documents:") for r in d["_records"])}
    if dept in (None, "finance", "marketing") and metrics:                 # board documents only, never speech
        names = sorted({str(s).split(":", 1)[-1].replace("_", " ") for s in metrics})[:4]
        add("upd.board", "The board updates report figures for " + ", ".join(names)
            + " (as reported; not independently verified)", {r for d in metrics.values() for r in d["_records"]},
            max(d["_batch"] for d in metrics.values()))

    if focus == "blockers":
        keep = {"upd.hiring", "upd.incidents", "upd.issues"} | {f for f, _, _ in facts if f.startswith("upd.version")}
        facts = [f for f in facts if f[0] in keep]
        out["gaps"].append("The records don't mark anything as a blocker; these are recorded constraints (dates that "
                           "moved, paused hiring, incidents, open work), not a judgement.")
    if not facts:
        out["gaps"].append("No admitted record gives an update for this department yet.")
        return out
    ids = sorted({r for _, _, recs in facts for r in recs})
    ev = vt._evidence(ctx.conn, ctx.basis, ids)
    seen = {e.record_id for e in ev}
    out.update(status="ok", facts=[{"fact_id": fid, "display": text, "kind": "record",
                                    "inputs": sorted(recs & seen)} for fid, text, recs in facts if recs & seen],
               evidence=[{"record_id": e.record_id, "kind": e.kind, "system": e.system, "data_class": e.data_class,
                          "batch_seq": e.batch_seq, "excerpt": e.excerpt} for e in ev])
    if dept is None:
        out["gaps"].append("This agent is not mapped to a department, so updates are company-wide.")
    return out
