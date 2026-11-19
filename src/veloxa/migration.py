"""Company records -> Jira: a MIGRATION PREVIEW of operational work. Read-only; nothing is written anywhere.

What becomes a Jira candidate (and nothing else): projects/initiatives (releases and the epics inside them) and
actionable work (concerns with a recorded follow-up, blockers such as paused hiring, unanswered delivery questions).
Never: documents, employees, metrics, historical rows, or sensitive HR matters (those go to an internal People Ops
review instead). Due dates only when a record states one; owners only as a department, never a person.
Existing Jira issues are linked, not recreated: issues already in the company's Jira export and open issues in the
connected Jira site with a matching summary are reported as duplicates.
"""

from __future__ import annotations

from collections import Counter
from datetime import date

from .db import tenant_tx

SENSITIVE_PREFIXES = {"notice:": "An individual's notice period is a sensitive HR matter: request a People Ops review "
                                 "instead of a Jira issue.",
                      "leave:": "Leave records are sensitive personal data.",
                      "pay:": "Pay is restricted (POL-PAY-01)."}
NEVER = ("payroll_register", "leave_register", "pulse_survey")


def _claims(conn, tenant_id, k, stype):
    out: dict[str, dict] = {}
    for r in conn.execute("SELECT subject, predicate, object, record_id FROM knowledge.claim WHERE tenant_id = %s"
                          " AND subject_type = %s AND batch_seq <= %s ORDER BY batch_seq, claim_id",
                          (tenant_id, stype, k)).fetchall():
        d = out.setdefault(r["subject"], {"_records": set()})
        d[r["predicate"]] = r["object"]
        d["_records"].add(r["record_id"])
    return out


def preview(conn, basis, tz: str, jira=None, tenant_key: str | None = None, test_project: str = "VIT") -> dict:
    from .risk import graph as dep_graph
    from .risk import worry
    from .signals.registry import assessment_date

    k = basis.batch_seq
    with tenant_tx(conn, basis.tenant_id, batch_seq=k):
        versions = _claims(conn, basis.tenant_id, k, "version")
        issues = _claims(conn, basis.tenant_id, k, "issue")
        items = worry.build_items(conn, basis.tenant_id, k, assessment_date(basis, tz))
    try:
        warnings = dep_graph.build(conn, basis, tz).get("warnings", [])
    except Exception:  # noqa: BLE001 - the dependency view is optional input
        warnings = []

    # ---- projects / initiatives
    projects = []
    for s, d in versions.items():
        name = s.split(":", 1)[1]
        epics = Counter(str(x.get("epic")) for x in issues.values()
                        if x.get("fix_version") == name or (d.get("project") and x.get("project") == d.get("project")))
        in_export = any(r.startswith("jira:") for r in d["_records"])
        projects.append({"name": name, "kind": "release", "due": d.get("release_date"),
                         "original_due": d.get("original_release_date"),
                         "epics": [{"key": e, "issues": n} for e, n in sorted(epics.items()) if e != "None"],
                         "proposed": ("Already tracked in the company's Jira (from its export): link the existing "
                                      "version and epics; do not recreate them." if in_export else
                                      "Propose an Epic (production project to be approved)."),
                         "evidence": sorted(d["_records"])[:5]})

    # ---- actionable candidates
    candidates, excluded = [], []
    for it in items:
        why = next((v for p, v in SENSITIVE_PREFIXES.items() if it.item_id.startswith(p)), None)
        if why:
            src = it.item_id.split(":")[0]
            prev = next((e for e in excluded if e["source"] == src), None)
            if prev:
                prev["records"] += len(it.evidence)
                prev["items"] += 1
            else:
                excluded.append({"source": src, "reason": why, "records": len(it.evidence), "items": 1})
            continue
        if not it.follow_up:
            continue
        candidates.append({"summary": it.follow_up.rstrip("."), "type": "Task", "context": it.title,
                           "department": it.department, "project": it.project,
                           "due": None, "evidence": list(it.evidence)[:10]})
    for w in warnings:
        if w.get("kind") == "moved_without_replacement_date":
            candidates.append({"summary": f"Confirm a replacement date for {w['commitment']}", "type": "Task",
                               "context": w["statement"], "department": "Engineering", "project": w["commitment"],
                               "due": None, "evidence": list(w.get("evidence", []))[:10]})
    same = Counter(c["summary"].lower() for c in candidates)        # one follow-up, several concerns: keep each
    for c in candidates:
        if same[c["summary"].lower()] > 1:
            c["summary"] = f"{c['summary']} ({c['context'].rstrip('.')})"[:250]
    seen, unique = set(), []
    for c in candidates:
        if c["summary"].lower() not in seen:
            seen.add(c["summary"].lower())
            unique.append(c)
    candidates = unique

    # ---- duplicates: the company's own Jira export, then the connected site (open issues, matching summary)
    export_summaries = {str(x.get("summary", "")).lower(): s.split(":", 1)[1] for s, x in issues.items()}
    live_checked = False
    for c in candidates:
        c["duplicates"] = []
        hit = export_summaries.get(c["summary"].lower())
        if hit:
            c["duplicates"].append({"where": "company Jira export", "key": hit})
        if jira is not None and tenant_key:
            try:
                words = " ".join(w for w in c["summary"].replace('"', " ").split()[:6])
                for h in jira.search(tenant_key, f'project = "{test_project}" AND statusCategory != Done AND '
                                                 f'summary ~ "{words}"', 5):
                    c["duplicates"].append({"where": f"Jira site ({test_project})", "key": h["key"], "url": h["url"]})
                live_checked = True
            except Exception:  # noqa: BLE001 - a duplicate check that cannot run is reported, not hidden
                c["duplicates_unchecked"] = True
        c["missing"] = [m for m, v in (("owner", c["department"]), ("deadline", c["due"])) if not v]
        c["proposed_destination"] = {"testing": f"{test_project} (integration test project)",
                                     "production": "a production project to be approved by you"}
    for sysname in NEVER:
        excluded.append({"source": sysname, "reason": "Never migrated: personal or protected data.", "records": None})
    return {
        "read_only": True, "read_basis": {"batch_seq": k, "as_of": date.today().isoformat()},
        "projects": projects, "candidates": candidates,
        "summary": {"projects": len(projects), "candidates": len(candidates),
                    "duplicates": sum(bool(c["duplicates"]) for c in candidates),
                    "missing_owner": sum("owner" in c["missing"] for c in candidates),
                    "missing_deadline": sum("deadline" in c["missing"] for c in candidates),
                    "excluded_sensitive": len([e for e in excluded if e["records"]])},
        "excluded": excluded, "live_duplicate_check": live_checked,
        "not_migrated": "Documents, employees, metrics and historical records stay in Veloxa as evidence; only "
                        "actionable work is proposed. Nothing is written until you approve a destination and confirm "
                        "each write.",
    }
