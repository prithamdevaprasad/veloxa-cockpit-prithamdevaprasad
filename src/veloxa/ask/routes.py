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
    def route(self, q: str) -> dict | None:
        from . import continuity

        if continuity.asks(q):                      # before DELAY: "what does the resignation do to the release?"
            out = continuity.answer(self, q)
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

