"""Answer pipeline v0 (ARCHITECTURE.md §13.1, C16): facts-only mode.

Stages: (1) principal and tenant are resolved by the caller -> (2) refusal pre-screen -> (3) temporal parse ->
(4) entity linking + (5) bounded deterministic plan -> (6) authorized execution of declared operations ->
(7) evidence pack -> (8) facts-only rendering -> (9) deterministic verification -> (10) grade and status ->
(11) record answer, citations and audit in one transaction.

Facts-only mode is the architecture's degraded path (C16 "model unavailable", §22.1): every claim is a verbatim
rendering of a fact or excerpt that a declared operation returned, so status is at most `qualified`. The model
planner and composer will run behind the same stages and contracts; the verifier and grading do not change.

No model writes SQL; every number comes from a governed metric or is a record value as written.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from decimal import Decimal

import psycopg

from .. import audit, ledger, tools
from ..db import tenant_tx
from ..temporal import Mode, ReadBasis, resolve_basis
from . import planner, policy, timeparse
from .verifier import PROTECTED_CLASSES, SOURCE_CLASS, Claim, verify

PIPELINE_VERSION = "ask-v0.1-facts-only"
MAX_CLAIMS = 6
FACTS_ONLY = "facts only: rendered from verified facts without model composition"
GRADE_LABEL = {"A": "High", "B": "Medium", "C": "Low", "D": "I don't know"}


class InvalidTime(ValueError):
    """Typed error `invalid_time` (§19.4)."""


def _jsonable(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else str(v)
    return v


def _people(conn: psycopg.Connection, basis: ReadBasis) -> list[dict]:
    if basis.batch_seq == 0:
        return []
    return [dict(p) for p in tools._person_snapshot(conn, basis)]


def _tokens(text: str) -> set[str]:
    return set(planner.focus_terms(text))


def _related(a: set[str], b: set[str]) -> int:
    return sum(1 for x in a if any(x == y or (min(len(x), len(y)) >= 4 and (x.startswith(y) or y.startswith(x)))
                                   for y in b))


def _select(facts: list[tuple[str, tools.Fact]], terms: list[str]) -> list[tuple[str, tools.Fact]]:
    """Keep the facts most related to the question's content words (all of them if none relate)."""
    if not terms or len(facts) <= 1:
        return facts[:MAX_CLAIMS]
    q = set(terms)
    scored = [(_related(q, _tokens(f.display)), fid, f) for fid, f in facts]
    best = max(s for s, _, _ in scored)
    return [(fid, f) for s, fid, f in scored if s == best][:MAX_CLAIMS] if best > 0 else facts[:MAX_CLAIMS]


def _claim_grade(c: Claim, facts: dict, records: dict) -> str:
    cited = [records[i] for e in c.evidence for i in ([e] if e in records else facts.get(e, {}).get("inputs", []))
             if i in records]
    classes = {SOURCE_CLASS.get(r["kind"], "document") for r in cited}
    qualifiers = [q for e in c.evidence if e in facts for q in facts[e]["qualifiers"]]
    if c.kind in ("derived", "record") and classes <= {"system_record", "message"}:
        return "B" if any(q.startswith("identity assumed") for q in qualifiers) else "A"
    if c.kind == "document" or (c.kind == "statement" and classes <= {"document"}):
        return "B"
    return "C"


def _status(refused: bool, grade: str, qualifiers: list[str], conflict: bool) -> str:
    """Status precedence (§13.5): refused > abstained > conflict > qualified > answered."""
    if refused:
        return "refused"
    if grade == "D":
        return "abstained"
    if conflict:
        return "conflict"
    if grade in ("B", "C") or qualifiers:
        return "qualified"
    return "answered"


def _basis_json(basis: ReadBasis) -> dict:
    return {"batch_seq": basis.batch_seq, "mode": str(basis.mode), "as_of": basis.as_of, "banner": basis.banner(),
            "notes": list(basis.notes)}


def answer(conn: psycopg.Connection, tenant_id: uuid.UUID, tz: str, principal: tools.Principal, question: str,
           now: datetime, *, mode: str | None = None, as_of: str | None = None,
           as_known_at: str | None = None) -> dict:
    question = question.strip()
    releases = ledger.sealed_releases(conn, tenant_id)
    current = resolve_basis(tenant_id, releases, Mode.CURRENT, None, tz, now)
    base = {"answer_id": str(uuid.uuid4()), "pipeline_version": PIPELINE_VERSION, "question": question}

    # (2) refusal pre-screen: before anything is read for the question. Names come from the latest sealed export
    # and are used only to recognise that a question targets an individual.
    refusal = policy.prescreen(question, [p["full_name"] for p in _people(conn, current)])
    if refusal:
        reading = f"I read this as a request covered by {refusal.policy.policy_id} ({refusal.policy.title})."
        return _record(conn, tenant_id, principal, now, current, base, _refused(refusal.policy, current, reading))

    # (3) temporal parse -> read basis
    try:
        spec = timeparse.parse(question, now, tz, mode, as_of, as_known_at)
        basis = resolve_basis(tenant_id, releases, spec.mode, spec.as_of, tz, now)
    except ValueError as e:
        raise InvalidTime(str(e)) from e
    if basis.nothing_admitted or basis.batch_seq == 0:
        return _record(conn, tenant_id, principal, now, basis, base, _abstained(
            basis, "I read this as a question about a time when nothing had been admitted.",
            ["nothing had been admitted at this knowledge point"] + list(basis.notes)))

    # (4)-(5) entity linking and bounded plan
    people = _people(conn, basis)
    the_plan = planner.plan(spec.remainder, people, now.year, tz)
    interpretation = the_plan.interpretation + (f" Time: {spec.phrase!r} -> {basis.banner()}." if spec.phrase
                                                else f" Time: {basis.banner()}.")

    # (6) execute declared operations (each runs under RLS at K with the principal's party filter)
    results: list[tools.ToolResult] = []
    for step in the_plan.steps:
        try:
            results.append(tools.TOOLS[step.tool](conn, basis, principal, **step.args))
        except Exception as e:  # an operation failure makes its claims unavailable, never the whole service
            results.append(tools.ToolResult(step.tool, "unsupported", reason=f"operation failed ({type(e).__name__})"))

    withheld = [r for r in results if r.status == "withheld"]
    if withheld and len(withheld) == len(results):
        return _record(conn, tenant_id, principal, now, basis, base,
                       _refused(policy.POLICIES["POL-PAY-02"], basis, interpretation))

    # (7) evidence pack: facts with pack ids, records deduplicated; protected classes excluded
    unknowns: list[str] = []
    records: dict[str, dict] = {}
    pack_facts: list[tuple[str, tools.Fact]] = []
    excerpt_claims: list[tuple[str, str]] = []
    for r in results:
        if r.status in ("unresolved", "ambiguous", "unsupported"):
            unknowns.append(f"{r.tool} ({r.status}): {r.reason}")
        elif r.status == "withheld":
            unknowns.append(f"{r.tool}: withheld ({policy.POLICIES['POL-PAY-02'].policy_id}, {r.reason})")
        unknowns += r.gaps
        for ev in r.evidence:
            if ev.data_class in PROTECTED_CLASSES:
                continue
            rec = {"record_id": ev.record_id, "kind": ev.kind, "system": ev.system,
                   "source_class": SOURCE_CLASS.get(ev.kind, "document"), "data_class": ev.data_class,
                   "batch_seq": ev.batch_seq, "source_path": ev.source_path, "locator": ev.locator,
                   "excerpt": ev.excerpt}
            records.setdefault(ev.record_id, rec)
            if r.tool == "text_search":
                excerpt_claims.append((ev.record_id, ev.excerpt))
        for f in r.facts:
            pack_facts.append((f"F{len(pack_facts) + 1}", f))
    facts = {fid: {"fact_id": fid, "display": f.display, "value": _jsonable(f.value), "unit": f.unit,
                   "kind": f.kind, "metric": f.metric, "inputs": list(f.inputs), "qualifiers": list(f.qualifiers)}
             for fid, f in pack_facts}

    # (8) facts-only rendering: each claim is one fact's display text (or one excerpt), citing it
    chosen = _select(pack_facts, the_plan.focus_terms)
    claims = [Claim(f.display, f.kind, [fid] + list(f.inputs)) for fid, f in chosen]
    claims += [Claim(text, "statement", [rid]) for rid, text in excerpt_claims[:MAX_CLAIMS - len(claims)]]

    # (9) deterministic verification against each claim's own evidence, visibility re-checked at K
    cited = sorted({i for c in claims for e in c.evidence for i in ([e] if e in records else
                                                                      facts.get(e, {}).get("inputs", []))})
    with tenant_tx(conn, tenant_id, principal.principal_id, basis.batch_seq):
        visible = {r["record_id"] for r in conn.execute(
            "SELECT record_id FROM ledger.visible_source_record WHERE record_id = ANY(%s)", (cited,))}
    outcome = verify(claims, facts, records, visible, [p["full_name"] for p in people])

    # (10) grade and status
    kept = outcome.kept
    if not kept:
        return _record(conn, tenant_id, principal, now, basis, base, _abstained(basis, interpretation, unknowns),
                       outcome.policy_events)
    grades = [_claim_grade(c, facts, records) for c in kept]
    headline = grades[0]
    qualifiers = list(dict.fromkeys([q for c in kept for e in c.evidence if e in facts
                                     for q in facts[e]["qualifiers"]] + [FACTS_ONLY]))
    status = _status(False, headline, qualifiers, conflict=False)
    shown = [c.text for c in kept[:3]]
    text = ". ".join(s.rstrip(".") for s in shown) + "."
    if len(kept) > 3:
        text += f" ({len(kept) - 3} more cited fact{'s' if len(kept) > 4 else ''} below.)"
    cited_records = sorted({i for c in kept for e in c.evidence
                            for i in ([e] if e in records else facts.get(e, {}).get("inputs", []))})
    used_facts = sorted({e for c in kept for e in c.evidence if e in facts}, key=lambda x: int(x[1:]))
    result = {
        "status": status,
        "interpretation": interpretation,
        "answer": text,
        "grade": {"value": headline, "label": GRADE_LABEL[headline],
                  "reasons": [_grade_reason(kept[0], headline)]},
        "qualifiers": qualifiers,
        "claims": [{"text": c.text, "kind": c.kind, "evidence": c.evidence, "verification": c.verification,
                    "grade": g, "notes": c.notes} for c, g in zip(kept, grades, strict=True)],
        "disagreements": [],
        "unknowns": list(dict.fromkeys(unknowns)),
        "refusal": None,
        "read_basis": _basis_json(basis),
        "facts": [facts[f] for f in used_facts],
        "evidence": [records[r] for r in cited_records],
        "verification": {"deterministic": "passed for every shown claim", "dropped_claims": len(outcome.dropped),
                         "entailment": "not run (facts-only: claims are verbatim renderings of cited evidence)"},
    }
    return _record(conn, tenant_id, principal, now, basis, base, result, outcome.policy_events)


def _grade_reason(c: Claim, grade: str) -> str:
    return {"A": f"{c.kind} claim from system records",
            "B": "formal document, first-person message or medium-confidence identity",
            "C": "statement from a transcript, message or other informal source"}.get(grade, "no usable evidence")


def _empty(basis: ReadBasis, status: str, grade: str, interpretation: str) -> dict:
    return {"status": status, "interpretation": interpretation, "answer": "", "grade": {
        "value": grade, "label": GRADE_LABEL[grade], "reasons": []}, "qualifiers": [], "claims": [],
        "disagreements": [], "unknowns": [], "refusal": None, "read_basis": _basis_json(basis), "facts": [],
        "evidence": [], "verification": None}


def _refused(p: policy.Policy, basis: ReadBasis, interpretation: str) -> dict:
    out = _empty(basis, "refused", "D", interpretation)
    out["answer"] = p.reason
    out["grade"]["reasons"] = ["refused by policy"]
    out["refusal"] = {"policy": p.policy_id, "title": p.title, "reason": p.reason, "alternative": p.alternative}
    return out


def _abstained(basis: ReadBasis, interpretation: str, unknowns: list[str]) -> dict:
    out = _empty(basis, "abstained", "D", interpretation)
    out["answer"] = "I don't know: no usable evidence answers this at this knowledge point."
    out["grade"]["reasons"] = ["no claim survived verification"]
    out["unknowns"] = list(dict.fromkeys(unknowns))
    return out


def _record(conn: psycopg.Connection, tenant_id: uuid.UUID, principal: tools.Principal, now: datetime,
            basis: ReadBasis, base: dict, result: dict, policy_events: list[dict] | None = None) -> dict:
    """(11) Record the answer, its citations and audit events in ONE transaction. Audit details hold structure
    and counts only, never question text or values."""
    answer_id = base["answer_id"]
    result = {**result, "audit_id": answer_id, "pipeline_version": base["pipeline_version"]}
    cited = sorted({e["record_id"] for e in result["evidence"]})
    policy_id = result["refusal"]["policy"] if result["refusal"] else None
    with tenant_tx(conn, tenant_id, principal.principal_id, basis.batch_seq):
        conn.execute(
            "INSERT INTO runtime.answer (tenant_id, answer_id, asked_at, principal_id, question, pipeline_version,"
            " batch_seq, mode, as_of, status, grade, policy_id, answer)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (tenant_id, answer_id, now, principal.principal_id, base["question"], base["pipeline_version"],
             basis.batch_seq, str(basis.mode), basis.as_of, result["status"], result["grade"]["value"], policy_id,
             json.dumps(result, default=_jsonable)))
        for rid in cited:
            conn.execute("INSERT INTO runtime.answer_citation (tenant_id, answer_id, record_id) VALUES (%s, %s, %s)",
                         (tenant_id, answer_id, rid))
        for ev in policy_events or []:
            audit.append(conn, tenant_id, "policy.claim_dropped", {"answer_id": answer_id, **ev})
        audit.append(conn, tenant_id, "ask.answered", {
            "answer_id": answer_id, "status": result["status"], "grade": result["grade"]["value"],
            "batch_seq": basis.batch_seq, "mode": str(basis.mode), "claims": len(result["claims"]),
            "citations": len(cited), "policy": policy_id})
    return json.loads(json.dumps(result, default=_jsonable))

