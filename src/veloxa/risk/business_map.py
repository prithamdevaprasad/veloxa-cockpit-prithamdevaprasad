"""The business risk and dependency map: one graph pooling every admitted source the reader may see.

Nodes: departments, people (HR export), source systems (HR, finance, GitHub, email, transcripts, Jira, data room
uploads...), records, releases/commitments, tracker issues and concerns. Edges are only those the evidence states:
works_in, reports_to, from_system, stated_in, evidence_for, assigned_to, mentions, concerns, conflict. Nothing is
inferred, scored or predicted; an item whose evidence the reader cannot see is left out and counted.
"""

from __future__ import annotations

import re
from collections import defaultdict

from ..db import tenant_tx

PER_SYSTEM = 12


def owner_of(owners: dict, d: dict) -> str | None:
    """The HR emp_id of an issue's assignee, only through an approved Jira identity (never by name)."""
    for ctx, pred in (("jira.account_id", "assignee_account_id"), ("jira.assignee_code", "assignee_source_id")):
        v = d.get(pred)
        if v and (ctx, str(v)) in owners:
            return owners[(ctx, str(v))]
    return None          # unlinked records shown per system (the rest are counted on the system node)


def build(conn, basis, tz, principal, readable, people_rows, worry_items) -> dict:
    from ..ask.continuity import _claims
    from . import graph

    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    seen_edges: set[tuple] = set()
    hidden = 0

    def node(nid, kind, label, **kw):
        nodes.setdefault(nid, {"id": nid, "kind": kind, "label": label, **kw})

    def edge(a, b, rel):
        if a and b and a in nodes and b in nodes and a != b and (a, b, rel) not in seen_edges:
            seen_edges.add((a, b, rel))
            edges.append({"source": a, "target": b, "relation": rel})

    # People and departments (work facts only).
    emp = {}
    for s in people_rows:
        dep = s.get("department") or "Unassigned"
        node(f"dept:{dep}", "department", dep)
        pid = f"person:{s['emp_id']}"
        node(pid, "person", s["full_name"], detail=s.get("title"), department=dep, record=s["source_record_id"])
        edge(pid, f"dept:{dep}", "works_in")
        emp[s["emp_id"]] = pid
    for s in people_rows:
        if s.get("manager_emp_id") in emp:
            edge(f"person:{s['emp_id']}", emp[s["manager_emp_id"]], "reports_to")

    k = basis.batch_seq
    with tenant_tx(conn, basis.tenant_id, principal.principal_id, k):
        latest = ("SELECT DISTINCT ON (record_id) record_id, system, kind, source_path, batch_seq"
                  " FROM ledger.visible_source_record ORDER BY record_id, version DESC")
        systems = conn.execute(f"SELECT system, count(*) AS n FROM ({latest}) x GROUP BY system").fetchall()  # noqa: S608
        recent = conn.execute(
            f"SELECT * FROM (SELECT x.*, row_number() OVER (PARTITION BY system ORDER BY batch_seq DESC, record_id)"  # noqa: S608
            f" AS rn FROM ({latest}) x) y WHERE rn <= %s", (PER_SYSTEM * 3,)).fetchall()
        issues = _claims(conn, basis.tenant_id, k, "issue") if k else {}
        from ..resolution import jira_identity
        owners = jira_identity.resolved(conn, basis.tenant_id)      # approved Jira identity -> HR emp_id only

    for s in systems:
        node(f"system:{s['system']}", "system", s["system"].replace("_", " "), count=s["n"])

    dep_graph = graph.build(conn, basis, tz) if k else {"nodes": [], "edges": [], "warnings": []}
    meta = {r["record_id"]: dict(r) for r in recent}
    for n in dep_graph["nodes"]:
        if n.get("type") == "record":
            meta.setdefault(n["id"], {"record_id": n["id"], "system": n["system"], "kind": n["kind"],
                                      "source_path": n.get("source_path")})
    linked = {n["id"] for n in dep_graph["nodes"] if n.get("type") == "record"}
    linked |= {r for i in worry_items for r in i.evidence}
    linked |= {r for d in issues.values() for r in d["_records"]}
    missing = sorted(linked - set(meta))
    if missing:
        from ..tools import _evidence
        for e in _evidence(conn, basis, missing):
            meta[e.record_id] = {"record_id": e.record_id, "system": e.system, "kind": e.kind,
                                 "source_path": e.source_path}
    ok = readable(sorted(linked | set(meta)))

    def record(rid):
        if rid not in ok or rid not in meta:
            return None
        m = meta[rid]
        nid = f"record:{rid}"
        if nid not in nodes:
            label = str(m.get("source_path") or rid).replace("\\", "/").split("/")[-1]
            node(nid, "record", label, record=rid, system=m["system"], detail=m.get("kind"))
            node(f"system:{m['system']}", "system", m["system"].replace("_", " "))
            edge(nid, f"system:{m['system']}", "from_system")
        return nid

    for rid in sorted(linked):
        record(rid)
    shown_per: dict[str, int] = defaultdict(int)
    for r in recent:
        if f"record:{r['record_id']}" not in nodes and shown_per[r["system"]] < PER_SYSTEM and record(r["record_id"]):
            shown_per[r["system"]] += 1

    # Releases and commitments, with the records that state them; figure conflicts between records.
    warn = defaultdict(list)
    for w in dep_graph.get("warnings", []):
        warn[w["commitment"]].append(w["statement"])
    for n in dep_graph["nodes"]:
        if n.get("type") == "commitment":
            node(n["id"], "commitment", n["label"], warnings=warn.get(n["label"], []))
    for e in dep_graph["edges"]:
        if e.get("relation") == "conflict":
            edge(record(e["from"]), record(e["to"]), "conflict")
        else:
            edge(e["from"], record(e["to"]), "stated_in")

    # Tracker issues: assignee (by recorded name) and the release their summary names.
    commitments = [n for n in nodes.values() if n["kind"] == "commitment"]
    for subj, d in sorted(issues.items()):
        recs = [r for r in d["_records"] if r in ok]
        if not recs:
            hidden += 1
            continue
        key = subj.split(":", 1)[1]
        emp = owner_of(owners, d)
        node(f"issue:{key}", "issue", key, detail=d.get("summary"), status=d.get("status_at_export"),
             due=d.get("due_date"), assignee=(nodes.get(f"person:{emp}") or {}).get("label") if emp else None,
             assignee_unresolved=None if emp else (d.get("assignee_name") or d.get("assignee_source_id")))
        for r in recs:
            edge(f"issue:{key}", record(r), "stated_in")
        if emp:                                   # only a verified/administrator-approved identity links a person
            edge(f"issue:{key}", f"person:{emp}", "assigned_to")
        summ = str(d.get("summary") or "").lower()
        for c in commitments:
            first = c["label"].split()[0].lower()
            if len(first) > 2 and re.search(rf"\b{re.escape(first)}\b", summ):
                edge(f"issue:{key}", c["id"], "mentions")

    for subj, d in issues.items():                # "Blocked by" as recorded in the tracker
        for b in str(d.get("blocked_by") or "").replace(";", ",").split(","):
            if b.strip():
                edge(f"issue:{subj.split(':', 1)[1]}", f"issue:{b.strip()}", "blocked_by")

    # Concerns (the worry list) and the records they rest on.
    for i in worry_items:
        if not set(i.evidence) <= ok:
            hidden += 1
            continue
        nid = f"concern:{i.item_id}"
        node(nid, "concern", i.title, detail=i.follow_up or None, unknowns=list(i.unknowns))
        for r in i.evidence:
            edge(nid, record(r), "evidence_for")
        if i.department:
            edge(nid, f"dept:{i.department}", "concerns")
        for c in commitments:
            if c["label"].lower() in i.title.lower():
                edge(nid, c["id"], "concerns")

    degree: dict[str, int] = defaultdict(int)
    for e in edges:
        degree[e["source"]] += 1
        degree[e["target"]] += 1
    out_nodes = [{**n, "degree": degree[n["id"]]} for n in nodes.values()]
    return {"nodes": out_nodes, "edges": edges, "hidden_for_access": hidden,
            "notes": ["Links are only those the admitted records state; nothing is inferred or predicted.",
                      f"Each source shows up to {PER_SYSTEM} of its most recent unlinked records; the source node "
                      "gives the full count."],
            "read_basis": {"batch_seq": k, "banner": basis.banner()}}
