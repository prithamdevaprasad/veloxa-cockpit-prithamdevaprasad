"""Intent routing for business questions that a keyword search cannot answer (checked before the planner).

Each route answers from the subsystem that owns the answer, and every claim cites the records it rests on:
- worries: the ranked list, unrankable items, a concern on a named topic (the D-15 item builder and ranking);
- "why did <a release> move?": the recorded schedule change plus the dependency, hiring-pause and incident evidence,
  with what the records do not establish;
- "what changed in the latest board update?": reported figures compared period by period;
- "what information is missing?": the measures the worry model could not obtain and why items are unrankable;
- "why is THIS a concern?" with no concern in context: a short clarification listing the concerns;
- personal details about an employee: no verified company record exists (persona details are never facts).
Pay of a named individual is refused earlier, by the policy pre-screen.

Nothing here generates text from a model. A route that finds no supporting record returns None or an honest
"no verified record" answer; it never falls back to unrelated passages.
"""

from __future__ import annotations

import re

import psycopg

from .. import tools
from ..db import tenant_tx

NO_RECORD = "I couldn't find a verified company record for that information."
PROTECTED = {"sensitive_personal", "payroll_aggregate"}
GRADE = {"system_record": "A", "document": "B", "message": "B", "transcript": "C"}

PERSONAL = re.compile(r"\b(hobb(y|ies)|personal (life|details|information|interests)|family|married|spouse|children|"
                      r"religion|health|medical|home address|phone number|birthday|date of birth|age of)\b", re.I)
THIS_CONCERN = re.compile(r"\b(this|that|it)\b.{0,40}\b(concern|risk|worry|ranked|flagged)\b|"
                          r"\bwhy is (this|that|it) (a |ranked|flagged)", re.I)
UNRANKABLE = re.compile(r"\b(can ?not|can't|cannot|not|un)[ -]?(currently |yet )?(be )?rank(ed|able)?\b|\bunranked\b",
                        re.I)
WORRY_LIST = re.compile(r"\b(what should i worry about|what (are|is) (the |our )?(top |main |biggest )?"
                        r"(risks?|concerns?|worries))\b|\bwhat needs (my )?attention\b", re.I)
TOPIC_RISK = re.compile(r"\b(risk|concern|worry|exposure)\b", re.I)
DELAY = re.compile(r"\b(why|what caused|reason)\b.{0,60}\b(ga|launch|release|go-?live|delivery|ship\w*|"
                   r"(?:product|atlas)\b).{0,40}\b(delay\w*|slip\w*|moved?|late|postponed)\b|"
                   r"\b(delay\w*|slip\w*|moved?|postponed)\b.{0,40}\b(ga|launch|release)\b|"
                   r"\bwhy (was|did|is|has)\b.{0,60}\b(delay\w*|slip\w*|moved?|late|postponed)\b|"
                   r"\b(hiring|pause|freeze|staffing|incidents?|reliability|vendor)\b.{0,40}\b(affect\w*|impact\w*|"
                   r"delay\w*)\b.{0,40}\b(ga|launch|release|delivery|atlas|version)\b", re.I)
BOARD_CHANGE = re.compile(r"\b(what )?(changed|change[sd]?|compare[sd]?|difference)\b.{0,40}\bboard\b|"
                          r"\bboard\b.{0,40}\b(changed|change[sd]?|compared?)\b|\blatest board update\b", re.I)
MISSING = re.compile(r"\bwhat (information|data|records?|evidence) (is|are) missing\b|\bmissing (information|data|"
                     r"records|evidence)\b|\bwhat (don't|do not) we know\b|\bknowledge gaps?\b", re.I)
STATUS = re.compile(r"\b(status|progress|on track|how (is )?.{0,30} (is |are )?(going|doing|tracking)|"
                    r"where (are|is) we|update on|"
                    r"latest on|state of|when (is|was|will|did) .{0,40}\b(due|ship\w*|released?|launch\w*)|"
                    r"original (date|deadline)|"
                    r"still due|committed date|delivered on|on time|"
                    r"date (has )?(moved|changed|slipped)|date move)\b", re.I)
ROLE_HOLDER = re.compile(r"\bwho (is|are|'s) (the |our )?(?P<role>ceo|chief executive( officer)?|cfo|chief financial "
                         r"officer|cto|chief technology officer|head of [a-z &]+?|vp [a-z &]+?)\s*\??$", re.I)
BLOCKED = re.compile(r"\b(which|what|how many|list)\b.{0,30}\b(issues?|tasks?|tickets?|work|items?)\b.{0,20}"
                     r"\b(blocked|blocking|stuck)\b|\bblocked (issues?|tasks?|work)\b", re.I)
BOARD_ABOUT = re.compile(r"\bboard\b.{0,40}\b(say|said|says|report\w*|mention\w*|state\w*)\b.{0,12}\babout\s+"
                         r"(?P<topic>[A-Za-z0-9][\w .-]{1,40}?)(\s+in\s+(?P<when>\w+))?\s*\??$", re.I)
EXEC_INTENTS: list[tuple[str, re.Pattern]] = [
    ("decisions", re.compile(r"\b(decisions?|approv\w+|sign[- ]?off)\b.{0,40}\b(need|needs|waiting|pending|my)\b|"
                             r"\bwhat (is|are) waiting (for|on) me\b", re.I)),
    ("consequence", re.compile(r"\b(what happens|what if we|consequences?)\b.{0,30}\b(ignore|delay|don't act|"
                               r"do nothing)\b", re.I)),
    ("verify", re.compile(r"\b(what should i|what to) (verify|check|confirm)\b", re.I)),
    ("good", re.compile(r"\bwhat('s| is) going (well|right)\b|\bwhat is working\b|\bgood news\b", re.I)),
    ("bad", re.compile(r"\bwhat('s| is) going (badly|wrong|poorly)\b|\bbad news\b|\bwhat is not working\b|"
                       r"\b(cause|do) the most damage\b|\bbiggest (risk|threat|problem)\b|\bgetting worse\b", re.I)),
    ("changed", re.compile(r"\bwhat('s| has)? changed( recently| lately| this (week|month))?\b|\bwhat's new\b|"
                           r"\brecent changes\b", re.I)),
    ("track", re.compile(r"\b(are we|is the company|are our projects?) on track\b|\bon track overall\b", re.I)),
    ("people", re.compile(r"\bkey[- ]person\b|\bpeople[- ]related risks?\b|\bcapacity (problems?|issues?|risks?)\b|"
                          r"\bpeople risks?\b|\bmost important people\b|\bsingle points? of failure\b", re.I)),
    ("delivery", re.compile(r"\b(deadlines?|projects?|delivery|releases?)\b.{0,20}\b(at risk|on track|slipp\w*|"
                            r"blocked|blocking|delayed)\b|\bwhat is blocking delivery\b|\bwhich teams have delivery\b|"
                            r"\bdependencies need escalation\b|\bneed(s)? escalation\b", re.I)),
    ("finance", re.compile(r"\bfinancial (position|health|situation|picture)\b|\bhow are (our )?finances\b|"
                           r"\bcommercial risks?\b", re.I)),
    ("priorities", re.compile(r"\b(focus on|prioriti[sz]e|immediate attention|needs? my attention|"
                              r"most important things?|five (most )?important|top (\d|three|five) things)\b", re.I)),
    ("summary", re.compile(r"\b(executive summary|summary of the company|summari[sz]e (the company|where we are)|"
                           r"how is the company doing|where do we stand|state of the company|company overview)\b",
                           re.I)),
]
EXECUTIVE = re.compile("|".join(f"(?:{rx.pattern})" for _, rx in EXEC_INTENTS), re.I)
DEPENDS = re.compile(r"\b(what|which|who)\b.{0,30}\b(depends? on|relies? on|rides on|hangs on|is waiting on|"
                     r"is tied to|blocks|is blocking)\s+(?P<x>[A-Za-z0-9][\w .-]{1,40})", re.I)
HIRING = re.compile(r"\b(roles?|positions?|requisitions?|vacanc\w*|openings?|hires?|hiring)\b.{0,40}"
                    r"\b(on hold|paused|frozen|open|status|filled)\b|\bwhat (are we|is) hiring\b", re.I)
OWNERS = re.compile(r"\b(who)\b.{0,40}\b(owns?|owners?|working on|assigned|responsible|holds?|has)\b.{0,40}"
                    r"\b(block\w*|issues?|tickets?|tasks?|work|most)\b|\bwhat is (?P<who>[A-Z][a-z]+(?: [A-Z][a-z]+)?)"
                    r" working on\b|\b(workload|owners?) (of|by|per) (person|people|assignee)", re.I)
COMMITMENTS = re.compile(r"\b(unresolved|open|outstanding|pending|missed|late|overdue|at[- ]risk)\b.{0,20}"
                         r"\bcommitments?\b|\bcommitments?\b.{0,30}\b(unresolved|open|outstanding|missed|at risk|"
                         r"without (a )?(new )?date)\b|\bwhich commitments\b", re.I)
TOPIC_ABOUT = re.compile(r"\b(what (do|does) (the )?records? (say|show)|what do we know|tell me|summari[sz]e)\b.{0,10}"
                         r"\babout\s+(?P<topic>[A-Za-z0-9][\w .-]{1,40}?)\s*(,|\band what\b|\?|$)", re.I)
ISSUE_KEY = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-\d{1,6})\b")
JIRA_ABOUT = re.compile(r"\b(jira|tickets?|issues?)\b.{0,30}\b(say|says|about|mention\w*|on)\s+(the\s+)?"
                        r"(?P<topic>[A-Za-z0-9][\w .-]{2,60}?)\s*\??$", re.I)
STOP = {"risk", "risks", "concern", "concerns", "worry", "exposure", "what", "our", "the", "is", "are", "about",
        "with", "this", "that", "current", "currently", "biggest", "main", "any", "there"}


def _result(basis_json: dict, status: str, interpretation: str, answer: str, claims: list[dict], evidence: list[dict],
            unknowns: list[str], follow_ups: list[str] | None = None) -> dict:
    best = min((c["grade"] for c in claims), default="D")
    return {"status": status, "interpretation": interpretation, "answer": answer,
            "grade": {"value": best, "label": {"A": "System record", "B": "Formal document or message",
                                               "C": "Informal statement", "D": "No usable evidence"}[best],
                      "reasons": ["answered by the subsystem that owns this question; claims cite records"]},
            "qualifiers": [], "claims": claims, "disagreements": [], "unknowns": list(dict.fromkeys(unknowns)),
            "refusal": None, "read_basis": basis_json, "facts": [], "evidence": evidence,
            "verification": {"deterministic": "routed answer: each claim lists the records it rests on",
                             "dropped_claims": 0, "entailment": "not run (no model)"},
            **({"follow_ups": follow_ups} if follow_ups else {})}


def _quote(excerpt: str) -> str:
    """A readable quote from a search excerpt: no document title, no leading half-sentence, whole words only."""
    text = excerpt.replace("⟦", "").replace("⟧", "").strip()
    head = text[:90]
    if ": " in head:                                   # "Title: Sub-title: text" -> "text"
        text = text[head.rindex(": ") + 2:]
    if text and not text[0].isupper() and ". " in text[:60] and len(text[:text.index(". ")].split()) <= 3:
        text = text[text.index(". ") + 2:]             # drop a short leading fragment of the previous sentence
    text = text.strip()
    if text and text[-1] not in ".!?":
        text = (text.rsplit(" ", 1)[0] + " …") if " " in text else text
    return text


def _sentence(excerpt: str, terms: list[str]) -> str | None:
    """The one sentence of a search excerpt that contains every term (so a quote never borrows a neighbouring
    sentence about something else); None for spreadsheet-row text, which is not a statement."""
    text = excerpt.replace("\u27e6", "").replace("\u27e7", "")
    for sent in re.split(r"(?<=[.!?])\s+|\n+", text):
        low = sent.lower()
        if all(t in low for t in terms) and sent.count("; ") < 2 and "synthetic;" not in low and len(sent) > 20:
            return _quote(sent)
    return None


def _human(day) -> str:
    """'2027-04-15' -> '15 April 2027' (anything else unchanged)."""
    from datetime import date as _date

    try:
        d = _date.fromisoformat(str(day)[:10])
        return f"{d.day} {d.strftime('%B %Y')}"
    except (TypeError, ValueError):
        return str(day)


class Router:
    def __init__(self, conn: psycopg.Connection, tenant_id, tz: str, principal: tools.Principal, basis,
                 basis_json: dict, source_class: dict):
        self.conn, self.tenant_id, self.tz, self.principal, self.basis = conn, tenant_id, tz, principal, basis
        self.basis_json, self.source_class = basis_json, source_class
        self._worry = None

    # ---- evidence the principal may read (protected classes never)
    def records(self, ids) -> dict[str, dict]:
        out = {}
        for e in tools._evidence(self.conn, self.basis, sorted(set(ids))):
            if e.data_class in PROTECTED:
                continue
            acc = self.principal.access
            if acc is not None and getattr(acc, "mode", "") == "rebac" and not acc.allows(e.system, e.data_class):
                continue
            out[e.record_id] = {"record_id": e.record_id, "kind": e.kind, "system": e.system,
                                "source_class": self.source_class.get(e.kind, "document"), "data_class": e.data_class,
                                "batch_seq": e.batch_seq, "source_path": e.source_path, "locator": e.locator,
                                "excerpt": e.excerpt}
        return out

    def claim(self, text: str, ids: list[str], recs: dict, kind: str = "record") -> dict | None:
        ids = [i for i in ids if i in recs]
        if not ids:
            return None
        grade = min(GRADE.get(recs[i]["source_class"], "C") for i in ids)
        return {"text": text, "kind": kind, "evidence": ids, "verification": "cites its records", "grade": grade,
                "notes": []}

    def worry(self) -> dict:
        if self._worry is None:
            from ..risk import governance
            from ..risk import worry as w
            from ..signals.registry import assessment_date

            policy = governance.current_policy(self.conn, self.tenant_id)
            with tenant_tx(self.conn, self.tenant_id, self.principal.principal_id, self.basis.batch_seq):
                items = w.build_items(self.conn, self.tenant_id, self.basis.batch_seq,
                                      assessment_date(self.basis, self.tz))
                content = None
                if policy.status == "approved":
                    content = self.conn.execute("SELECT content FROM policy.ranking_decision WHERE tenant_id = %s"
                                                " AND version = %s", (self.tenant_id, policy.version)
                                                ).fetchone()["content"]
            recs = self.records([r for i in items for r in i.evidence])
            items = [i for i in items if set(i.evidence) <= set(recs)]          # hidden whole if any record is not
            if content is None:
                self._worry = {"mode": "pending", "ranked": [], "unrankable": [
                    {"item_id": i.item_id, "title": i.title, "evidence": i.evidence,
                     "reasons": ["the owner's ranking decision (D-15) is not recorded for this workspace"],
                     "unknowns": i.unknowns} for i in items], "recs": recs}
            else:
                out = governance.weighted_ranking(policy, items, content)
                self._worry = {"mode": "weighted", **out, "recs": recs}
        return self._worry

    # ---- routes
    def route(self, q: str, original: str | None = None) -> dict | None:
        full = original or q                      # the time parser may strip words such as 'board'
        from . import continuity

        if continuity.asks(q):                      # before DELAY: "what does the resignation do to the release?"
            out = continuity.answer(self, q)
            if out is not None:
                return out
        if re.search(r"\bon-?call\b|\brota\b", q, re.I) and re.search(r"\bwho\b|\bhow often\b|\bmost\b", q, re.I):
            out = self.oncall(q)
            if out is not None:
                return out
        if EXECUTIVE.search(q) and not DEPENDS.search(full) and not re.search(r"\bboard\b", full, re.I):
            out = self.executive(q)
            if out is not None:
                return out
        if DEPENDS.search(q):
            out = self.depends(q)
            if out is not None:
                return out
        if HIRING.search(q):
            out = self.hiring(q)
            if out is not None:
                return out
        if OWNERS.search(q) and not re.search(r"\bon-?call\b|\brota\b|\bpager\b", q, re.I):
            out = self.owners(q)
            if out is not None:
                return out
        if ISSUE_KEY.search(q) or JIRA_ABOUT.search(q):
            out = self.issues(q)
            if out is not None:
                return out
        if COMMITMENTS.search(q):
            out = self.commitments(q)
            if out is not None:
                return out
        m = TOPIC_ABOUT.search(q)
        if m:                                       # "what do the records say about Atlas?": the structured status
            out = self.status(f"status of {m.group('topic')}")
            if out is not None:
                out["interpretation"] = f"I read this as: what the records show about {m.group('topic')}."
                return out
        for test, fn in ((PERSONAL, self.personal), (THIS_CONCERN, self.this_concern), (UNRANKABLE, self.unrankable),
                         (MISSING, self.missing), (DELAY, self.delay), (BOARD_CHANGE, self.board_change),
                         (WORRY_LIST, self.worry_list), (ROLE_HOLDER, self.role_holder), (BLOCKED, self.blocked),
                         (BOARD_ABOUT, self.board_about),
                         (STATUS, self.status)):
            if test.search(q):
                out = fn(q)
                if out is not None:
                    return out
        if TOPIC_RISK.search(q):
            return self.topic_risk(q)
        return None

    def status(self, q):
        """Project / release status from the tracker's structured records (named project only; else None)."""
        from collections import Counter

        from .continuity import OPEN, _claims

        k = self.basis.batch_seq
        with tenant_tx(self.conn, self.tenant_id, self.principal.principal_id, k):
            versions = _claims(self.conn, self.tenant_id, k, "version")
            issues = _claims(self.conn, self.tenant_id, k, "issue")
        low = q.lower()
        named = [(s, d) for s, d in versions.items() if s.split(":", 1)[1].split()[0].lower() in low]
        if not named:
            return None
        recs = self.records({r for _, d in named for r in d["_records"]} |
                            {r for d in issues.values() for r in d["_records"]})
        claims = []
        for s, d in named:
            name = s.split(":", 1)[1]
            moved = d.get("original_release_date") and d.get("release_date") != d.get("original_release_date")
            rel_status = str(d.get("release_status") or "")
            verb = "was due" if "released" in rel_status.lower() else "is due"
            c = self.claim(f"{name} {verb} {_human(d.get('release_date'))}" + (
                f"; it was originally due {_human(d.get('original_release_date'))}" if moved else "")
                + (f" (the date was changed on {_human(d.get('last_changed'))})" if moved and d.get("last_changed")
                   else "")
                + (f". Release status: {rel_status}" if rel_status else "") + ".", sorted(d["_records"]), recs)
            if c:
                claims.append(c)
            proj = d.get("project")
            mine = {i: x for i, x in issues.items() if x.get("fix_version") == name
                    or (proj and not x.get("fix_version") and x.get("project") == proj)}
            if mine:
                st = Counter(str(x.get("status_at_export")) for x in mine.values())
                pts = lambda xs: sum(float(x.get("story_points") or 0) for x in xs)  # noqa: E731
                op = [x for x in mine.values() if OPEN(x.get("status_at_export"))]
                c = self.claim(f"At the tracker export, {len(mine)} issues target {name}: "
                               + ", ".join(f"{n} {k_}" for k_, n in st.most_common())
                               + f". {pts(op):g} of {pts(mine.values()):g} story points are still open.",
                               sorted({r for x in mine.values() for r in x["_records"]}), recs)
                if c:
                    claims.append(c)
        if not claims:
            return None
        unknowns = ["The tracker export is a snapshot; work done since the export is not recorded.",
                    "Whether the remaining scope fits the current date is not established (no supported throughput)."]
        return _result(self.basis_json, "answered", "I read this as: the current status of a named release, from the "
                       "tracker records.", " ".join(c["text"] for c in claims), claims,
                       [recs[i] for c in claims for i in c["evidence"]], unknowns,
                       ["Why did the release date move?", "What does the reported resignation mean for the release?"])

    def _body(self, record_id: str) -> str:
        """The full admitted text of a document record (for whole-sentence quotes)."""
        with tenant_tx(self.conn, self.tenant_id, self.principal.principal_id, self.basis.batch_seq):
            r = self.conn.execute("SELECT body FROM projection.document_text WHERE source_record_id = %s"
                                  " AND batch_seq <= %s ORDER BY batch_seq DESC LIMIT 1",
                                  (record_id, self.basis.batch_seq)).fetchone()
        return (r or {}).get("body") or ""

    def _whole(self, record_id: str, excerpt: str, terms: list[str]) -> str | None:
        """A whole sentence containing the terms: from the excerpt, or from the full text when the excerpt clips it."""
        s = _sentence(excerpt, terms)
        if s is None or s.endswith("\u2026"):
            full = _sentence(self._body(record_id), terms)
            s = full or s
        return s

    def board_about(self, q):
        """What the board updates say about a topic: whole sentences, each citing its update (as reported)."""
        m = BOARD_ABOUT.search(q)
        topic, when = m.group("topic").strip().rstrip("?. "), (m.group("when") or "").lower()
        quarter = re.search(r"\bq([1-4])\b", q, re.I)
        with tenant_tx(self.conn, self.tenant_id, self.principal.principal_id, self.basis.batch_seq):
            docs = self.conn.execute(
                "SELECT DISTINCT ON (source_record_id) source_record_id, title, body FROM projection.document_text"
                " WHERE batch_seq <= %s AND system IN ('board_reporting', 'documents') AND body ILIKE %s"
                " AND body ILIKE '%%board%%' ORDER BY source_record_id, batch_seq DESC",
                (self.basis.batch_seq, f"%{topic.split()[0]}%")).fetchall()
        months = {"q1": ("jan", "apr"), "q2": ("apr to jun", "jul"), "q3": ("jul to sep", "oct"),
                  "q4": ("oct to dec", "jan")}
        if quarter:
            key = months[f"q{quarter.group(1)}"][0]
            docs = [d for d in docs if key in d["body"].lower()] or docs
        elif when:
            docs = [d for d in docs if re.search(rf"\bsent \d+ {when[:3]}", d["body"].lower())] or docs
        recs = self.records([d["source_record_id"] for d in docs])
        claims = []
        for d in docs:
            if d["source_record_id"] not in recs:
                continue
            sents = [x for x in re.split(r"(?<=[.!?])\s+|\n+", d["body"]) if topic.split()[0].lower() in x.lower()
                     and x.count("; ") < 2 and len(x) > 15]
            sent = re.search(r"\bSent ([^.]+)\.", d["body"])
            for x in sents[:2]:
                c = self.claim(f"Board update{f' sent {sent.group(1)}' if sent else ''} (as reported): "
                               f"\u201c{x.strip()}\u201d", [d["source_record_id"]], recs, "document")
                if c:
                    claims.append(c)
        if not claims:
            return None
        return _result(self.basis_json, "qualified", f"I read this as: what the board updates say about {topic}.",
                       " ".join(c["text"] for c in claims), claims, [recs[i] for c in claims for i in c["evidence"]],
                       ["Board updates are reported statements, not independently verified."])

    def hiring(self, q):
        """Open roles by status from the hiring records (role, team and status only; never candidate details)."""
        from .continuity import _claims

        k = self.basis.batch_seq
        with tenant_tx(self.conn, self.tenant_id, self.principal.principal_id, k):
            reqs = _claims(self.conn, self.tenant_id, k, "requisition")
        if not reqs:
            return None
        want = re.search(r"\b(on hold|paused|frozen)\b", q, re.I)
        rows = [(s.split(":", 1)[1], d) for s, d in sorted(reqs.items())
                if not want or str(d.get("status", "")).lower() in ("on hold", "paused", "frozen")]
        if not rows:
            return _result(self.basis_json, "answered", "I read this as: which roles are on hold.",
                           "No role is recorded as on hold.", [], [], [])
        recs = self.records({r for _, d in rows for r in d["_records"]})
        claims = [c for c in (self.claim(f"{d.get('role') or rid} ({d.get('team') or 'team not stated'}): "
                                         f"{d.get('status') or 'status not stated'}.", sorted(d["_records"]), recs)
                              for rid, d in rows) if c]
        if not claims:
            return None
        lead = "Roles on hold:" if want else "Roles by hiring status:"
        return _result(self.basis_json, "answered", "I read this as: " + lead.lower().rstrip(":") + ".",
                       lead + "\n" + "\n".join(f"- {c['text']}" for c in claims), claims,
                       [recs[i] for c in claims for i in c["evidence"]],
                       ["Why a role is paused, and what it delays, is not recorded."])

    def oncall(self, q):
        """Who held primary on-call, counted over the recorded rota weeks (rota presence, not qualification)."""
        from collections import Counter

        from .continuity import _claims

        k = self.basis.batch_seq
        with tenant_tx(self.conn, self.tenant_id, self.principal.principal_id, k):
            weeks = _claims(self.conn, self.tenant_id, k, "rota_week")
            names = {r["emp_id"]: r["full_name"] for r in self.conn.execute(
                "SELECT emp_id, full_name FROM projection.person WHERE tenant_id = %s AND batch_seq ="
                " (SELECT max(batch_seq) FROM projection.person WHERE tenant_id = %s AND batch_seq <= %s)",
                (self.tenant_id, self.tenant_id, k)).fetchall()}
        held = Counter(d.get("primary_source_id") for d in weeks.values() if d.get("primary_source_id"))
        if not held:
            return None
        recs_by = {p: sorted({r for d in weeks.values() if d.get("primary_source_id") == p for r in d["_records"]})
                   for p in held}
        recs = self.records({r for v in recs_by.values() for r in v})
        total = sum(held.values())
        claims = [c for c in (self.claim(f"{names.get(p, p)}: primary on-call in {n} of {total} recorded weeks.",
                                         recs_by[p], recs) for p, n in held.most_common(6)) if c]
        if not claims:
            return None
        return _result(self.basis_json, "answered", "I read this as: who holds primary on-call, and how often.",
                       "Primary on-call by person, most weeks first:\n" + "\n".join(f"- {c['text']}" for c in claims),
                       claims, [recs[i] for c in claims for i in c["evidence"]],
                       ["Being on the rota does not show who is qualified to cover; that is not recorded."])

    def owners(self, q):
        """Who holds open and blocked tracker work. A person is named only through an approved Jira identity
        mapping (verified e-mail or an administrator's mapping); everything else is counted as unresolved."""
        from ..resolution import jira_identity
        from ..risk.business_map import owner_of
        from .continuity import _claims

        k = self.basis.batch_seq
        with tenant_tx(self.conn, self.tenant_id, self.principal.principal_id, k):
            issues = _claims(self.conn, self.tenant_id, k, "issue")
            owners = jira_identity.resolved(self.conn, self.tenant_id)
            names = {r["emp_id"]: (r["full_name"], r["title"]) for r in self.conn.execute(
                "SELECT emp_id, full_name, title FROM projection.person WHERE tenant_id = %s AND batch_seq ="
                " (SELECT max(batch_seq) FROM projection.person WHERE tenant_id = %s AND batch_seq <= %s)",
                (self.tenant_id, self.tenant_id, k)).fetchall()}
        done = re.compile(r"done|closed|resolved|cancel", re.I)
        blockers = {b.strip() for d in issues.values() for b in str(d.get("blocked_by") or "").replace(";", ",")
                    .split(",") if b.strip()}
        per: dict[str, dict] = {}
        unresolved = 0
        for subj, d in issues.items():
            if done.search(str(d.get("status_at_export") or "")):
                continue
            key = subj.split(":", 1)[1]
            emp = owner_of(owners, d)
            if not emp:
                unresolved += 1
                continue
            x = per.setdefault(emp, {"open": 0, "blocked": 0, "blocking": 0, "keys": [], "recs": set()})
            x["open"] += 1
            x["blocked"] += bool(d.get("blocked_by")) or "block" in str(d.get("status_at_export") or "").lower()
            x["blocking"] += key in blockers
            x["keys"].append(key)
            x["recs"] |= set(d["_records"])
        m = OWNERS.search(q)
        wanted = m.group("who") if m and m.group("who") else None
        if wanted:
            per = {e: v for e, v in per.items() if names.get(e, ("",))[0].lower().startswith(wanted.lower())}
        if not per:
            if not unresolved:
                return None
            return _result(self.basis_json, "abstained", "I read this as: who holds open tracker work.",
                           f"I can't name owners yet: {unresolved} open issues have Jira assignees that are not yet "
                           "linked to an employee. Assignees are linked only when their Jira account is verified to "
                           "be that employee's, so these stay unnamed rather than guessed.",
                           [], [], ["Jira assignees are linked to employees only through a verified e-mail match or "
                                    "an administrator's mapping, never by name."])
        ranked = sorted(per.items(), key=lambda kv: (-kv[1]["blocking"], -kv[1]["blocked"], -kv[1]["open"]))[:8]
        recs = self.records({r for _, v in ranked for r in v["recs"]})
        claims = []
        for emp, v in ranked:
            name, title = names.get(emp, (emp, None))
            text = (f"{name}{f' ({title})' if title else ''}: {v['open']} open issue{'s' if v['open'] != 1 else ''}"
                    f", {v['blocked']} blocked, {v['blocking']} blocking other work"
                    f" ({', '.join(sorted(v['keys'])[:5])}{'…' if len(v['keys']) > 5 else ''}).")
            c = self.claim(text, sorted(v["recs"]), recs)
            if c:
                claims.append(c)
        if not claims:
            return None
        lead = "Open tracker work by owner, those blocking the most other work first:"
        return _result(self.basis_json, "answered", "I read this as: who owns open and blocked tracker work.",
                       lead + "\n" + "\n".join(f"- {c['text']}" for c in claims), claims,
                       [recs[i] for c in claims for i in c["evidence"]],
                       ([f"{unresolved} open issues have assignees not yet linked to an employee, so they are not "
                         "counted above."] if unresolved else [])
                       + ["Owners come from approved Jira identity links; tracker records are as of their last sync."])

    def issues(self, q):
        """Named issues ("status of VIT-2") or issues about a topic, from the issue claims (exports and Jira sync)."""
        from .continuity import _claims

        k = self.basis.batch_seq
        with tenant_tx(self.conn, self.tenant_id, self.principal.principal_id, k):
            issues = _claims(self.conn, self.tenant_id, k, "issue")
        keys = {m.upper() for m in ISSUE_KEY.findall(q)}
        if keys:
            hits = {s: d for s, d in issues.items() if s.split(":", 1)[1] in keys}
        else:
            m = JIRA_ABOUT.search(q)
            words = [w for w in re.findall(r"[a-z0-9]+", m.group("topic").lower()) if len(w) > 2 and w not in STOP]
            hits = {s: d for s, d in issues.items()
                    if len([w for w in words if w in str(d.get("summary", "")).lower()]) >= min(2, len(words))}
        if not hits:
            return None
        recs = self.records({r for d in hits.values() for r in d["_records"]})
        claims = []
        for s, d in sorted(hits.items())[:8]:
            bits = [f"{s.split(':', 1)[1]}: {d.get('summary') or 'no summary recorded'}",
                    f"status {d.get('status_at_export') or 'not recorded'}"]
            if d.get("assignee_name") or d.get("assignee_source_id"):
                bits.append(f"assignee {d.get('assignee_name') or d.get('assignee_source_id')}")
            bits.append(f"due {_human(d['due_date'])}" if d.get("due_date") else "no due date recorded")
            c = self.claim("; ".join(bits) + ".", sorted(d["_records"]), recs)
            if c:
                claims.append(c)
        if not claims:
            return None
        return _result(self.basis_json, "answered", "I read this as: what the tracker records say about these "
                       "issues.", " ".join(c["text"] for c in claims), claims,
                       [recs[i] for c in claims for i in c["evidence"]],
                       ["Tracker records are as of their last sync or export; later changes in Jira are not shown "
                        "until the next sync is reviewed and admitted."])

    # ------------------------------------------------------------------ CEO briefing (broad executive questions)
    def _metrics(self) -> list[dict]:
        """Board-reported figures, latest period first: [{name, value, period, prev, prev_period, records}]."""
        k = self.basis.batch_seq
        with tenant_tx(self.conn, self.tenant_id, self.principal.principal_id, k):
            rows = self.conn.execute(
                "SELECT subject, object, record_id FROM knowledge.claim WHERE tenant_id = %s AND batch_seq <= %s"
                " AND subject_type = 'metric' AND predicate = 'reported_value'", (self.tenant_id, k)).fetchall()
        by: dict[str, list] = {}
        for r in rows:
            o = r["object"] if isinstance(r["object"], dict) else {}
            by.setdefault(r["subject"].split(":", 1)[1], []).append((str(o.get("issued_on") or ""), o, r["record_id"]))
        out = []
        for name, vals in by.items():
            vals.sort(key=lambda v: v[0], reverse=True)
            cur, prev = vals[0], (vals[1] if len(vals) > 1 and vals[1][1].get("period") != vals[0][1].get("period")
                                  else None)
            label = name.replace(" (recurring)", "").upper() if name.startswith("arr") else name.capitalize()
            out.append({"name": label,
                        "value": cur[1].get("value_text"), "period": cur[1].get("period"),
                        "prev": prev[1].get("value_text") if prev else None,
                        "prev_period": prev[1].get("period") if prev else None,
                        "records": [cur[2]] + ([prev[2]] if prev else [])})
        order = {"ARR": 0, "Customers": 1, "Monthly burn": 2, "Runway": 3, "Team": 4}
        return sorted(out, key=lambda m: order.get(m["name"], 9))

    def _pending_decisions(self) -> tuple[int, int, list[str]]:
        """(submissions awaiting a decision, findings still open, their ids) for this principal's tenant."""
        with tenant_tx(self.conn, self.tenant_id, self.principal.principal_id):
            subs = self.conn.execute("SELECT count(*) AS n FROM intake.submission_state WHERE tenant_id = %s AND"
                                     " state IN ('awaiting_review', 'awaiting_authorisation')",
                                     (self.tenant_id,)).fetchone()["n"]
            fnd = self.conn.execute("SELECT finding_id FROM runtime.finding_current WHERE tenant_id = %s AND state"
                                    " IN ('detected', 'verified', 'notified', 'reopened')",
                                    (self.tenant_id,)).fetchall()
        return subs, len(fnd), [f["finding_id"] for f in fnd]

    def executive(self, q):
        """A CEO briefing assembled ONLY from cited facts the system already holds: the latest board figures (with the
        previous quarter), the ranked concerns (close calls stated), concerns that need more evidence, commitment
        warnings, people risks and what is waiting for a decision. Which sections appear depends on the question;
        nothing is inferred beyond the records, and what the records cannot say is listed as unknown."""
        from ..risk import graph

        intent = next((name for name, rx in EXEC_INTENTS if rx.search(q)), "summary")
        w = self.worry()
        recs = dict(w["recs"])
        ranked, unrank = w.get("ranked", []), w.get("unrankable", [])
        mets = self._metrics()
        recs |= self.records({r for m in mets for r in m["records"]})
        warn = [x for x in graph.build(self.conn, self.basis, self.tz).get("warnings", []) if x.get("commitment")]
        recs |= self.records({r for x in warn for r in x.get("evidence", [])})
        claims, lines, unknowns = [], [], []

        def add(text, ids, kind="record"):
            c = self.claim(text, list(ids), recs, kind)
            if c:
                claims.append(c)
                lines.append(f"- {text}")

        better = {"ARR": 1, "Customers": 1, "Runway": 1, "Monthly burn": -1}   # which way is an improvement

        def direction(m) -> int:
            """+1 improved, -1 worsened, 0 unknown or unchanged (from the reported values, as numbers)."""
            num = lambda v: float((re.search(r"\d+(?:\.\d+)?", str(v)) or [None])[0] or "nan")  # noqa: E731
            try:
                d = num(m["value"]) - num(m["prev"])
            except (TypeError, ValueError):
                return 0
            return 0 if d != d or d == 0 else (1 if d > 0 else -1) * better.get(m["name"], 0)

        def figures(changes_only=False, want=None):
            for m in mets:
                if not m["value"]:
                    continue
                if want is not None and (not m["prev"] or direction(m) != want):
                    continue
                if m["prev"] and m["prev"] != m["value"]:
                    add(f"{m['name']}: {m['value']} for {m['period']} (was {m['prev']} for {m['prev_period']}),"
                        " as reported to the board.", m["records"])
                elif not changes_only:
                    add(f"{m['name']}: {m['value']} for {m['period']}, as reported to the board.", m["records"])

        def concerns(n=3):
            close = any(x.get("tied") for x in ranked[:n])
            for i, x in enumerate(ranked[:n], 1):
                add(f"Priority #{i}{' (close call)' if x.get('tied') else ''}: {x['title']}.", x["evidence"], "derived")
            if close:
                unknowns.append("The top concerns are close calls: their possible impact overlaps, so the records "
                                "can't say which comes first. Look at them together.")

        def thin():
            for x in unrank:
                add(f"Needs more evidence: {x['title']}. {(x.get('unknowns') or [''])[0]}".strip(), x["evidence"],
                    "derived")

        def warnings():
            for x in warn[:4]:
                add(f"{x['commitment']}: {x['statement']}", x.get("evidence", []))

        def people():
            for x in ranked + unrank:
                if any(k in x["item_id"] for k in ("oncall", "notice", "hiring")):
                    add(f"{x['title']}.", x["evidence"], "derived")

        def decisions():
            subs, fnd, _ = self._pending_decisions()
            lines.append(f"- {subs} Data Room submission{'s' if subs != 1 else ''} waiting for review or "
                         f"authorisation, and {fnd} finding{'s' if fnd != 1 else ''} not yet investigated or resolved"
                         " (see What to worry about).")
            unknowns.append("Veloxa only knows about decisions recorded in Veloxa; approvals handled elsewhere "
                            "(email, meetings) are not visible unless those records are added.")

        heads = {"summary": "Where the company stands, from the verified records:",
                 "priorities": "What needs you first, from the verified records:",
                 "good": "What the records show improving (as reported):",
                 "bad": "What the records show going wrong or at risk:",
                 "changed": "What changed in the latest records:",
                 "track": "Whether we're on track, from the verified records:",
                 "people": "People-related risks in the records:",
                 "delivery": "Delivery risks in the records:",
                 "finance": "The financial position as reported (not independently verified):",
                 "decisions": "What is waiting for a decision in Veloxa:",
                 "consequence": "What the records say about the biggest concern (they don't project outcomes):",
                 "verify": "What to verify before deciding:"}
        if intent == "summary":
            for f in (figures, concerns, warnings, thin):
                f()
        elif intent == "priorities":
            for f in (concerns, warnings, decisions):
                f()
        elif intent == "good":
            figures(want=1)
            if any(x["item_id"].startswith("figure:") for x in ranked + unrank):
                unknowns.append("The sources disagree on ARR, so treat the ARR movement with care.")
            unknowns.append("Only movements in board-reported figures are listed; the records don't rate "
                            "performance against a plan.")
        elif intent == "bad":
            for f in (concerns, warnings):
                f()
            figures(want=-1)
        elif intent == "changed":
            figures(changes_only=True)
            warnings()
        elif intent == "track":
            warnings()
            concerns()
            unknowns.append("The records hold no company-wide plan to measure 'on track' against, so this lists "
                            "what has moved or is at risk rather than giving a yes/no.")
        elif intent == "people":
            people()
        elif intent == "delivery":
            warnings()
            for x in ranked:
                if x["item_id"].startswith("slip:"):
                    add(f"{x['title']}.", x["evidence"], "derived")
        elif intent == "finance":
            figures()
            if any(x["item_id"].startswith("figure:") for x in ranked + unrank):
                unknowns.append("The sources disagree on ARR, and no approved rule says which figure is right.")
            unknowns.append("No ledger-based revenue or cash figure is admitted; these are the board's own numbers.")
        elif intent == "decisions":
            decisions()
        elif intent == "consequence":
            if ranked:
                x = ranked[0]
                add(f"{x['title']}.", x["evidence"], "derived")
                unknowns += list(x.get("unknowns") or [])[:3]
            unknowns.append("The records don't project what happens if this is ignored. Use What-if to test a "
                            "specific change, such as moving a date.")
        elif intent == "verify":
            for x in ranked:
                for u in (x.get("unknowns") or [])[:1]:
                    add(f"{x['title']}: {u}", x["evidence"], "derived")
            thin()
        if not lines:
            return None
        return _result(self.basis_json, "qualified", f"I read this as: a CEO briefing ({intent}).",
                       heads[intent] + "\n" + "\n".join(lines), claims,
                       [recs[i] for c in claims for i in c["evidence"]], list(dict.fromkeys(unknowns)),
                       ["What should I worry about?", "What is missing from the records?", "Why did Atlas GA move?"])

    def depends(self, q):
        """What hangs on a release or epic: the tracker work aimed at it (by its fix version or epic) and the
        recorded concerns about it. Only links the records state; nothing inferred."""
        from .continuity import _claims

        name = DEPENDS.search(q).group("x").strip(" ?.\"'")
        x = name.lower()
        if len(x) < 3:
            return None
        k = self.basis.batch_seq
        with tenant_tx(self.conn, self.tenant_id, self.principal.principal_id, k):
            issues = _claims(self.conn, self.tenant_id, k, "issue")
        done = re.compile(r"done|closed|resolved|cancel", re.I)
        hit = [(s.split(":", 1)[1], d) for s, d in sorted(issues.items())
               if x in str(d.get("fix_version", "")).lower() or x in str(d.get("epic", "")).lower()]
        if not hit:
            return None
        open_ = [(key, d) for key, d in hit if not done.search(str(d.get("status_at_export") or ""))]
        recs = self.records({r for _, d in open_[:10] for r in d["_records"]})
        claims = [c for key, d in open_[:10] if (c := self.claim(
            f"{key} {d.get('summary') or ''} ({d.get('status_at_export') or 'status not stated'}).",
            sorted(d["_records"]), recs))]
        if not claims:
            return None
        lead = (f"{len(hit)} tracker issue{'s' if len(hit) != 1 else ''} are aimed at {name}; "
                f"{len(open_)} still open. The open ones:")
        return _result(self.basis_json, "answered", f"I read this as: what work depends on {name}.",
                       lead + "\n" + "\n".join(f"- {c['text']}" for c in claims), claims,
                       [recs[i] for c in claims for i in c["evidence"]],
                       ["Only links the tracker records state (fix version or epic) are counted; other dependencies "
                        "may exist but are not recorded."], [f"Why did {name} move?"])

    def commitments(self, q):
        """Recorded commitments and what is unresolved about each (moved without a new date, no date, one source)."""
        from ..risk import graph

        g = graph.build(self.conn, self.basis, self.tz)
        ws = [w for w in g.get("warnings", []) if w.get("commitment")]
        if not ws:
            return None
        plain = {"moved_without_replacement_date": "was moved and no replacement date is recorded",
                 "no_stated_date": "has no stated date", "single_source": "rests on a single record",
                 "shared_evidence": "rests on evidence shared with other commitments",
                 "conflicted_evidence": "rests on evidence that other records contradict"}
        recs = self.records({r for w in ws for r in w.get("evidence", [])})
        claims = [c for w in ws if (c := self.claim(f"{w['commitment']} {plain.get(w['kind'], w['kind'])}: "
                                                    f"{w['statement']}", w.get("evidence", []), recs))]
        if not claims:
            return None
        names = sorted({w["commitment"] for w in ws})
        return _result(self.basis_json, "qualified", "I read this as: which recorded commitments have something "
                       "unresolved.", f"{len(names)} recorded commitment{'s' if len(names) != 1 else ''} with "
                       f"something unresolved: " + "; ".join(c["text"] for c in claims), claims,
                       [recs[i] for c in claims for i in c["evidence"]],
                       ["Only commitments stated in admitted records are listed; others may exist."],
                       [f"What if {names[0]} moves by 3 weeks?", f"Why did {names[0]} move?"])

    def role_holder(self, q):
        """Who holds a named role, from the HR export's titles (work facts only; nothing about the person)."""
        from .continuity import _claims

        role = ROLE_HOLDER.search(q).group("role").lower()
        want = {"ceo": "chief executive officer", "chief executive": "chief executive officer",
                "cfo": "chief financial officer", "cto": "chief technology officer"}.get(role, role)
        k = self.basis.batch_seq
        with tenant_tx(self.conn, self.tenant_id, self.principal.principal_id, k):
            people = _claims(self.conn, self.tenant_id, k, "person")
        hits = [d for d in people.values() if str(d.get("title", "")).lower() == want
                or (want == "chief executive officer" and str(d.get("title", "")).lower() == "ceo")]
        recs = self.records({r for d in hits for r in d["_records"]})
        claims = [c for d in hits if (c := self.claim(
            f"{d.get('name')} holds the title {d.get('title')} in the HR export"
            + (f" ({d.get('department')})" if d.get("department") else "")
            + (f"; recorded status: {d.get('employment_status')}" if d.get("employment_status") not in (None, "Active")
               else "") + ".", sorted(d["_records"]), recs))]
        if not claims:
            return None
        return _result(self.basis_json, "answered", "I read this as: who holds a role, from the HR export.",
                       " ".join(c["text"] for c in claims), claims, [recs[i] for c in claims for i in c["evidence"]],
                       ["Titles are as at the HR export date; later changes are not recorded."])

    def blocked(self, q):
        """Issues the tracker marks as blocked, with what the record says blocks them (no judgement)."""
        from collections import Counter

        from .continuity import _claims

        k = self.basis.batch_seq
        with tenant_tx(self.conn, self.tenant_id, self.principal.principal_id, k):
            issues = _claims(self.conn, self.tenant_id, k, "issue")
        low = q.lower()
        blocked = {s: d for s, d in issues.items() if str(d.get("status_at_export", "")).lower() == "blocked"}
        named = {s: d for s, d in blocked.items() if d.get("project") and str(d["project"]).lower() in low}
        blocked = named or blocked
        if not blocked:
            return None
        recs = self.records({r for d in blocked.values() for r in d["_records"]})
        why = Counter(str(d.get("blocked_by") or "not stated") for d in blocked.values())
        by_proj = Counter(str(d.get("project") or "unassigned") for d in blocked.values())
        ids = sorted({r for d in blocked.values() for r in d["_records"]})
        c1 = self.claim(f"The tracker export marks {len(blocked)} issue{'s' if len(blocked) != 1 else ''} as blocked ("
                        + ", ".join(f"{n} in {p}" for p, n in by_proj.most_common()) + ").", ids, recs)
        c2 = self.claim("What the records say blocks them: " + "; ".join(f"{w} ({n})" for w, n in why.most_common(4))
                        + ".", ids, recs)
        claims = [c for c in (c1, c2) if c]
        if not claims:
            return None
        return _result(self.basis_json, "answered", "I read this as: which tracker issues are blocked, and by what.",
                       " ".join(c["text"] for c in claims), claims, [recs[i] for c in claims for i in c["evidence"]],
                       ["The export is a snapshot; work unblocked since then is not recorded."],
                       ["What does this mean for the release date?"])

    def personal(self, q):
        return _result(self.basis_json, "abstained", "I read this as a question about an employee's personal life.",
                       NO_RECORD + " Personal details are not part of the company records. The AI assistants "
                       "have invented persona details for the simulation; they are labelled as fictional and are "
                       "never facts about a real employee.", [], [],
                       ["personal details are outside the company records"])

    def this_concern(self, q):
        w = self.worry()
        titles = [x["title"] for x in w.get("ranked", [])] + [x["title"] for x in w.get("unrankable", [])]
        return _result(self.basis_json, "abstained", "I read this as a question about a specific concern, but none "
                       "was selected.", "Which concern do you mean? Choose one below, or open it from What to worry "
                       "about and select Explain.", [], [], ["no concern was selected"],
                       [f"Why is “{t}” a concern?" for t in titles[:3]])

    def _item_claims(self, x, recs, ranked: bool):
        from .. import explain

        e = explain.worry(x, ranked=ranked, total_ranked=len(self.worry().get("ranked", [])))
        out = []
        for s in e["sections"]:
            for it in s["items"]:
                c = self.claim(it["text"], it["evidence"] or x["evidence"], recs,
                               "derived" if s["basis"] != "recorded" else "record")
                if c:
                    out.append(c)
        return out, e

    def unrankable(self, q):
        w = self.worry()
        recs = w["recs"]
        items = w.get("unrankable", [])
        if not items:
            return _result(self.basis_json, "answered", "I read this as: which concerns cannot be ranked.",
                           "Every concern the records support can currently be ranked.", [], [], [])
        claims = [c for x in items if (c := self.claim(f"{x['title']}: not ranked, because "
                                                         + "; ".join(x["reasons"]).rstrip(".") + ".", x["evidence"],
                                                         recs, "derived"))]
        lead = (f"{len(claims)} concern{'s' if len(claims) != 1 else ''} cannot be ranked yet. They are recorded "
                "and shown, but there isn't enough measured evidence to place them in the ranking.")
        return _result(self.basis_json, "qualified", "I read this as: which concerns cannot be ranked (the worry "
                       "model's unrankable list).", lead, claims, [recs[i] for c in claims for i in c["evidence"]],
                       ["an unranked concern is not a lower priority; it is a concern whose exposure isn't measured"])

    def worry_list(self, q):
        w = self.worry()
        recs = w["recs"]
        if w["mode"] == "pending":
            return self.unrankable(q)
        from ..explain import group_label

        claims = [c for x in w["ranked"][:5] if (c := self.claim(
            f"{group_label(x['rank'])}: {x['title']}.", x["evidence"], recs, "derived"))]
        n_un = len(w.get("unrankable", []))
        return _result(self.basis_json, "qualified", "I read this as: the ranked list of concerns (approved D-15 "
                       "method).", f"{len(claims)} ranked concern{'s' if len(claims) != 1 else ''}"
                       + (f", plus {n_un} that cannot be ranked yet" if n_un else "") + ".",
                       claims, [recs[i] for c in claims for i in c["evidence"]],
                       ["concerns in the same attention group cannot be ordered individually by the evidence"]
                       if any(x["tied"] for x in w["ranked"]) else [])

    def topic_risk(self, q):
        words = {t for t in re.findall(r"[a-z][a-z-]{2,}", q.lower()) if t not in STOP}
        norm = lambda s: s.lower().replace("-", "").replace(" ", "")          # noqa: E731
        w = self.worry()
        pool = [(x, True) for x in w.get("ranked", [])] + [(x, False) for x in w.get("unrankable", [])]
        hits = [(x, r) for x, r in pool if any(norm(t) in norm(x["title"]) or norm(t) in norm(x["item_id"])
                                               for t in words)]
        if not hits:
            return None
        x, ranked = hits[0]
        claims, e = self._item_claims(x, w["recs"], ranked)
        return _result(self.basis_json, "qualified", f"I read this as: the concern “{x['title']}”.",
                       e["summary"], claims, [w["recs"][i] for c in claims for i in c["evidence"]],
                       [it["text"] for s in e["sections"] if s["basis"] == "unknown" for it in s["items"]])

    def missing(self, q):
        w = self.worry()
        recs = w["recs"]
        claims = []
        for x in w.get("ranked", []):
            for m in x["severity"]["criteria"].values():
                if m["status"] in ("missing", "unquantified", "unmapped"):
                    cl = self.claim(f"{x['title']}: {m['reason'].rstrip('.')}.", x["evidence"], recs, "derived")
                    if cl:
                        claims.append(cl)
        for x in w.get("unrankable", []):
            cl = self.claim(f"{x['title']}: " + "; ".join(x["reasons"]).rstrip(".") + ".", x["evidence"], recs,
                            "derived")
            if cl:
                claims.append(cl)
        if not claims:
            return None
        return _result(self.basis_json, "qualified", "I read this as: what the records do not establish for the "
                       "current concerns.", "Here is what the records don't establish for the current concerns.",
                       claims, [recs[i] for c in claims for i in c["evidence"]],
                       ["missing information is shown as missing; nothing is estimated in its place"])

    def _search(self, query: str, must: list[str] | None = None, limit: int = 3) -> list[tools.Evidence]:
        r = tools.text_search(self.conn, self.basis, self.principal, query=query, limit=limit)
        out = []
        for e in r.evidence:
            text = e.excerpt.lower()
            if e.data_class in PROTECTED or (must and not all(m in text for m in must)):
                continue
            out.append(e)
        return out

    def delay(self, q):
        w = self.worry()
        recs = dict(w["recs"])
        slips = [x for x in w.get("ranked", []) + w.get("unrankable", []) if str(x["item_id"]).startswith("slip:")]
        if not slips:
            return None
        low = q.lower()          # the release the question names, else the first recorded move
        x = next((s for s in slips if str(s.get("project") or s["title"].removeprefix("Release ").split(" moved")[0])
                  .lower() in low), slips[0])
        claims = []
        reason = (x.get("severity") or {}).get("criteria", {}).get("C1", {}).get("reason")
        c = self.claim(f"Recorded schedule change: {x['title']}" + (f" ({reason})" if reason else "") + ".",
                       x["evidence"], recs)
        if c:
            claims.append(c)
        used: set[tuple[str, str]] = set()
        for label, query, must in (("Stated dependency", "two backend hires", ["backend"]),
                                   ("Stated dependency", "backend engineers", ["backend"]),
                                   ("Hiring pause", "pausing", ["paus"]), ("Hiring pause", "hiring paused", ["paus"]),
                                   ("Reliability work", "unplanned reliability work", ["reliab"]),
                                   ("Reliability work", "reliability", ["reliab"])):
            if any(lab == label for lab, _ in used):          # one line per factor: no near-duplicates
                continue
            for e in self._search(query, must):
                if (label, e.record_id) in used:
                    continue
                quote = _sentence(e.excerpt, must or [])
                if quote is None:                                  # a table row or a neighbouring sentence: skip
                    continue
                used.add((label, e.record_id))
                recs.update(self.records([e.record_id]))
                cl = self.claim(f"{label}: \u201c{quote}\u201d", [e.record_id], recs, "document")
                if cl:
                    claims.append(cl)
                break
        project = str(x.get("project") or x["title"].split(" moved")[0].removeprefix("Release ")).strip()
        for word in ("because", "depend", "slipp", "paus"):              # statements about THIS release, any company
            if any(lab == f"Recorded reason ({word})" for lab, _ in used) or len(claims) > 6:
                continue
            for e in self._search(f"{project} {word}", [project.split()[0].lower(), word]):
                if any(rid == e.record_id for _, rid in used):
                    continue
                quote = self._whole(e.record_id, e.excerpt, [project.split()[0].lower(), word])
                if quote is None:
                    continue
                used.add((f"Recorded reason ({word})", e.record_id))
                recs.update(self.records([e.record_id]))
                cl = self.claim(f"Stated in the records: “{quote}”", [e.record_id], recs, "document")
                if cl:
                    claims.append(cl)
                break
        claims += self._incidents(recs)
        unknowns = ["The records do not establish how much each factor contributed to the move; treat the cause as "
                    "multifactorial (see TRANSCRIPT_NOTES C-31).",
                    "No probability or confidence percentage for the new date is given; the evidence does not support "
                    "one."]
        return _result(self.basis_json, "qualified", "I read this as: why a release date moved (the recorded change, "
                       "then the dependency, hiring and reliability evidence).",
                       "The release date was moved, and the records show several contributing factors. They don't say "
                       "how much each one mattered.", claims,
                       [recs[i] for c in claims for i in c["evidence"]], unknowns)

    def _incidents(self, recs: dict) -> list[dict]:
        """SEV1 incidents per month from the incident export (counts only; each incident record is cited)."""
        with tenant_tx(self.conn, self.tenant_id, batch_seq=self.basis.batch_seq):
            rows = self.conn.execute(
                "SELECT DISTINCT ON (record_id) record_id, payload->>'Opened' AS opened FROM ledger.source_record"
                " WHERE system = 'incident_log' AND batch_seq <= %s AND payload->>'Severity' = 'SEV1'"
                " ORDER BY record_id, version DESC", (self.basis.batch_seq,)).fetchall()
        by: dict[str, list[str]] = {}
        for r in rows:
            if r["opened"]:
                by.setdefault(str(r["opened"])[:7], []).append(r["record_id"])
        out = []
        for month, ids in sorted(by.items()):
            recs.update(self.records(ids))
            y, m = month.split("-")
            name = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
                    "October", "November", "December"][int(m) - 1]
            c = self.claim(f"Incidents: the incident log records {len(ids)} SEV1 (most severe) incident"
                           f"{'s' if len(ids) != 1 else ''} in {name} {y}.", ids, recs)
            if c:
                out.append(c)
        return out

    def board_change(self, q):
        from .. import metrics

        try:
            res = metrics.run(self.conn, metrics.load_definitions()["reported_figure"], self.basis,
                              ["metric", "period"], {})
        except metrics.MetricError:
            return None

        def when(period: str) -> tuple:
            m = re.search(r"([A-Z][a-z]{2})\w* to ([A-Z][a-z]{2})\w* (\d{4})", period or "")
            months = "JanFebMarAprMayJunJulAugSepOctNovDec"
            return (int(m.group(3)), months.index(m.group(2)) // 3) if m else (0, 0)

        by: dict[str, list] = {}
        for r in res.rows:
            by.setdefault(r.dims["metric"], []).append(r)
        recs = self.records([i for r in res.rows for i in r.inputs])
        claims = []
        for metric, rows in by.items():
            rows = sorted(rows, key=lambda r: when(r.dims["period"]))
            if len(rows) < 2:
                continue
            a, b = rows[-2], rows[-1]
            same = a.value == b.value
            c = self.claim(f"{metric}: {a.value} ({a.dims['period']}) → {b.value} ({b.dims['period']})"
                           + (" (unchanged)" if same else "") + ". As reported; not independently verified.",
                           list(a.inputs) + list(b.inputs), recs, "document")
            if c:
                claims.append(c)
        if not claims:
            return None
        return _result(self.basis_json, "qualified", "I read this as: how the latest board update's reported figures "
                       "compare with the previous one.",
                       f"Compared with the previous board update, {len(claims)} reported figures are shown side by "
                       "side below.", claims, [recs[i] for c in claims for i in c["evidence"]],
                       ["board figures are as reported by the document's author; they are not independently verified"])


_PAY_PERSON = re.compile(r"(\b[\w.\- ]{2,40}'s\s+(salary|pay|ctc|compensation|wages?|bonus|package|earnings)\b)|"
                         r"\b(salary|pay|ctc|compensation|wages?|bonus|package)\s+(of|for)\s+(?!the company\b|"
                         r"(?:the )?(?:team|department|company|engineering|finance|marketing)\b)[A-Za-z]|"
                         r"\bhow much (does|did|is) .{1,40} (earn|make|paid|get paid)\b", re.I)


def pays_an_individual(q: str) -> bool:
    """A question about one person's pay (named or not in the HR export): always refused by POL-PAY-01."""
    return bool(_PAY_PERSON.search(q))

