"""Declared agent tools (ARCHITECTURE.md §10.6, §14.3): read-only lookups over EXISTING governed Veloxa services.

Every tool declares a stable id, description, input and output schemas, the capability it needs, the data classes
it may return, its purpose, a timeout and that it is audited. Agents never reach PostgreSQL directly and never
author SQL: a tool is a Python function over the declared query operations (`veloxa.tools`), the conflict register
and the Today view. There are NO action tools (no send, create, approve or modify).

A source that is not admitted is never exposed as working: such a tool is declared with an availability other than
`available` and is refused before it runs (e.g. on-call coverage: its source is not admitted).
Payroll is not an agent tool: protected aggregates go only through C19 on the interviewer's ledger, which the agent
runtime does not wire yet, so a payroll request is refused (fail closed).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Literal

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from .. import tools as vt
from ..temporal import ReadBasis

INTERVIEW_CLASSES = frozenset({"internal", "confidential", "personal", "private_comms"})


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(_In):
    pass


class HeadcountIn(_In):
    by: Literal["department"] | None = None


class ProfileIn(_In):
    name: str = Field(min_length=1, max_length=80)


class SpendIn(_In):
    by: list[Literal["cost_centre", "month", "vendor"]] = Field(default_factory=list, max_length=3)
    cost_centre: str | None = Field(default=None, max_length=40, pattern=r"^[A-Za-z0-9&-]+$")


class TimelineIn(_In):
    start: date | None = None
    end: date | None = None


class BoardIn(_In):
    figure: str | None = Field(default=None, max_length=60)


class SearchIn(_In):
    query: str = Field(min_length=2, max_length=120)
    limit: int = Field(default=5, ge=1, le=10)


class FactOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fact_id: str
    display: str
    kind: str
    inputs: list[str]


class EvidenceOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    record_id: str
    kind: str
    system: str
    data_class: str
    batch_seq: int
    excerpt: str


class ToolOutput(BaseModel):
    """The single output schema every agent tool must satisfy (validated on receipt)."""
    model_config = ConfigDict(extra="forbid")
    tool: str
    status: Literal["ok", "no_data", "unresolved", "ambiguous", "withheld", "unsupported"]
    facts: list[FactOut]
    evidence: list[EvidenceOut]
    gaps: list[str]
    reason: str | None = None
    self_records: list[str] = Field(default_factory=list)   # self.profile only: the agent's own HR record (+ manager)


@dataclass(frozen=True)
class ToolContext:
    conn: psycopg.Connection
    tenant_id: object
    basis: ReadBasis
    principal: vt.Principal          # the INTERVIEWER: tools run under the interviewer's authorization
    tz: str
    agent_id: str | None = None      # the agent being interviewed (used only by self.profile)


@dataclass(frozen=True)
class ToolSpec:
    tool_id: str
    description: str
    capability: str
    input_model: type[BaseModel]
    fn: Callable[[ToolContext, BaseModel], dict]
    data_classes: frozenset[str] = INTERVIEW_CLASSES
    purpose: str = "interview"
    timeout_s: float = 10.0
    audited: bool = True
    availability: Literal["available", "blocked_by_admission"] = "available"
    read_only: bool = True


def _out(r: vt.ToolResult, tool_id: str) -> dict:
    return {"tool": tool_id, "status": r.status,
            "facts": [{"fact_id": f.fact_id, "display": f.display, "kind": f.kind, "inputs": list(f.inputs)}
                      for f in r.facts],
            "evidence": [{"record_id": e.record_id, "kind": e.kind, "system": e.system, "data_class": e.data_class,
                          "batch_seq": e.batch_seq, "excerpt": e.excerpt} for e in r.evidence],
            "gaps": list(r.gaps), "reason": r.reason}


def _headcount(ctx: ToolContext, a: HeadcountIn) -> dict:
    return _out(vt.headcount(ctx.conn, ctx.basis, ctx.principal, by=a.by), "people.headcount")


def _profile(ctx: ToolContext, a: ProfileIn) -> dict:
    return _out(vt.person(ctx.conn, ctx.basis, ctx.principal, a.name), "people.profile")


def _spend(ctx: ToolContext, a: SpendIn) -> dict:
    filters = {"cost_centre": a.cost_centre} if a.cost_centre else None
    return _out(vt.finance_totals(ctx.conn, ctx.basis, ctx.principal, dims=list(a.by) or None, filters=filters),
                "finance.ledger_spend")


def _timeline(ctx: ToolContext, a: TimelineIn) -> dict:
    return _out(vt.timeline(ctx.conn, ctx.basis, ctx.principal, a.start, a.end), "delivery.timeline")


def _board(ctx: ToolContext, a: BoardIn) -> dict:
    return _out(vt.board_statements(ctx.conn, ctx.basis, ctx.principal, a.figure), "delivery.board_statements")


def _search(ctx: ToolContext, a: SearchIn) -> dict:
    return _out(vt.text_search(ctx.conn, ctx.basis, ctx.principal, a.query, a.limit), "evidence.search")


def _conflicts(ctx: ToolContext, _: NoArgs) -> dict:
    from ..ask import conflicts

    found = conflicts.register(ctx.conn, ctx.basis, ctx.tz) if ctx.basis.batch_seq > 0 else []
    facts, ids = [], []
    for c in found:
        sides = "; ".join(f"{s['value']} ({s['source']})" for s in c["claims"])
        inputs = [s["record_id"] for s in c["claims"]]
        ids += inputs
        facts.append({"fact_id": f"conflict:{c['conflict_id']}", "kind": "record", "inputs": inputs,
                      "display": f"The sources disagree on {c['subject']}: {sides}. No value is chosen."})
    ev = vt._evidence(ctx.conn, ctx.basis, sorted(set(ids))) if ids else []
    return {"tool": "conflicts.register", "status": "ok" if facts else "no_data", "facts": facts,
            "evidence": _out(vt.ToolResult("x", "ok", evidence=ev), "x")["evidence"],
            "gaps": [] if facts else ["no source disagreement is visible at this read basis"], "reason": None}


def _today(ctx: ToolContext, _: NoArgs) -> dict:
    from ..risk import briefing, detectors, governance

    policy = governance.current_policy(ctx.conn, ctx.tenant_id)
    mappings = detectors.load_mappings()
    body = briefing.build(ctx.conn, ctx.principal, ctx.basis, mappings, policy)
    today = briefing.today_section(body)
    facts, ids = [], []
    for i, item in enumerate(today["items"]):
        ids += item["evidence"]
        facts.append({"fact_id": f"today:{i}", "kind": "record", "inputs": list(item["evidence"]),
                      "display": f"{item['title']} ({item['why_shown']})"})
    ev = vt._evidence(ctx.conn, ctx.basis, sorted(set(ids))) if ids else []
    return {"tool": "briefing.today", "status": "ok" if facts else "no_data", "facts": facts,
            "evidence": _out(vt.ToolResult("x", "ok", evidence=ev), "x")["evidence"],
            "gaps": list(today.get("notes", [])) if not facts else [], "reason": None}


def _self_profile(ctx: ToolContext, _: NoArgs) -> dict:
    """The interviewed agent's OWN represented person, from the reviewed mapping (runtime.agent_person, owner-approved
    by exact employee ID or e-mail). Work facts from the HR record only: title, department, recorded status, join
    date, reporting line, exit date. No pay, leave or private field exists here; persona assumptions are not facts
    and are never returned as facts. Another person's record is cited only for the manager's name and title."""
    from .. import schema
    from . import people

    out = {"tool": "self.profile", "status": "no_data", "facts": [], "evidence": [], "gaps": [], "reason": None,
           "self_records": []}
    if not ctx.agent_id or not ctx.basis.batch_seq or not schema.available(ctx.conn, "company_intelligence"):
        out["gaps"] = ["this agent is not mapped to a person"]
        return out
    from ..db import tenant_tx

    with tenant_tx(ctx.conn, ctx.tenant_id, batch_seq=ctx.basis.batch_seq):     # FORCE RLS: tenant-scoped reads
        ap = ctx.conn.execute("SELECT person_record FROM runtime.agent_person WHERE agent_id = %s ORDER BY version"
                              " DESC LIMIT 1", (ctx.agent_id,)).fetchone()
        staff = people._people_at(ctx.conn, ctx.tenant_id, ctx.basis.batch_seq)
    me = next((p for p in staff if ap and p["source_record_id"] == ap["person_record"]), None)
    if me is None:
        out["gaps"] = ["the represented person's HR record is not visible at this knowledge point"]
        return out
    rid, name = me["source_record_id"], me["full_name"]

    def day(d) -> str:
        return f"{d.day} {d.strftime('%B %Y')}"

    facts = [("self.identity", f"I'm the AI representation of {name}, built from their HR record", [rid]),
             ("self.title", f"My job title is {me['title']}", [rid]),
             ("self.department", f"I'm part of the {me['department']} department", [rid]),
             ("self.status", f"My HR record lists my employment status as {me['status']}, as at the export date",
              [rid]),
             ("self.join_date", f"I joined on {day(me['join_date'])}", [rid])]
    mgr_rec = None
    if me["manager_emp_id"]:
        mgr = next((p for p in staff if p["emp_id"] == me["manager_emp_id"]), None)
        if mgr:
            mgr_rec = mgr["source_record_id"]
            facts.append(("self.manager", f"I report to {mgr['full_name']}, the {mgr['title']}", [rid, mgr_rec]))
        else:
            facts.append(("self.manager", f"I report to employee {me['manager_emp_id']}, who is not in this HR extract",
                          [rid]))
    if me["exit_date"]:
        facts.append(("self.exit_date", f"My HR record shows an exit date of {day(me['exit_date'])}", [rid]))
    ids = sorted({i for _, _, inputs in facts for i in inputs})
    ev = vt._evidence(ctx.conn, ctx.basis, ids)
    out.update(status="ok", self_records=[rid] + ([mgr_rec] if mgr_rec else []),
               facts=[{"fact_id": fid, "display": d, "kind": "record", "inputs": inputs} for fid, d, inputs in facts],
               evidence=[{"record_id": e.record_id, "kind": e.kind, "system": e.system, "data_class": e.data_class,
                          "batch_seq": e.batch_seq, "excerpt": e.excerpt} for e in ev],
               gaps=[])
    return out


def _not_admitted(ctx: ToolContext, _: NoArgs) -> dict:          # never reached: refused by availability
    raise RuntimeError("a tool whose source is not admitted must not run")


REGISTRY: dict[str, ToolSpec] = {s.tool_id: s for s in (
    ToolSpec("people.headcount", "People in the HR export, by recorded status (optionally by department)",
             "people_directory", HeadcountIn, _headcount),
    ToolSpec("people.profile", "Work facts of one named person from the HR export at the read basis",
             "people_directory", ProfileIn, _profile),
    ToolSpec("finance.ledger_spend", "Ledger spend from the governed metric (payroll lines excluded)",
             "finance_ledger", SpendIn, _spend),
    ToolSpec("delivery.timeline", "Dated events from records (joins, recorded exits, documents issued)",
             "delivery", TimelineIn, _timeline),
    ToolSpec("delivery.board_statements", "Figures reported in board updates (reported, not verified)",
             "delivery", BoardIn, _board),
    ToolSpec("conflicts.register", "Source disagreements visible at the read basis (no value chosen)",
             "conflicts", NoArgs, _conflicts),
    ToolSpec("briefing.today", "At most three evidence-backed attention items (unranked while D-15 is pending)",
             "briefing", NoArgs, _today, timeout_s=30.0),
    ToolSpec("self.profile", "The interviewed agent's own HR work facts (reviewed mapping; not the directory)",
             "self_profile", NoArgs, _self_profile),
    ToolSpec("evidence.search", "Lexical search over admitted documents the interviewer may see",
             "evidence_search", SearchIn, _search),
    ToolSpec("coverage.oncall", "Scheduled on-call coverage (its source is not admitted yet)",
             "coverage", NoArgs, _not_admitted, availability="blocked_by_admission"),
)}
