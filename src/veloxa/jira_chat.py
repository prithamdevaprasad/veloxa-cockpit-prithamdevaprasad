"""Jira actions from Ask ("create a Jira task for ...", "update ENG-12 ...", "assign ENG-12 to ...", "prepare an
overdue-issues report"). Nothing is ever written here.

- Projects, issue types and assignees are resolved ONLY from what Jira returns; anything ambiguous or missing becomes
  a clarifying question with the real options. Nothing is invented.
- Create and update produce a PREVIEW (via `jira_oauth.Jira.preview_*`, validated against Jira's own metadata, with
  the duplicate check). The write happens only when the user confirms that preview (`/v1/integrations/jira/commit`).
- Reports are reads. No delete, no transition.
"""

from __future__ import annotations

import re

from . import jira_oauth

KEY = re.compile(r"\b([A-Z][A-Z0-9_]{1,19}-\d{1,9})\b")
_CREATE = re.compile(r"\b(create|open|raise|file|log|add|make)\b.{0,40}\b(task|ticket|issue|bug|story)\b", re.I)
_ASSIGN = re.compile(r"\bassign\b.{0,60}\bto\s+(?P<who>[A-Za-z][A-Za-z .'-]{1,60}?)\s*[.?!]?$", re.I)
_UPDATE = re.compile(r"\b(update|change|set|rename|comment|add a comment|edit)\b", re.I)
_OVERDUE = re.compile(r"\boverdue\b.{0,30}\b(issues?|tickets?|tasks?|items?|report)\b|\b(issues?|tickets?|tasks?)\b"
                      r".{0,20}\b(past due|overdue)\b", re.I)
_JIRA = re.compile(r"\bjira\b", re.I)


def asks(q: str) -> bool:
    """A Jira action or report request (not a question about company records that merely mentions work)."""
    if _OVERDUE.search(q) and (_JIRA.search(q) or re.search(r"\b(report|prepare|list)\b", q, re.I)):
        return True
    if _CREATE.search(q) and (_JIRA.search(q) or re.search(r"\b(ticket|task)\b", q, re.I)):
        return True
    return bool(KEY.search(q) and (_ASSIGN.search(q) or _UPDATE.search(q)))


def _result(status: str, answer: str, interpretation: str, *, action: dict | None = None,
            options: list[str] | None = None, unknowns: list[str] | None = None) -> dict:
    return {"status": status, "interpretation": interpretation, "answer": answer,
            "grade": {"value": "A" if status in ("answered", "action_preview") else "D",
                      "label": "Live from Jira" if status in ("answered", "action_preview") else "No usable evidence",
                      "reasons": ["Jira integration; nothing is written without your confirmation"]},
            "qualifiers": [], "claims": [], "disagreements": [], "unknowns": unknowns or [], "refusal": None,
            "read_basis": {"batch_seq": 0, "banner": "Live from Jira (not admitted evidence)"}, "facts": [],
            "evidence": [], "action": action, **({"follow_ups": options} if options else {}),
            "verification": {"deterministic": "Jira integration", "dropped_claims": 0, "entailment": "not run"}}


def _summary(q: str) -> str:
    quoted = re.search(r"[\"“](.+?)[\"”]", q)
    if quoted:
        return quoted.group(1).strip()
    m = re.search(r"\b(?:for|about|to|:)\s+(?:an?\s+|the\s+)?(.+)$", q, re.I)
    text = (m.group(1) if m else q).strip().rstrip(".?!")
    return (text[:1].upper() + text[1:])[:255]


def handle(j: jira_oauth.Jira | None, tenant: str, principal: str, q: str) -> dict:
    if j is None:
        return _result("abstained", "Jira isn't configured on this server, so I can't act in Jira. An administrator "
                       "can set it up in Administration > Integrations.", "I read this as a Jira request.")
    st = j.status(tenant)
    if not st.get("connected") or st.get("expired") or st.get("needs_site_choice"):
        why = ("Jira access has expired or was revoked" if st.get("expired") else
               "a Jira site still needs to be chosen" if st.get("needs_site_choice") else "Jira isn't connected")
        return _result("abstained", f"I can't do that yet: {why}. An administrator can fix this in Administration > "
                       "Integrations.", "I read this as a Jira request.")
    try:
        if _OVERDUE.search(q):
            return _overdue(j, tenant, q)
        key = KEY.search(q)
        if key and _ASSIGN.search(q):
            return _assign(j, tenant, principal, key.group(1), _ASSIGN.search(q).group("who").strip())
        if key and _UPDATE.search(q):
            return _update(j, tenant, principal, key.group(1), q)
        return _create(j, tenant, principal, q)
    except jira_oauth.JiraError as e:
        return _result("abstained", str(e), "I read this as a Jira request; Jira did not allow it.")


def _project(j, tenant, q):
    projects = j.projects(tenant)
    low = q.lower()
    hits = [p for p in projects if re.search(rf"\b{re.escape(p['key'].lower())}\b", low)
            or (p.get("name") and p["name"].lower() in low)]
    if len(hits) == 1:
        return hits[0], projects
    if not hits and len(projects) == 1:
        return projects[0], projects
    return None, (hits or projects)


def _overdue(j, tenant, q):
    proj, _ = _project(j, tenant, q)
    issues = j.overdue(tenant, proj["key"] if proj else None)
    scope = f" in {proj['key']}" if proj else ""
    if not issues:
        return _result("answered", f"Jira shows no overdue open issues{scope}.", "I read this as: an overdue-issues "
                       "report from Jira (a read; nothing is changed).")
    lines = [f"- {i['key']}: {i['summary']} (due {i['due_date']}, {i['status']}, "
             f"{i['assignee'] or 'unassigned'})" + (f" {i['url']}" if i["url"] else "") for i in issues]
    return _result("answered", f"{len(issues)} overdue open issue{'s' if len(issues) != 1 else ''}{scope}, oldest "
                   "due date first:\n" + "\n".join(lines), "I read this as: an overdue-issues report from Jira (a "
                   "read; nothing is changed).",
                   unknowns=[f"Live from Jira at {issues[0]['source']['retrieved_at']}; not admitted evidence."])


def _create(j, tenant, principal, q):
    proj, options = _project(j, tenant, q)
    if proj is None:
        names = [f"{p['key']} ({p['name']})" for p in options[:8]]
        return _result("clarify", "Which Jira project should this go in? " + ", ".join(names) + ".",
                       "I read this as: create a Jira issue, but the project is not clear.",
                       options=[f"{q.rstrip('.?!')} in project {p['key']}" for p in options[:4]])
    types = j.issue_types(tenant, proj["key"])
    want = re.search(r"\b(task|bug|story|epic|sub-?task)\b", q, re.I)
    wanted = (want.group(1) if want else "task").lower().replace("-", "")
    match = [t for t in types if t["name"].lower().replace("-", "") == wanted]
    if not match:
        return _result("clarify", f"{proj['key']} has these issue types: " + ", ".join(t["name"] for t in types)
                       + ". Which one should I use?", "I read this as: create a Jira issue, but the issue type is not "
                       "one this project offers.",
                       options=[f"{q.rstrip('.?!')} as a {t['name']}" for t in types[:4]])
    summary = _summary(q)
    filler = {"engineering", "finance", "marketing", "sales", "operations", "people", "ops", "hr", "support",
              "leadership", "jira", "task", "ticket", "issue", "bug", "story", "a", "an", "the", "in", "project",
              "space", "for", "team", proj["key"].lower(), *str(proj.get("name") or "").lower().split()}
    if not re.search(r"[\"“]", q) and len([w for w in re.findall(r"[a-z0-9]+", summary.lower())
                                                 if w not in filler]) < 2:
        res = _result("clarify", f"What should the {proj['key']} task say? Give a short title, for example: "
                      "“Confirm the replacement GA date with the release owner”.",
                      "I read this as: create a Jira task, but no task title was given.")
        res["awaiting"] = {"field": "summary", "project": proj["key"], "type": match[0]["name"]}
        return res
    description = f"Requested in Veloxa: “{q.strip()}”"
    p = j.preview_create(tenant, principal, proj["key"], match[0]["id"], summary, description)
    return _result("action_preview", f"Here is the issue I would create in {proj['key']}. Check it, then confirm to "
                   "write it to Jira. Nothing has been created yet.", "I read this as: create a Jira issue (preview "
                   "only until you confirm).", action=p)


def _update(j, tenant, principal, key, q):
    fields, comment = {}, ""
    m = re.search(r"\b(?:rename|change the (?:title|summary)|set the (?:title|summary)|summary)\b.*?\bto\s+(.+)$", q,
                  re.I)
    if m:
        fields["summary"] = m.group(1).strip().strip("\"“”").rstrip(".")
    m = re.search(r"\bdue(?: date)?\b.*?\b(\d{4}-\d{2}-\d{2})\b", q, re.I)
    if m:
        fields["duedate"] = m.group(1)
    m = re.search(r"\bcomment\b\s*(?:saying|that|:)?\s*(.+)$", q, re.I)
    if m:
        comment = m.group(1).strip().strip("\"“”")
    if not fields and not comment:
        return _result("clarify", f"What should change on {key}? For example: “rename {key} to ...”, “set"
                       f" the due date of {key} to 2026-12-01” or “comment on {key}: ...”.",
                       "I read this as: update a Jira issue, but no change was stated.")
    p = j.preview_update(tenant, principal, key, fields, comment)
    return _result("action_preview", f"Here is the change I would make to {key}. Check it, then confirm to write it "
                   "to Jira. Nothing has changed yet.", "I read this as: update a Jira issue (preview only until you "
                   "confirm).", action=p)


def _assign(j, tenant, principal, key, who):
    users = j.assignable(tenant, key, who)
    if not users:
        return _result("abstained", f"Jira lists nobody matching “{who}” who can be assigned {key}. I won't "
                       "assign it to anyone else.", "I read this as: assign a Jira issue.")
    exact = [u for u in users if (u["name"] or "").lower() == who.lower()]
    pick = exact if len(exact) == 1 else users
    if len(pick) > 1:
        return _result("clarify", f"Several people can be assigned {key}: " + ", ".join(u["name"] for u in pick[:6])
                       + ". Who did you mean?", "I read this as: assign a Jira issue, but the person is ambiguous.",
                       options=[f"Assign {key} to {u['name']}" for u in pick[:4]])
    p = j.preview_update(tenant, principal, key, {"assignee": {"accountId": pick[0]["account_id"]}})
    return _result("action_preview", f"Here is the assignment I would make on {key}. Confirm to write it to Jira. "
                   "Nothing has changed yet.", "I read this as: assign a Jira issue (preview only until you confirm).",
                   action=p)
