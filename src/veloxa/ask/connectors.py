"""Answers about the connected sources (GitHub, Jira, Gmail), written as plain sentences from the admitted records.

A question reaches these routes only when it names the source ("GitHub", "the repo", "pull requests", "Jira",
"tickets", "e-mail", "inbox"). Each route returns None when the records hold nothing for it, so the question falls
through to the normal pipeline; nothing is answered from unrelated records.

Access: GitHub and Jira rows go through `Router.records` (the same per-system and class checks as every other
answer). E-mail is found only through `tools.text_search`, which returns a message only to its sender and
recipients.
"""

from __future__ import annotations

import ast
import re
from collections import Counter

from .. import tools
from ..db import tenant_tx

GITHUB = re.compile(r"\b(github|git hub|repo|repos|repository|repositories|pull requests?|PRs?|commits|"
                    r"(last|latest|recent) commit|branch(es)?|workflow runs?|ci (runs?|builds?|checks?)|"
                    r"builds? (failing|failed|passing|status)|code changes?)\b", re.I)
JIRA = re.compile(r"\b(jira|tickets?)\b", re.I)
EMAIL = re.compile(r"\b(e-?mails?|inbox|mailbox|my mail|messages? (from|about)|wrote to me|sent me)\b", re.I)

PR = re.compile(r"\b(pull requests?|PRs?|reviews?)\b", re.I)
CI = re.compile(r"\b(ci|workflows?|builds?|checks?|pipelines?|tests? (failing|passing))\b", re.I)
CHANGED = re.compile(r"\b(changed?|changes|recent(ly)?|latest|last|new|commits?|activity|happen\w*)\b", re.I)
OPEN = re.compile(r"\b(open|pending|outstanding|unresolved|to do|in progress|not done)\b", re.I)
OVERDUE = re.compile(r"\b(overdue|late|past due|due)\b", re.I)
DONE = {"done", "closed", "resolved", "complete", "completed"}
STOP = {"what", "which", "who", "when", "where", "how", "why", "is", "are", "was", "were", "the", "a", "an", "in",
        "on", "of", "to", "for", "my", "me", "i", "do", "does", "did", "say", "says", "said", "about", "any", "there",
        "email", "emails", "e-mail", "e-mails", "mail", "inbox", "mailbox", "message", "messages", "from", "sent",
        "wrote", "tell", "show", "and", "or", "we", "our", "us", "have", "has", "had", "it", "this", "that", "with"}


def asks(q: str) -> bool:
    return bool(GITHUB.search(q) or JIRA.search(q) or EMAIL.search(q))


def answer(router, q: str):
    """The first connector route that has records for the question, else None."""
    if EMAIL.search(q):
        out = email(router, q)
        if out is not None:
            return out
    if GITHUB.search(q):
        out = github(router, q)
        if out is not None:
            return out
    if JIRA.search(q):
        return jira(router, q)
    return None


def _day(v) -> str:
    from .routes import _human

    return _human(str(v)[:10]) if v else "an unrecorded date"


def _data(p: dict) -> dict:
    d = p.get("data")
    if isinstance(d, str):                   # older imports kept the structure as text
        try:
            d = ast.literal_eval(d)
        except (ValueError, SyntaxError):
            d = {}
    return d if isinstance(d, dict) else {}


# ------------------------------------------------------------------ GitHub
def _github_rows(router) -> list[dict]:
    k = router.basis.batch_seq
    with tenant_tx(router.conn, router.tenant_id, router.principal.principal_id, k):
        rows = router.conn.execute(
            "SELECT DISTINCT ON (record_id) record_id, payload FROM ledger.visible_source_record"
            " WHERE tenant_id = %s AND batch_seq <= %s AND system = 'github_app'"
            " ORDER BY record_id, version DESC", (router.tenant_id, k)).fetchall()
    return [r for r in rows if isinstance(r["payload"], dict)]


def github(router, q: str):
    rows = _github_rows(router)
    if not rows:
        return None
    recs = router.records([r["record_id"] for r in rows])
    rows = [r for r in rows if r["record_id"] in recs]
    if not rows:
        return None
    by = {}
    for r in rows:
        by.setdefault(r["payload"].get("kind"), []).append(r)
    when = lambda r: str(r["payload"].get("updated_at") or r["payload"].get("created_at") or "")  # noqa: E731
    claims, unknowns = [], ["GitHub records are as of the last import that was reviewed and admitted; later activity "
                            "is not shown until the next import is admitted."]

    def add(text, rid):
        c = router.claim(text, [rid], recs)
        if c:
            claims.append(c)

    if PR.search(q):
        prs = sorted(by.get("pull_request", []), key=when, reverse=True)
        want_open = bool(OPEN.search(q))
        for r in prs:
            p, d = r["payload"], _data(r["payload"])
            state = "merged" if d.get("merged_at") else (d.get("state") or "state not recorded")
            if want_open and state != "open":
                continue
            add(f"Pull request #{d.get('number')} in {p.get('repository')}: “{d.get('title') or 'untitled'}” is "
                f"{state}, opened by {p.get('actor') or 'an unrecorded author'} on {_day(p.get('created_at'))}"
                + (f" and merged on {_day(d['merged_at'])}." if d.get("merged_at") else "."), r["record_id"])
            if len(claims) >= 8:
                break
        if not claims:
            text = "No open pull requests are recorded." if want_open else "No pull requests are recorded."
            return _plain(router, text, rows[:1], recs, unknowns, "GitHub pull requests")
        lead = "Pull requests in the GitHub records:"
    elif CI.search(q):
        runs = sorted(by.get("workflow_run", []), key=when, reverse=True)
        if not runs:
            return None
        recent = runs[:20]
        tally = Counter((_data(r["payload"]).get("conclusion") or _data(r["payload"]).get("status") or "unknown")
                        for r in recent)
        summary = ", ".join(f"{n} {c}" for c, n in tally.most_common())
        add(f"Of the last {len(recent)} CI runs, {summary}.", recent[0]["record_id"])
        for r in [x for x in recent if _data(x["payload"]).get("conclusion") == "failure"][:3]:
            d = _data(r["payload"])
            add(f"Failed: “{d.get('name') or 'workflow'}” on {d.get('head_branch') or 'an unrecorded branch'}, "
                f"{_day(r['payload'].get('updated_at'))}.", r["record_id"])
        unknowns.append("A CI run is not proof that anything was deployed.")
        lead = "CI runs in the GitHub records:"
    elif CHANGED.search(q) or not by.get("repository"):
        commits = sorted(by.get("commit", []), key=when, reverse=True)[:6]
        for r in commits:
            p, d = r["payload"], _data(r["payload"])
            first = (d.get("message") or "no message").strip().splitlines()[0][:140]
            add(f"{_day(p.get('created_at'))}: “{first}” ({p.get('actor') or 'unrecorded author'}, "
                f"{p.get('repository')}).", r["record_id"])
        if not claims:
            return None
        unknowns.append("A commit does not prove that any piece of work is complete.")
        lead = "The most recent commits in the GitHub records:"
    else:
        for r in by.get("repository", [])[:5]:
            p = r["payload"]
            repo = p.get("repository")
            n = Counter(x["payload"].get("kind") for x in rows if x["payload"].get("repository") == repo)
            latest = max((when(x) for x in rows if x["payload"].get("repository") == repo
                          and x["payload"].get("kind") == "commit"), default=None)
            parts = [f"{n[k]} {label}" for k, label in (("commit", "commits"), ("pull_request", "pull requests"),
                                                       ("branch", "branches"), ("workflow_run", "CI runs"))
                     if n.get(k)]
            add(f"{repo}: {', '.join(parts) or 'no activity'} recorded"
                + (f"; latest commit on {_day(latest)}." if latest else "."), r["record_id"])
        lead = "Connected GitHub repositories:"
    if not claims:
        return None
    return _answer(router, lead, claims, recs, unknowns, "what the connected GitHub repositories show")


# ------------------------------------------------------------------ Jira
def jira(router, q: str):
    from .continuity import _claims

    k = router.basis.batch_seq
    with tenant_tx(router.conn, router.tenant_id, router.principal.principal_id, k):
        issues = _claims(router.conn, router.tenant_id, k, "issue")
    if not issues:
        return None
    recs = router.records({r for d in issues.values() for r in d["_records"]})
    issues = {s: d for s, d in issues.items() if set(d["_records"]) & set(recs)}
    if not issues:
        return None
    status = lambda d: str(d.get("status_at_export") or "not recorded")  # noqa: E731
    claims = []

    def add(text, d):
        c = router.claim(text, sorted(set(d["_records"]) & set(recs)), recs)
        if c:
            claims.append(c)

    open_ = {s: d for s, d in issues.items() if status(d).lower() not in DONE}
    if OVERDUE.search(q) or OPEN.search(q):
        from ..signals.registry import assessment_date

        today = assessment_date(router.basis, router.tz)
        pick = open_
        if OVERDUE.search(q) and not OPEN.search(q):
            pick = {s: d for s, d in open_.items() if d.get("due_date") and str(d["due_date"])[:10] < str(today)}
        for s, d in sorted(pick.items(), key=lambda x: str(x[1].get("due_date") or "9999"))[:10]:
            key = s.split(":", 1)[1]
            bits = [f"{key}: {d.get('summary') or 'no summary recorded'} ({status(d)}"]
            bits.append(f", assigned to {d.get('assignee_name')}" if d.get("assignee_name") else ", unassigned")
            bits.append(f", due {_day(d['due_date'])})." if d.get("due_date") else ", no due date).")
            add("".join(bits), d)
        if not claims:
            return _plain(router, "No overdue issues are recorded." if OVERDUE.search(q) else
                          "No open issues are recorded.", [], recs, [], "Jira issues", ids=[
                              next(iter(next(iter(issues.values()))["_records"]))])
        lead = "Overdue issues in the tracker:" if pick is not open_ else "Open issues in the tracker:"
    else:
        by_project = {}
        for s, d in issues.items():
            proj = d.get("project") or s.split(":", 1)[1].rsplit("-", 1)[0]
            by_project.setdefault(proj, []).append(d)
        for proj, ds in sorted(by_project.items(), key=lambda x: -len(x[1]))[:8]:
            tally = Counter(status(d) for d in ds)
            add(f"{proj}: {len(ds)} issue{'s' if len(ds) != 1 else ''} "
                f"({', '.join(f'{n} {st}' for st, n in tally.most_common())}).",
                {"_records": {r for d in ds for r in d["_records"]}})
        lead = f"The tracker holds {len(issues)} issues, {len(open_)} not done:"
    if not claims:
        return None
    return _answer(router, lead, claims, recs, ["Tracker records are as of their last sync or export; later changes "
                                                "in Jira are not shown until the next sync is reviewed and admitted."],
                   "what the tracker (Jira) records show")


# ------------------------------------------------------------------ Gmail
def email(router, q: str):
    words = [w for w in re.findall(r"[A-Za-z0-9][\w.-]*", q.lower()) if w not in STOP and len(w) > 2]
    if not words:
        return None
    res = tools.text_search(router.conn, router.basis, router.principal, query=" OR ".join(words), limit=12)
    hits = [e for e in res.evidence if e.system == "gmail_oauth"][:5]
    if not hits:
        return None
    recs = router.records([e.record_id for e in hits])
    k = router.basis.batch_seq
    with tenant_tx(router.conn, router.tenant_id, router.principal.principal_id, k):
        meta = {r["record_id"]: r["payload"] for r in router.conn.execute(
            "SELECT DISTINCT ON (record_id) record_id, payload FROM ledger.visible_source_record WHERE tenant_id = %s"
            " AND record_id = ANY(%s) ORDER BY record_id, version DESC", (router.tenant_id, list(recs))).fetchall()}
    from .routes import _quote

    claims = []
    for e in hits:
        if e.record_id not in recs:
            continue
        p = meta.get(e.record_id, {})
        sender = re.sub(r"\s*<[^>]+>", "", p.get("from") or "an unrecorded sender").strip()
        c = router.claim(f"{sender} wrote on {_day(_iso(p.get('date')))} (“{p.get('subject') or 'no subject'}”): "
                         f"{_quote(e.excerpt)}", [e.record_id], recs, kind="communication")
        if c:
            claims.append(c)
    if not claims:
        return None
    return _answer(router, "What your e-mail says:", claims, recs,
                   ["E-mail is a communication, not a verified record: a message states what its sender said."],
                   "what your connected e-mail says about this")


def _iso(d):
    from email.utils import parsedate_to_datetime

    try:
        return parsedate_to_datetime(d).date().isoformat() if d else None
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ shared
def _answer(router, lead, claims, recs, unknowns, reading):
    from .routes import _result

    text = lead + "\n" + "\n".join(f"- {c['text']}" for c in claims)
    return _result(router.basis_json, "answered", f"I read this as: {reading}.", text, claims,
                   [recs[i] for c in claims for i in c["evidence"]], unknowns)


def _plain(router, text, rows, recs, unknowns, reading, ids=None):
    """A one-sentence answer (for example "no open pull requests"), still citing the records it was read from."""
    from .routes import _result

    c = router.claim(text, ids or [r["record_id"] for r in rows], recs)
    if c is None:
        return None
    return _result(router.basis_json, "answered", f"I read this as: {reading}.", text, [c],
                   [recs[i] for i in c["evidence"]], unknowns)
