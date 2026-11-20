"""Jira assignee -> HR employee identity resolution (migration 0022), built on the C12 registry and decisions.

- `register_from_claims` (projection role, per sealed batch K): every Jira assignee value in admitted issue claims
  becomes a source identity, citing the issue record it came from. Jira OAuth issues carry Jira's stable account ID
  (`jira.account_id`); exported issue sheets carry a short code (`jira.assignee_code`). Rule candidates are recorded
  as SUGGESTIONS (initials etc.); they can never be approved for real data.
- `verify_with_jira` (application role, administrator-initiated): Jira's user list is read; where Jira reports an
  e-mail for an account and it equals exactly one HR work e-mail, an `exact_identifier` candidate is recorded and
  approved by that administrator. The e-mail is compared server side and never stored or returned.
- `map_identity` / `unmap_identity`: an administrator's explicit mapping (or its withdrawal), appended as a new
  versioned decision with a rationale; the previous decision stays in the history.
- Display names are never used to establish identity. Ambiguous cases stay unresolved until a human maps them.
"""

from __future__ import annotations

import json
import uuid

from ..db import tenant_tx
from . import rules
from . import store as c12

CONTEXTS = {"assignee_account_id": "jira.account_id", "assignee_source_id": "jira.assignee_code"}


def register_from_claims(conn, tenant_id: uuid.UUID, k: int, population: list[rules.Profile]) -> dict:
    with tenant_tx(conn, tenant_id, batch_seq=k):
        rows = conn.execute(
            "SELECT DISTINCT ON (predicate, object) predicate, object, record_id, record_version, batch_seq"
            " FROM knowledge.claim WHERE tenant_id = %s AND subject_type = 'issue' AND batch_seq <= %s"
            " AND predicate = ANY(%s) ORDER BY predicate, object, batch_seq, record_id",
            (tenant_id, k, list(CONTEXTS))).fetchall()
        if not population:                     # a batch without HR rows (e.g. a Jira sync): HR as known at K
            population = [rules.Profile(str(p["entity_id"]), p["full_name"],
                                        frozenset({p["work_email"].lower()}) if p["work_email"] else frozenset(),
                                        frozenset({("hr.emp_id", e)})) for e, p in _people(conn, tenant_id, k).items()]
    out = {"jira_identities": 0, "jira_suggestions": 0}
    for r in rows:
        value = str(r["object"] if not isinstance(r["object"], str) else r["object"]).strip().strip('"')
        if not value:
            continue
        ctx = CONTEXTS[r["predicate"]]
        ident = c12.register_identity(conn, tenant_id, "jira", ctx, value, "release_pack",
                                      (r["record_id"], r["record_version"], r["batch_seq"]))
        out["jira_identities"] += 1
        if ctx == "jira.assignee_code":            # suggestions only (initials); never approvable for real data
            found = [rules.Candidate(uuid.UUID(c.entity_id), c.classification, c.evidence)
                     for c in rules.candidates(value, population)]
            out["jira_suggestions"] += len(c12.propose(conn, tenant_id, ident, found))
    return out


def _people(conn, tenant_id, k):
    """HR person entities at K: emp_id -> (entity_id, name, title, department, work_email)."""
    return {r["emp_id"]: r for r in conn.execute(
        "SELECT p.emp_id, p.full_name, p.title, p.department, p.work_email, e.entity_id"
        " FROM projection.person p"
        " JOIN judgement.source_identity a ON a.tenant_id = p.tenant_id AND a.context = 'hr.emp_id'"
        "  AND a.source_value = p.emp_id"
        " JOIN judgement.entity e ON e.tenant_id = a.tenant_id AND e.anchor_identity_id = a.identity_id"
        " WHERE p.tenant_id = %s AND p.batch_seq = (SELECT max(batch_seq) FROM projection.person"
        "  WHERE tenant_id = %s AND batch_seq <= %s)", (tenant_id, tenant_id, k)).fetchall()}


_LATEST = (" LEFT JOIN LATERAL (SELECT d.outcome, d.version, d.decision_id, d.decided_by, d.decided_at, d.rationale"
           "  FROM judgement.resolution_decision d WHERE d.tenant_id = mc.tenant_id"
           "  AND d.candidate_id = mc.candidate_id"
           "  ORDER BY d.version DESC LIMIT 1) last ON true")


def resolved(conn, tenant_id: uuid.UUID) -> dict[tuple[str, str], str]:
    """(context, value) -> HR emp_id, for approved Jira identities only."""
    rows = conn.execute(
        "SELECT si.context, si.source_value, a.source_value AS emp_id FROM judgement.match_candidate mc" + _LATEST +  # noqa: S608 - constant SQL fragments only
        " JOIN judgement.source_identity si ON si.tenant_id = mc.tenant_id AND si.identity_id = mc.identity_id"
        " JOIN judgement.entity e ON e.tenant_id = mc.tenant_id AND e.entity_id = mc.entity_id"
        " JOIN judgement.source_identity a ON a.tenant_id = e.tenant_id AND a.identity_id = e.anchor_identity_id"
        " WHERE mc.tenant_id = %s AND si.context = ANY(%s) AND last.outcome = 'approved'",
        (tenant_id, list(CONTEXTS.values()))).fetchall()
    return {(r["context"], r["source_value"]): r["emp_id"] for r in rows}


def overview(conn, tenant_id: uuid.UUID, k: int) -> dict:
    """Every Jira identity with its state, suggestions and decision history (for administrators only)."""
    with tenant_tx(conn, tenant_id, batch_seq=k):
        idents = conn.execute(
            "SELECT identity_id, context, source_value, observed_at FROM judgement.source_identity"
            " WHERE tenant_id = %s AND context = ANY(%s) ORDER BY context, source_value",
            (tenant_id, list(CONTEXTS.values()))).fetchall()
        cands = conn.execute(
            "SELECT mc.candidate_id, mc.identity_id, mc.classification, mc.evidence, a.source_value AS emp_id,"  # noqa: S608 - constant SQL fragments only
            " last.outcome, last.version, last.decided_by, last.decided_at, last.rationale"
            " FROM judgement.match_candidate mc" + _LATEST +
            " JOIN judgement.entity e ON e.tenant_id = mc.tenant_id AND e.entity_id = mc.entity_id"
            " JOIN judgement.source_identity a ON a.tenant_id = e.tenant_id AND a.identity_id = e.anchor_identity_id"
            " WHERE mc.tenant_id = %s AND mc.identity_id = ANY(%s)",
            (tenant_id, [i["identity_id"] for i in idents])).fetchall()
        hist = conn.execute(
            "SELECT mc.identity_id, d.version, d.outcome, d.decided_by, d.decided_at, d.rationale, mc.classification,"
            " a.source_value AS emp_id FROM judgement.resolution_decision d"
            " JOIN judgement.match_candidate mc ON mc.tenant_id = d.tenant_id AND mc.candidate_id = d.candidate_id"
            " JOIN judgement.entity e ON e.tenant_id = mc.tenant_id AND e.entity_id = mc.entity_id"
            " JOIN judgement.source_identity a ON a.tenant_id = e.tenant_id AND a.identity_id = e.anchor_identity_id"
            " WHERE d.tenant_id = %s AND mc.identity_id = ANY(%s) ORDER BY d.decided_at",
            (tenant_id, [i["identity_id"] for i in idents])).fetchall()
        names = conn.execute(
            "SELECT a.object AS account, n.object AS name, count(*) AS issues FROM knowledge.claim a"
            " LEFT JOIN knowledge.claim n ON n.tenant_id = a.tenant_id AND n.subject = a.subject"
            "  AND n.predicate = 'assignee_name' AND n.batch_seq = a.batch_seq"
            " WHERE a.tenant_id = %s AND a.subject_type = 'issue' AND a.batch_seq <= %s"
            "  AND a.predicate IN ('assignee_account_id', 'assignee_source_id') GROUP BY 1, 2",
            (tenant_id, k)).fetchall()
        people = _people(conn, tenant_id, k)
    seen = {}
    for n in names:
        key = str(n["account"]).strip('"')
        seen.setdefault(key, {"jira_name": None, "issues": 0})
        seen[key]["issues"] += n["issues"]
        if n["name"]:
            seen[key]["jira_name"] = str(n["name"]).strip('"')

    def who(emp):
        p = people.get(emp)
        return {"emp_id": emp, "name": p["full_name"], "title": p["title"], "department": p["department"]} if p \
            else {"emp_id": emp, "name": None}
    rows = []
    for i in idents:
        mine = [c for c in cands if c["identity_id"] == i["identity_id"]]
        approved = [c for c in mine if c["outcome"] == "approved"]
        suggestions = [{"classification": c["classification"], "employee": who(c["emp_id"]),
                        "approvable": c["classification"] in c12.JIRA_APPROVABLE}
                       for c in mine if c["outcome"] is None and c["classification"] != "administrator_mapping"]
        state = ("mapped" if approved else "ambiguous" if len({s["employee"]["emp_id"] for s in suggestions}) > 1
                 else "suggested" if suggestions else "unmapped")
        extra = seen.get(i["source_value"], {})
        rows.append({
            "identity_id": str(i["identity_id"]), "context": i["context"], "value": i["source_value"],
            "jira_name": extra.get("jira_name"), "issues": extra.get("issues", 0), "state": state,
            "mapped_to": who(approved[0]["emp_id"]) if approved else None,
            "mapped_by": {"kind": approved[0]["classification"], "by": approved[0]["decided_by"],
                          "at": approved[0]["decided_at"].isoformat(), "rationale": approved[0]["rationale"]}
            if approved else None,
            "suggestions": suggestions,
            "history": [{"version": h["version"], "outcome": h["outcome"], "kind": h["classification"],
                         "employee": who(h["emp_id"]), "by": h["decided_by"], "at": h["decided_at"].isoformat(),
                         "rationale": h["rationale"]} for h in hist if h["identity_id"] == i["identity_id"]]})
    return {"identities": rows,
            "employees": sorted(({"emp_id": e, "name": p["full_name"], "title": p["title"],
                                  "department": p["department"]} for e, p in people.items()),
                                key=lambda x: (x["department"] or "", x["name"] or ""))}


def _candidate_for(conn, tenant_id, identity_id, entity_id, classification, evidence):
    row = conn.execute(
        "SELECT candidate_id FROM judgement.match_candidate WHERE tenant_id = %s AND identity_id = %s"
        " AND entity_id = %s AND classification = %s", (tenant_id, identity_id, entity_id, classification)).fetchone()
    if row:
        return row["candidate_id"]
    cid = uuid.uuid4()
    conn.execute("INSERT INTO judgement.match_candidate (tenant_id, candidate_id, identity_id, entity_id,"
                 " classification, rule_version, evidence) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                 (tenant_id, cid, identity_id, entity_id, classification, f"jira-identity/1:{classification}",
                  json.dumps(evidence)))
    return cid


def _current_approval(conn, tenant_id, identity_id):
    return conn.execute(
        "SELECT mc.candidate_id, mc.entity_id FROM judgement.match_candidate mc" + _LATEST +  # noqa: S608 - constant SQL fragments only
        " WHERE mc.tenant_id = %s AND mc.identity_id = %s AND last.outcome = 'approved'",
        (tenant_id, identity_id)).fetchone()


def unmap_identity(conn, tenant_id, admin: str, identity_id: uuid.UUID, rationale: str) -> dict:
    with tenant_tx(conn, tenant_id, admin):
        cur = _current_approval(conn, tenant_id, identity_id)
    if cur is None:
        raise c12.Invalid("this Jira identity is not mapped")
    return c12.decide(conn, tenant_id, admin, cur["candidate_id"], "rejected", rationale,
                      "administrator withdrew the Jira identity mapping")


def map_identity(conn, tenant_id, admin: str, identity_id: uuid.UUID, emp_id: str, rationale: str, k: int,
                 kind: str = "administrator_mapping", evidence: list[str] | None = None) -> dict:
    if not rationale or not rationale.strip():
        raise c12.Invalid("a reason is required")
    with tenant_tx(conn, tenant_id, admin, batch_seq=k):
        ident = conn.execute("SELECT context FROM judgement.source_identity WHERE tenant_id = %s AND identity_id = %s",
                             (tenant_id, identity_id)).fetchone()
        if ident is None or ident["context"] not in CONTEXTS.values():
            raise c12.Invalid("unknown Jira identity")
        person = _people(conn, tenant_id, k).get(emp_id)
        if person is None:
            raise c12.Invalid("unknown employee")
        cur = _current_approval(conn, tenant_id, identity_id)
        if cur is not None and cur["entity_id"] == person["entity_id"]:
            return {"unchanged": True}
        cid = _candidate_for(conn, tenant_id, identity_id, person["entity_id"], kind,
                             evidence or [f"mapped by an administrator to HR record {emp_id}"])
    if cur is not None:                        # a re-mapping first withdraws the old link (both stay in history)
        c12.decide(conn, tenant_id, admin, cur["candidate_id"], "rejected", rationale,
                   "administrator re-mapped the Jira identity")
    return c12.decide(conn, tenant_id, admin, cid, "approved", rationale,
                      "verified exact identifier" if kind == "exact_identifier" else "administrator mapping")


def verify_with_jira(conn, tenant_id, admin: str, jira_users: list[dict], k: int) -> dict:
    """Map accounts whose Jira-reported e-mail equals exactly one HR work e-mail. Nothing else is matched."""
    with tenant_tx(conn, tenant_id, admin, batch_seq=k):
        idents = {r["source_value"]: r["identity_id"] for r in conn.execute(
            "SELECT identity_id, source_value FROM judgement.source_identity WHERE tenant_id = %s"
            " AND context = 'jira.account_id'", (tenant_id,)).fetchall()}
        people = _people(conn, tenant_id, k)
    by_email: dict[str, list[str]] = {}
    for emp, p in people.items():
        if p["work_email"]:
            by_email.setdefault(p["work_email"].strip().lower(), []).append(emp)
    out = {"jira_users": len(jira_users), "with_email": 0, "matched": 0, "already": 0, "ambiguous": 0,
           "no_hr_match": 0, "not_assignees": 0}
    for u in jira_users:
        email = (u.get("email") or "").strip().lower()
        if email:
            out["with_email"] += 1
        if u["account_id"] not in idents:
            out["not_assignees"] += 1
            continue
        if not email:
            continue
        emps = by_email.get(email, [])
        if len(emps) != 1:
            out["ambiguous" if emps else "no_hr_match"] += 1
            continue
        r = map_identity(conn, tenant_id, admin, idents[u["account_id"]], emps[0],
                         "Jira's user profile e-mail equals this employee's HR work e-mail exactly", k,
                         kind="exact_identifier",
                         evidence=[f"Jira account e-mail equals the HR work e-mail of record {emps[0]} (exact)"])
        out["already" if r.get("unchanged") else "matched"] += 1
    return out
