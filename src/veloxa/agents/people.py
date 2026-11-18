"""The 14 agent people: grounding in the HR records, evidence-derived work profiles, and the persona split.

Owner decision (18 Nov 2026, hackathon tenant only; K-01/K-02): map the 14 agent slots to the HR employees of the
four departments the brief names (engineering, finance, HR, marketing). Links follow the K-04 rule:
- the HR employee record is the anchor (`hr_employee_id`);
- another system's identifier links ONLY by exact HR employee ID or exact corporate e-mail (case-insensitive);
- names, first names and initials never link: such source identifiers stay UNIDENTIFIED and are reported as such.

A profile separates three things and never mixes them:
1. verified work facts (claims from admitted records, each with its record id),
2. fictional persona assumptions (the Assumption Ledger; labelled, never evidence),
3. unknowns (what no admitted record states).
"""

from __future__ import annotations

import uuid

import psycopg

from . import persona, registry

DEPT_CATEGORY = {"Engineering": "engineering", "Finance": "finance", "Marketing": "marketing",
                 "People & Ops": "people_ops"}
SHORT = {"engineering": "eng", "finance": "fin", "people_ops": "ppl", "marketing": "mkt"}
DECISION_REF = ("Owner directive 18 Nov 2026 (Company Intelligence Foundation): K-01/K-02 approved for the "
                "hackathon tenant")
DONE = {"done", "closed", "resolved"}


class GroundingError(RuntimeError):
    pass


def _people_at(conn: psycopg.Connection, tenant_id: uuid.UUID, k: int) -> list[dict]:
    return conn.execute(
        "SELECT * FROM projection.person WHERE tenant_id = %s AND batch_seq = (SELECT max(batch_seq) FROM"
        " projection.person WHERE tenant_id = %s AND batch_seq <= %s) ORDER BY emp_id", (tenant_id, tenant_id, k)
    ).fetchall()


def assignments(people: list[dict]) -> list[tuple[str, dict]]:
    """Deterministic slot -> person mapping (by HR employee ID within each department). Refuses rather than invents:
    the department counts must equal the 14-slot distribution."""
    by: dict[str, list[dict]] = {}
    for p in people:
        cat = DEPT_CATEGORY.get(p["department"])
        if cat:
            by.setdefault(cat, []).append(p)
    counts = {c: len(v) for c, v in by.items()}
    if counts != registry.PROPOSED_DISTRIBUTION:
        raise GroundingError(f"HR records give {counts}, not the 14-slot distribution "
                             f"{registry.PROPOSED_DISTRIBUTION}; no agent is mapped")
    out = []
    for cat, ps in by.items():
        for i, p in enumerate(sorted(ps, key=lambda x: x["emp_id"]), 1):
            out.append((f"agent-{SHORT[cat]}-{i:02d}", p))
    return out


def ground(conn: psycopg.Connection, tenant_id: uuid.UUID, k: int) -> list[str]:
    """Publish an `active` configuration per slot representing its HR person, record runtime.agent_person and the
    persona assumptions. Idempotent: an agent already representing the same person is left unchanged."""
    from ..db import tenant_tx

    with tenant_tx(conn, tenant_id):
        mapped = assignments(_people_at(conn, tenant_id, k))
    done = []
    for agent_id, p in mapped:
        person_ref = f"person:{p['emp_id']}"
        cur = registry.latest(conn, tenant_id, agent_id)
        if cur and cur.represented_person == person_ref and cur.activation == "active" \
                and "self.profile" in cur.tools:     # configurations before self_profile get a new version
            continue
        cat = DEPT_CATEGORY[p["department"]]
        caps, tools = registry.category_default(cat)
        version = registry.publish(conn, tenant_id, registry.AgentConfig(
            agent_id, cat, caps, tools, knowledge_scopes=("company_records", "own_participation"),
            represented_person=person_ref, activation="active", approved_by="Owner", decision_record=DECISION_REF))
        with tenant_tx(conn, tenant_id):
            conn.execute(
                "INSERT INTO runtime.agent_person (tenant_id, agent_id, version, person_record, display_name, title,"
                " department, link_rule, decision_ref) VALUES (%s,%s,%s,%s,%s,%s,%s,'hr_employee_id',%s)",
                (tenant_id, agent_id, version, p["source_record_id"], p["full_name"], p["title"], p["department"],
                 DECISION_REF))
            persona.record(conn, tenant_id, [agent_id])
        done.append(agent_id)
    return done


def link(source_id: str | None, people: list[dict]) -> dict | None:
    """K-04: exact HR employee ID or exact corporate e-mail only. Everything else is unidentified."""
    if not source_id:
        return None
    s = str(source_id).strip()
    for p in people:
        if s == p["emp_id"] or (p["work_email"] and s.lower() == p["work_email"].lower()):
            return p
    return None


def _claims(conn, tenant_id, k, subject_type: str) -> dict[str, dict]:
    rows = conn.execute(
        "SELECT c.subject, c.predicate, c.object, c.record_id, c.valid_at FROM knowledge.claim c WHERE c.tenant_id = %s"
        " AND c.subject_type = %s AND c.batch_seq <= %s ORDER BY c.batch_seq", (tenant_id, subject_type, k)).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        d = out.setdefault(r["subject"], {"_records": set()})
        d[r["predicate"]] = r["object"]
        d["_records"].add(r["record_id"])
    return out


def profile(conn: psycopg.Connection, tenant_id: uuid.UUID, k: int, agent_id: str) -> dict:
    """Evidence-derived work profile at knowledge point K, plus labelled persona assumptions and unknowns."""
    ap = conn.execute("SELECT * FROM runtime.agent_person WHERE tenant_id = %s AND agent_id = %s ORDER BY version"
                      " DESC LIMIT 1", (tenant_id, agent_id)).fetchone()
    if ap is None:
        return {"agent_id": agent_id, "grounded": False,
                "unknowns": ["This slot is not mapped to a person (no owner-approved mapping)."]}
    people = _people_at(conn, tenant_id, k)
    me = next((p for p in people if p["source_record_id"] == ap["person_record"]), None)
    if me is None:
        return {"agent_id": agent_id, "grounded": False,
                "unknowns": ["The represented person's HR record is not visible at this knowledge point."]}
    by_emp = {p["emp_id"]: p for p in people}
    facts = [
        {"fact": "Name", "value": me["full_name"], "record": me["source_record_id"]},
        {"fact": "Title", "value": me["title"], "record": me["source_record_id"]},
        {"fact": "Department", "value": me["department"], "record": me["source_record_id"]},
        {"fact": "Employment status (as at the HR extraction)", "value": me["status"],
         "record": me["source_record_id"]},
        {"fact": "Joined", "value": me["join_date"].isoformat(), "record": me["source_record_id"]},
    ]
    if me["manager_emp_id"]:
        mgr = by_emp.get(me["manager_emp_id"])
        facts.append({"fact": "Reports to", "value": f"{mgr['full_name']} ({mgr['title']})" if mgr
                      else f"employee {me['manager_emp_id']} (not in this HR extract)",
                      "record": me["source_record_id"]})
    reports = [p for p in people if p["manager_emp_id"] == me["emp_id"]]
    if reports:
        facts.append({"fact": "Direct reports", "value": ", ".join(f"{p['full_name']} ({p['title']})" for p in reports),
                      "record": me["source_record_id"]})
    if me["exit_date"]:
        facts.append({"fact": "Exit date", "value": me["exit_date"].isoformat(), "record": me["source_record_id"]})

    issues = _claims(conn, tenant_id, k, "issue")
    mine = []
    for key, d in issues.items():
        who = d.get("assignee_source_id")
        p = link(who, people)
        if p is None:
            continue
        if p["emp_id"] == me["emp_id"]:
            mine.append({"key": key.split(":", 1)[1], "summary": d.get("summary"), "status": d.get("status_at_export"),
                         "points": d.get("story_points"), "fix_version": d.get("fix_version"),
                         "resolved": d.get("resolved"), "records": sorted(d["_records"])})
    open_ = [i for i in mine if str(i["status"] or "").lower() not in DONE]
    rota = _claims(conn, tenant_id, k, "rota_week")
    rota_weeks = sorted(w.split(":", 1)[1] for w, d in rota.items()
                        if any((p := link(d.get(f), people)) and p["emp_id"] == me["emp_id"]
                               for f in ("primary_source_id", "secondary_source_id")))
    incidents = _claims(conn, tenant_id, k, "incident")
    responded = sorted(i.split(":", 1)[1] for i, d in incidents.items()
                       if (p := link(d.get("responder_source_id"), people)) and p["emp_id"] == me["emp_id"])
    reqs = _claims(conn, tenant_id, k, "requisition")
    hiring = sorted(r.split(":", 1)[1] for r, d in reqs.items()
                    if (p := link(d.get("hiring_manager_source_id"), people)) and p["emp_id"] == me["emp_id"])

    # Repository allocation from git (exact corporate e-mail only). Never counts, rates or timing (POL-PROD-01).
    repos = [{"repo": r["repo"], "first": r["first"], "last": r["last"]} for r in conn.execute(
        "SELECT payload->>'repo' AS repo, min(left(payload->>'committed_at', 10)) AS first,"
        " max(left(payload->>'committed_at', 10)) AS last FROM ledger.source_record WHERE tenant_id = %s"
        " AND system = 'git' AND batch_seq <= %s AND lower(payload->>'author_email') = lower(%s)"
        " GROUP BY 1 ORDER BY 1", (tenant_id, k, me["work_email"] or "")).fetchall()] if me["work_email"] else []
    unknowns = []
    if not issues:
        unknowns.append("No task records are admitted at this knowledge point, so current workload is unknown.")
    elif not mine:
        unknowns.append("No admitted task record links to this person by employee ID or exact e-mail; workload is "
                        "unknown, not zero.")
    if issues and any(link(d.get("assignee_source_id"), people) is None and d.get("assignee_source_id")
                      for d in issues.values()):
        unknowns.append("Some task assignees are written in a form that cannot be linked to an HR record without an "
                        "owner-reviewed identity decision; they are not attributed to anyone.")
    unknowns.append("Private thoughts, motivations and anything outside the records are unknown; persona details "
                    "below are invented and labelled as assumptions.")
    assumptions = conn.execute(
        "SELECT assumption_id, category, value FROM runtime.assumption_current WHERE tenant_id = %s AND agent_id = %s"
        " ORDER BY category", (tenant_id, agent_id)).fetchall()
    return {
        "agent_id": agent_id, "grounded": True, "person_record": ap["person_record"], "name": me["full_name"],
        "title": me["title"], "department": me["department"], "status": me["status"],
        "verified_facts": facts,
        "workload": {"open_tasks": len(open_) if mine else None,          # None = unknown, never zero
                     "open_points": sum(float(i["points"] or 0) for i in open_) if mine else None,
                     "repositories": repos,
                     "tasks": sorted(mine, key=lambda i: i["key"]), "rota_weeks": rota_weeks,
                     "incidents_responded": responded, "requisitions_owned": hiring,
                     "basis": "Admitted Jira, rota, incident, hiring and git records linked by HR employee ID or "
                              "exact e-mail only; repositories show allocation, never activity counts"},
        "assumptions": [{"assumption_id": a["assumption_id"], "category": a["category"], "value": a["value"],
                         "label": "Assumption (invented persona detail, not a company record)"} for a in assumptions],
        "unknowns": unknowns,
        "knowledge_point": k,
    }
