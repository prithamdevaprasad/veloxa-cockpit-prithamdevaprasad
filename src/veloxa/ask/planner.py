"""Entity linking and bounded planning (ARCHITECTURE.md §13.1 stages 4-5, §13.2).

v0 planner is DETERMINISTIC: keyword intents map to declared operations (tools.TOOLS) with validated arguments.
It is the facts-only path of C16; the model planner (schema-constrained, same declared operations, same bounds)
is added behind this interface later. Either way:
- only declared operations, at most MAX_STEPS per request, arguments validated against each tool's schema;
- no SQL, no free-form tools;
- the interpretation is echoed ("I read this as ...").

Entity linking is code: names are matched against the HR export at the read basis. Full name or employee ID
first; a first name only if it is unique; otherwise the mention is passed on as written and the person tool
reports it as ambiguous or unresolved. Identities are never guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from .. import tools
from .timeparse import find_dates

MAX_STEPS = 3

# Argument schema per declared operation: name -> allowed values (None = free text, validated by the tool).
ARG_SCHEMA: dict[str, dict[str, object]] = {
    "person": {"name": None},
    "headcount": {"by": {"department"}},
    "finance_totals": {"dims": {"cost_centre", "month", "vendor"}, "filters": {"cost_centre", "vendor"}},
    "payroll_total": {},
    "board_statements": {"figure": None},
    "timeline": {"start": date, "end": date},
    "conflicts": {},
    "text_search": {"query": None},
}


@dataclass(frozen=True)
class Step:
    tool: str
    args: dict = field(default_factory=dict)
    why: str = ""


@dataclass
class Plan:
    steps: list[Step]
    interpretation: str
    mentions: list[str] = field(default_factory=list)      # person mentions as written in the question
    focus_terms: list[str] = field(default_factory=list)   # content words, used to select relevant facts


class PlanError(ValueError):
    pass


def validate(step: Step) -> None:
    if step.tool not in tools.TOOLS or step.tool not in ARG_SCHEMA:
        raise PlanError(f"undeclared operation {step.tool!r}")
    schema = ARG_SCHEMA[step.tool]
    for k, v in step.args.items():
        if k not in schema:
            raise PlanError(f"{step.tool}: undeclared argument {k!r}")
        allowed = schema[k]
        if allowed is None:
            if not isinstance(v, str) or not v.strip() or len(v) > 200:
                raise PlanError(f"{step.tool}.{k}: expected short text")
        elif allowed is date:
            if not isinstance(v, date) or isinstance(v, datetime):
                raise PlanError(f"{step.tool}.{k}: expected a date")
        elif isinstance(v, (list, tuple)):
            if any(x not in allowed for x in v):
                raise PlanError(f"{step.tool}.{k}: value not allowed")
        elif isinstance(v, dict):
            if any(x not in allowed or not isinstance(y, str) for x, y in v.items()):
                raise PlanError(f"{step.tool}.{k}: value not allowed")
        elif v not in allowed:
            raise PlanError(f"{step.tool}.{k}: value not allowed")


_STOP = set("""a an and are as at be by can did do does for from had has have how i in is it its me my of on or our
show tell that the their them there these this those to us was we were what when where which who whom why with you
your about any all give list please much many there's what's who's know known current currently now today latest
""".split())

_INTENTS: list[tuple[str, str]] = [
    ("payroll_total", r"\b(payroll|salar(y|ies)|wage bill|people cost|staff cost)\b"),
    ("headcount", r"\b(headcount|head count|how many (people|employees|staff|persons|heads|members)|number of "
                  r"(people|employees|staff|heads)|team sizes?|staff (count|numbers?|size)|size of the (team|company|"
                  r"organi[sz]ation|workforce)|how (big|large) is the (team|company|organi[sz]ation|workforce)|"
                  r"workforce size|employee count|people do we (have|employ)|employ(ees)? do we have|"
                  r"(largest|biggest|smallest) (department|team)s?|which (department|team) is (the )?(largest|"
                  r"biggest|smallest))\b"),
    ("finance_totals", r"\b(spend|spent|spending|expenses?|expenditure|outgoings|outflows?|costs?|ledger|vendors?|"
                       r"suppliers?|payees?|paying (our )?(vendors|suppliers)|paid to|cost cent(re|er)s?|"
                       r"general ledger|gl)\b"),
    ("board_statements", r"\b(board|investors?|reported|key numbers|revenue|arr|mrr|runway|customers?|"
                         r"burn rate|cash)\b"),
    ("conflicts", r"\b(conflicts?|disagree\w*|contradict\w*|inconsisten\w*|discrepanc\w*|sources? differ)\b"),
    ("timeline", r"\b(timeline|what happened|events?|when did|history|chronolog\w*|joined|joins|exits?|"
                 r"key dates|sequence of events|what (has )?changed)\b"),
    ("person", r"\b(who is|who's|reports? to|manager of|title of|role of|position of|works? (as|in)|"
               r"line manager|boss of|job title)\b"),
]


def _mentions(question: str, people: list[dict]) -> list[str]:
    """Person mentions in the question: full names, employee IDs, then first names (as written)."""
    low = question.lower()
    found: list[str] = []
    taken: list[tuple[int, int]] = []

    def add(span: tuple[int, int], text: str) -> None:
        if not any(s < span[1] and span[0] < e for s, e in taken):
            taken.append(span)
            found.append(text)

    for p in sorted(people, key=lambda x: -len(x["full_name"])):
        for key in (p["full_name"], p["emp_id"]):
            m = re.search(rf"\b{re.escape(key.lower())}\b", low)
            if m:
                add(m.span(), question[m.start():m.end()])
    firsts = {p["full_name"].split()[0].lower() for p in people if len(p["full_name"].split()[0]) > 2}
    for m in re.finditer(r"\b[A-Za-z][a-z]+\b", question):
        if m.group(0).lower() in firsts:
            add(m.span(), m.group(0))
    # "who is <Capitalised Name>" for a name not in the export: passed on as written -> unresolved
    m = re.search(r"\b(?i:who)\s+(?i:is)\s+(?P<n>[A-Z][a-zA-Z.'-]+(?:\s+[A-Z][a-zA-Z.'-]+)?)", question)
    if m and not found:
        add(m.span("n"), m.group("n"))
    return found


def focus_terms(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z][a-z0-9-]{2,}", text.lower()) if w not in _STOP]


IDENTIFIER = re.compile(r"\b[A-Z][A-Z0-9]{1,}-[A-Z0-9]{1,6}\b")
FOREIGN = re.compile(r"\b(competitors?'?s?|rivals?'?s?|other compan(y|ies)|another company|industry (average|"
                     r"benchmark)s?|market leader|peers?'?s?)\b", re.I)
EVENT = re.compile(r"^\s*(did|has|have|had|was|were|is|are)\b.{0,80}\b(approv\w*|decid\w*|agree\w*|sign\w*|"
                   r"open\w*|launch\w*|announc\w*|hire[ds]?|fire[ds]?|acquir\w*|merg\w*|vot\w*|reject\w*)\b", re.I)
FIGURE_ASK = re.compile(r"\b(how (much|many)|what (is|was|are|were) (our|the)\b.{0,30}\b(figure|number|amount|"
                        r"total|value)|how big)\b", re.I)


def plan(question: str, people: list[dict], default_year: int, tz: str) -> Plan:
    q = question.strip()
    mentions = _mentions(q, people)
    steps: list[Step] = []
    for tool, rx in _INTENTS:
        if not re.search(rx, q, re.IGNORECASE):
            continue
        # Our reported figures answer questions ABOUT OUR FIGURES only: never a question about another
        # organisation ("competitor's revenue"), and never a yes/no question about an event ("did the board approve
        # a London office?"), which a list of figures would appear to answer while saying nothing about it.
        if tool in ("board_statements", "finance_totals", "headcount") and (FOREIGN.search(q) or (
                EVENT.search(q) and not FIGURE_ASK.search(q))):
            continue
        if tool == "headcount":
            named_dept = any(p.get("department") and re.search(rf"\b{re.escape(p['department'])}\b", q, re.I)
                             for p in people)
            big = re.search(r"\b(largest|biggest|smallest)\b", q, re.I)
            per_dept = re.search(r"\b(by|per|each|across) (department|team|function)s?\b", q, re.I)
            by = "department" if named_dept or big or per_dept else None
            steps.append(Step("headcount", {"by": by} if by else {}, "headcount question"))
        elif tool == "finance_totals":
            dims = [d for d, rx2 in (("vendor", r"\b(by|per|each) (vendor|supplier|payee)s?\b"),
                                     ("cost_centre", r"\b(by|per|each) cost cent(re|er)s?\b"),
                                     ("month", r"\b(by|per|each) month\b|\bmonthly\b")) if re.search(rx2, q, re.I)]
            filters = {}
            cc = (re.search(r"\b(CC-[A-Z0-9]+)\b", q)
                  or re.search(r"\bcost cent(?:re|er)\s+([A-Z][A-Z0-9&]*(?:-[A-Z0-9&]+)*)(?![\w&-])", q))
            if cc:
                filters["cost_centre"] = cc.group(1)
            args = ({"dims": dims} if dims else {}) | ({"filters": filters} if filters else {})
            steps.append(Step("finance_totals", args, "spend question"))
        elif tool == "timeline":
            args = {}
            dates = [d for d, _ in find_dates(q, default_year, tz)]
            days = [d.date() if isinstance(d, datetime) else d for d in dates]
            if re.search(r"\b(between|from)\b", q, re.I) and len(days) >= 2:
                args = {"start": min(days[:2]), "end": max(days[:2])}
            steps.append(Step("timeline", args, "dated events"))
        elif tool == "person":
            if mentions:
                steps.append(Step("person", {"name": mentions[0]}, "a named person"))
        else:
            steps.append(Step(tool, {}, f"{tool.replace('_', ' ')} question"))
    if mentions and not any(s.tool in ("person", "timeline") for s in steps):
        steps.insert(0, Step("person", {"name": mentions[0]}, "a named person"))
    terms = focus_terms(q)
    if not steps and terms:
        steps.append(Step("text_search", {"query": " OR ".join(dict.fromkeys(terms[:8]))}, "free-text question"))
    else:
        # A record identifier (LIN-001, CMP-04, NIM-E1) or, with the generic timeline intent, a named subject
        # ("What happened to Quartz 2.0?") is looked up directly as well: the intent alone would answer a
        # different question.
        used = {str(v) for s in steps for a in s.args.values() for v in (a.values() if isinstance(a, dict) else [a])}
        ids = [i for i in IDENTIFIER.findall(q) if i not in used]          # not an identifier already a filter
        named = [w for w in re.findall(r"\b[A-Z][a-zA-Z0-9]{2,}(?:\s+\d+(?:\.\d+)?)?\b", q)[1:]
                 if not any(w.lower().startswith(m.lower()) for m in mentions)]
        generic_timeline = any(s.tool == "timeline" and not s.args for s in steps)
        subject = ids or (named if generic_timeline else [])
        if subject and not any(s.tool == "text_search" for s in steps):
            if generic_timeline and not mentions:
                # "What happened in LIN-001?" is about that record, not the company's whole dated history.
                steps = [s for s in steps if not (s.tool == "timeline" and not s.args)]
            steps.append(Step("text_search", {"query": " OR ".join(dict.fromkeys(subject))[:200]},
                              "a named record or subject"))
    steps = steps[:MAX_STEPS]
    for s in steps:
        validate(s)
    reading = "; ".join(_describe(s) for s in steps) or "no declared operation matches this question"
    return Plan(steps, f"I read this as: {reading}.", mentions, terms)


def _describe(s: Step) -> str:
    a = s.args
    if s.tool == "person":
        return f"facts about the person named {a['name']!r} in the HR export"
    if s.tool == "headcount":
        return "headcount from the HR export" + (" by department" if a.get("by") else "")
    if s.tool == "finance_totals":
        extra = (" by " + ", ".join(a["dims"]) if a.get("dims") else "") + \
                (" for " + ", ".join(f"{k} {v}" for k, v in a["filters"].items()) if a.get("filters") else "")
        return "ledger spend (governed metric ledger_spend)" + extra
    if s.tool == "payroll_total":
        return "a payroll total (a protected aggregate)"
    if s.tool == "board_statements":
        return "figures as reported in formal board updates"
    if s.tool == "timeline":
        return "dated events" + (f" between {a['start']} and {a['end']}" if a else "")
    if s.tool == "conflicts":
        return "owner-reviewed conflicts between sources"
    words = [w for w in str(a.get("query", "")).split(" OR ") if w][:4]
    return "passages that mention " + (", ".join(words) if words else "your question")
