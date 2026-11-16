"""Declared query operations (ARCHITECTURE.md §13.1 stage 5-6, §13.2): the only things a planner may call.

Every tool:
- runs under the application role inside the tenant context at the read basis's K (RLS, protected-pay policies
  and sealed-batch visibility all apply);
- takes every number from a governed metric (metrics.py) or returns record values as written;
- returns facts with lineage (source record ids) and the evidence records behind them;
- never guesses an identity: a name either matches exactly, matches a unique first name, or is reported as
  unresolved or ambiguous;
- applies source-faithful authorization to communications: an email (private_comms) is usable only if the
  requesting principal is one of its parties (§10, §13.6). Messages between other people are excluded
  (the policy for executives reading them, D-13b, is deferred and therefore not granted).

Hiring depends on the D2 release pack and is added with D2 admission.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import psycopg
from psycopg import sql as psql

from . import metrics
from .db import tenant_tx
from .temporal import ReadBasis, predicate


@dataclass(frozen=True)
class Principal:
    principal_id: str
    role: str            # e.g. "executive"
    email: str | None    # used for party-based authorization of communications


@dataclass
class Evidence:
    record_id: str
    kind: str
    system: str
    data_class: str
    batch_seq: int
    source_path: str
    locator: dict
    excerpt: str


@dataclass
class Fact:
    fact_id: str
    display: str
    value: object
    unit: str | None
    kind: str                  # "derived" (governed metric) | "record" (value as written) | "document" (reported)
    inputs: list[str]
    metric: str | None = None
    qualifiers: list[str] = field(default_factory=list)


@dataclass
class ToolResult:
    tool: str
    status: str                # ok | no_data | unresolved | ambiguous | withheld | unsupported
    facts: list[Fact] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    reason: str | None = None


def _evidence(conn: psycopg.Connection, basis: ReadBasis, record_ids: list[str], excerpts: dict | None = None,
              ) -> list[Evidence]:
    if not record_ids:
        return []
    with tenant_tx(conn, basis.tenant_id, batch_seq=basis.batch_seq):
        rows = conn.execute(
            "SELECT record_id, kind, system, data_class, batch_seq, source_path, locator"
            " FROM ledger.visible_source_record WHERE record_id = ANY(%s) ORDER BY record_id",
            (sorted(set(record_ids)),),
        ).fetchall()
    out = []
    for r in rows:
        loc = r["locator"]
        default = f"{r['source_path']} row {loc['row']}" if "row" in loc else r["source_path"]
        out.append(Evidence(r["record_id"], r["kind"], r["system"], r["data_class"], r["batch_seq"],
                            r["source_path"], loc, (excerpts or {}).get(r["record_id"], default)))
    return out


def _person_snapshot(conn: psycopg.Connection, basis: ReadBasis) -> list[dict]:
    p = predicate(basis, "p")
    with tenant_tx(conn, basis.tenant_id, batch_seq=basis.batch_seq):
        return conn.execute(
            psql.SQL(
                'SELECT * FROM projection.person "p" WHERE {} AND "p"."batch_seq" = '
                '(SELECT max(batch_seq) FROM projection.person WHERE batch_seq <= %(k)s)').format(p.clause),
            p.params,
        ).fetchall()


def resolve_principal_by_title(conn: psycopg.Connection, basis: ReadBasis, title: str) -> Principal | None:
    """Resolve the requesting principal (v0: a configured role title, e.g. 'CEO'), only if exactly one person holds
    that title. No identity is guessed."""
    matches = [p for p in _person_snapshot(conn, basis) if p["title"] == title]
    if len(matches) != 1:
        return None
    return Principal(matches[0]["entity_id"], "executive", (matches[0]["work_email"] or "").lower() or None)


def resolve_principal_by_emp_id(conn: psycopg.Connection, basis: ReadBasis, emp_id: str) -> Principal | None:
    """Resolve an AUTHENTICATED principal's employee ID to exactly one person in the HR export at the basis.
    The ID comes from server-side authentication, never from the request."""
    matches = [p for p in _person_snapshot(conn, basis) if p["emp_id"] == emp_id]
    if len(matches) != 1:
        return None
    return Principal(matches[0]["entity_id"], "authenticated", (matches[0]["work_email"] or "").lower() or None)


def person(conn: psycopg.Connection, basis: ReadBasis, principal: Principal, name: str) -> ToolResult:
    people = _person_snapshot(conn, basis)
    if not people:
        return ToolResult("person", "no_data", gaps=["no HR export admitted at this knowledge point"])
    key = name.strip().lower()
    exact = [p for p in people if p["full_name"].lower() == key or p["emp_id"].lower() == key]
    by_first_name = not exact
    if by_first_name:
        exact = [p for p in people if p["full_name"].split()[0].lower() == key]
    if not exact:
        return ToolResult("person", "unresolved", reason=f"no person in the HR export matches {name!r} exactly; "
                                                         "spelling variants are not resolved automatically")
    if len(exact) > 1:
        return ToolResult("person", "ambiguous",
                          reason=f"{len(exact)} people match {name!r}; the identity is not guessed")
    p = exact[0]
    by_id = {x["emp_id"]: x for x in people}
    rid = [p["source_record_id"]]
    q = ["as at extraction of the HR export"]
    if by_first_name:  # a unique first-name match is medium-confidence identity resolution (§13.5 grade B)
        q.append(f"identity assumed: {name.strip()!r} matched only {p['full_name']}'s first name")
    facts = [
        Fact("person.title", f"{p['full_name']}: {p['title']}, {p['department']}", p["title"], None, "record", rid,
             qualifiers=q),
        Fact("person.join_date", f"{p['full_name']} joined on {p['join_date'].isoformat()}", p["join_date"], None,
             "record", rid, qualifiers=q[1:]),
        Fact("person.status", f"{p['full_name']}'s recorded status: {p['status']}", p["status"], None, "record", rid,
             qualifiers=q),
    ]
    manager = by_id.get(p["manager_emp_id"]) if p["manager_emp_id"] else None
    if manager:
        facts.append(Fact("person.manager", f"{p['full_name']} reports to {manager['full_name']} ({manager['title']})",
                          manager["full_name"], None, "record", rid + [manager["source_record_id"]], qualifiers=q))
    if p["exit_date"]:
        facts.append(Fact("person.exit_date", f"{p['full_name']}'s recorded exit date: {p['exit_date'].isoformat()}",
                          p["exit_date"],
                          None, "record", rid, qualifiers=q))
    if p["location"]:
        facts.append(Fact("person.location", f"{p['full_name']}'s location: {p['location']}", p["location"], None,
                          "record", rid,
                          qualifiers=q))
    ids = sorted({i for f in facts for i in f.inputs})
    return ToolResult("person", "ok", facts, _evidence(conn, basis, ids))


def _metric_facts(result: metrics.MetricResult, label) -> list[Fact]:
    d = result.definition
    return [Fact(f"{d.name}:{i}", label(r), r.value, d.unit, "derived" if not d.measure.startswith("reported")
                 else "document", r.inputs, d.ref, list(result.qualifiers)) for i, r in enumerate(result.rows)]


def finance_totals(conn: psycopg.Connection, basis: ReadBasis, principal: Principal, dims: list[str] | None = None,
                   filters: dict | None = None) -> ToolResult:
    defs = metrics.load_definitions()
    try:
        res = metrics.run(conn, defs["ledger_spend"], basis, dims or [], filters or {})
    except metrics.MetricError as e:
        return ToolResult("finance_totals", "unsupported", reason=str(e))
    if not res.rows:
        return ToolResult("finance_totals", "no_data", gaps=["no ledger lines match at this knowledge point"])

    def label(r):
        scope = ", ".join(f"{k} {v}" for k, v in r.dims.items()) or "all extracted cost centres"
        return f"Ledger spend ({scope}): INR {r.value:,}"
    facts = _metric_facts(res, label)
    ids = sorted({i for f in facts for i in f.inputs})
    return ToolResult("finance_totals", "ok", facts, _evidence(conn, basis, ids),
                      gaps=["the ledger extract covers selected cost centres only"])


def headcount(conn: psycopg.Connection, basis: ReadBasis, principal: Principal, by: str | None = None) -> ToolResult:
    """People in the HR export (governed metric headcount@1), in total and by recorded status, optionally also by
    department. Status is shown as recorded; nothing is inferred about who is active."""
    defs = metrics.load_definitions()
    dims = ["status"] + ([by] if by == "department" else [])
    try:
        total = metrics.run(conn, defs["headcount"], basis)
        split = metrics.run(conn, defs["headcount"], basis, dims)
    except metrics.MetricError as e:
        return ToolResult("headcount", "unsupported", reason=str(e))
    if not total.rows:
        return ToolResult("headcount", "no_data", gaps=["no HR export admitted at this knowledge point"])
    facts = _metric_facts(total, lambda r: f"People in the HR export: {r.value}")
    for i, f in enumerate(_metric_facts(split, lambda r: "People in the HR export with " + ", ".join(
            f"{k} {v}" for k, v in r.dims.items()) + f": {r.value}")):
        f.fact_id = f"headcount.split:{i}"
        facts.append(f)
    return ToolResult("headcount", "ok", facts, _evidence(conn, basis, sorted({i for f in facts for i in f.inputs})))


def payroll_total(conn: psycopg.Connection, basis: ReadBasis, principal: Principal, **_) -> ToolResult:
    defs = metrics.load_definitions()
    try:
        metrics.run(conn, defs["payroll_total"], basis)
    except metrics.MetricError as e:
        return ToolResult("payroll_total", "withheld", reason=e.reason)
    return ToolResult("payroll_total", "withheld", reason="disclosure_auditor_unavailable")  # never reached


def board_statements(conn: psycopg.Connection, basis: ReadBasis, principal: Principal,
                     figure: str | None = None) -> ToolResult:
    defs = metrics.load_definitions()
    filters = {"metric": figure} if figure else {}
    try:
        res = metrics.run(conn, defs["reported_figure"], basis, ["metric", "period"], filters)
    except metrics.MetricError as e:
        return ToolResult("board_statements", "unsupported", reason=str(e))
    if not res.rows:
        return ToolResult("board_statements", "no_data", gaps=["no reported figure matches at this knowledge point"])
    facts = _metric_facts(res, lambda r: f"{r.dims['metric']} for {r.dims['period']}: {r.value} (as reported)")
    return ToolResult("board_statements", "ok", facts,
                      _evidence(conn, basis, sorted({i for f in facts for i in f.inputs})))


def text_search(conn: psycopg.Connection, basis: ReadBasis, principal: Principal, query: str,
                limit: int = 5) -> ToolResult:
    p = predicate(basis, "d")
    sql = psql.SQL(
        'SELECT "d"."source_record_id", "d"."title",'
        " ts_headline('english', \"d\".\"body\", q, 'StartSel=[,StopSel=],MaxWords=25,MinWords=8') AS excerpt,"
        ' ts_rank("d"."tsv", q) AS rank'
        ' FROM projection.document_text "d", websearch_to_tsquery(\'english\', %(q)s) q'
        ' WHERE {} AND "d"."tsv" @@ q'
        ' AND ("d"."data_class" <> \'private_comms\' OR %(email)s = ANY("d"."parties"))'
        ' ORDER BY rank DESC, "d"."source_record_id" LIMIT %(limit)s').format(p.clause)
    with tenant_tx(conn, basis.tenant_id, batch_seq=basis.batch_seq):
        rows = conn.execute(sql, {**p.params, "q": query, "email": principal.email or "", "limit": limit}).fetchall()
    if not rows:
        return ToolResult("text_search", "no_data", gaps=[f"no admitted text the requester may see matches {query!r}"])
    excerpts = {r["source_record_id"]: f"{r['title']}: {r['excerpt']}" for r in rows}
    return ToolResult("text_search", "ok", [], _evidence(conn, basis, list(excerpts), excerpts))


def timeline(conn: psycopg.Connection, basis: ReadBasis, principal: Principal, start: date | None = None,
             end: date | None = None) -> ToolResult:
    """Dated events from records: joins and recorded exits (HR), formal documents issued, and emails the requester
    was party to. Each event cites its record."""
    events: list[tuple[date, Fact]] = []
    for p in _person_snapshot(conn, basis):
        rid = [p["source_record_id"]]
        events.append((p["join_date"], Fact(f"event.join.{p['emp_id']}", f"{p['join_date']}: {p['full_name']} joined",
                                            p["join_date"], None, "record", rid)))
        if p["exit_date"]:
            events.append((p["exit_date"], Fact(f"event.exit.{p['emp_id']}",
                                                f"{p['exit_date']}: recorded exit date for {p['full_name']}",
                                                p["exit_date"], None, "record", rid, qualifiers=["as at extraction"])))
    pr = predicate(basis, "r")
    pd = predicate(basis, "d")
    with tenant_tx(conn, basis.tenant_id, batch_seq=basis.batch_seq):
        docs = conn.execute(psql.SQL(
            'SELECT DISTINCT "r"."source_record_id", "r"."issued_on", "r"."period" FROM projection.reported_figure "r"'
            ' WHERE {} AND "r"."issued_on" IS NOT NULL').format(pr.clause), pr.params).fetchall()
        mails = conn.execute(psql.SQL(
            'SELECT "d"."source_record_id", "d"."title", "d"."event_at" FROM projection.document_text "d"'
            ' WHERE {} AND "d"."kind" = \'email\' AND "d"."event_at" IS NOT NULL AND %(email)s = ANY("d"."parties")'
        ).format(pd.clause), {**pd.params, "email": principal.email or ""}).fetchall()
    for d in docs:
        events.append((d["issued_on"], Fact(f"event.doc.{d['source_record_id']}",
                                            f"{d['issued_on']}: formal update issued for {d['period']}",
                                            d["issued_on"], None, "document", [d["source_record_id"]])))
    for m in mails:
        day = m["event_at"].date()
        events.append((day, Fact(f"event.mail.{m['source_record_id']}", f"{day}: email '{m['title']}'", day, None,
                                 "record", [m["source_record_id"]])))
    events = [(d, f) for d, f in events if (start is None or d >= start) and (end is None or d <= end)]
    events.sort(key=lambda e: (e[0], e[1].fact_id))
    facts = [f for _, f in events]
    if not facts:
        return ToolResult("timeline", "no_data", gaps=["no dated events at this knowledge point"])
    return ToolResult("timeline", "ok", facts, _evidence(conn, basis, sorted({i for f in facts for i in f.inputs})))


def conflicts(conn: psycopg.Connection, basis: ReadBasis, principal: Principal, **_) -> ToolResult:
    """Owner-reviewed conflict records (§7.6). None have been authored yet; the tool says so instead of
    inventing or auto-detecting a source-truth conclusion."""
    return ToolResult("conflicts", "no_data",
                      gaps=["no owner-reviewed conflict records exist yet (conclusions are the owner's, in "
                            "TRANSCRIPT_NOTES.md)"])


TOOLS = {
    "person": person,
    "headcount": headcount,
    "finance_totals": finance_totals,
    "payroll_total": payroll_total,
    "board_statements": board_statements,
    "timeline": timeline,
    "conflicts": conflicts,
    "text_search": text_search,
}
