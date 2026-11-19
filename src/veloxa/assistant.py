"""The one conversational entry point behind Ask Veloxa (plain /v1/ask and saved threads alike).

Governed intent handling, in order; every branch is deterministic and runs AFTER the policy pre-screen where it can
read records, and BEFORE any tool:
1. small talk (greeting, thanks, help): answered without reading any record;
2. contextual follow-ups in a thread ("tell me more", "and why?"): re-asked about the thread's last question;
3. Jira actions: the integration's own permission, then a read, a clarifying question or a PREVIEW (never a write);
4. what-if scenarios: the read-only scenario engine (nothing saved to records or Jira);
5. internal review requests / department notifications: a PREVIEW of a recorded request (pending; Veloxa sends
   nothing and changes no official record; HR matters stay unverified reports until HR reviews them);
6. investigations ("investigate the on-call concern"): the concern routes, with next steps offered;
7. everything else: the evidence pipeline.
No model is involved; models could never bypass retrieval, policies or approvals here.
"""

from __future__ import annotations

import re
import uuid

from . import jira_chat, jira_oauth
from .ask import pipeline, policy
from .temporal import Mode, resolve_basis

SMALL = re.compile(r"^\s*(hi|hello|hey|good (morning|afternoon|evening)|namaste|thanks?( you)?|thank you|cheers|"
                   r"help|what can you do\??|how do i use (this|veloxa)\??)[\s!.?,]*$", re.I)
MORE = re.compile(r"^\s*(tell me more|more( detail(s)?)?|go on|continue|elaborate|and why\??|why\??|what else\??)"
                  r"[\s!.?]*$", re.I)
WHATIF = re.compile(r"\b(what if|what happens if|suppose|scenario|if we (move|delay|shift))\b", re.I)
INVESTIGATE = re.compile(r"^\s*(please\s+)?(investigate|look into|dig into|analy[sz]e)\s+(?P<topic>.+?)[.?!]*$", re.I)
REVIEW = re.compile(r"\b(request|ask for|raise|submit)\b.{0,20}?\b(an? )?(internal )?(?P<dept>people ops|people|hr|"
                    r"finance|engineering|marketing|sales|operations|leadership)?\s*review\b(?:\s+(?:of|about|for|on)"
                    r"\s+(?P<topic>.+?))?[.?!]*$|\bnotify\s+(the\s+)?(?P<dept2>people ops|hr|finance|engineering|"
                    r"marketing|sales|operations|leadership)(\s+team)?\s+(about|of|that)\s+(?P<topic2>.+?)[.?!]*$",
                    re.I)
HR = re.compile(r"\b(people ops|people|hr)\b", re.I)
PREVIEWS = jira_oauth.OneTime(10 * 60)
HELP = ("I'm Veloxa. I answer from your company's admitted records and show where each answer comes from. You can "
        "ask a question (“Why did the Atlas GA date move?”), investigate a concern (“investigate the "
        "on-call concern”), run a what-if (“what if Atlas GA moves by 3 weeks?”), prepare a Jira "
        "action (“create a Jira task for ...”) or request an internal review (“request a People Ops "
        "review of ...”). Nothing is written anywhere without your confirmation.")


def _simple(status: str, answer: str, interpretation: str, **extra) -> dict:
    base = jira_chat._result(status, answer, interpretation, action=extra.pop("action", None),
                             options=extra.pop("options", None), unknowns=extra.pop("unknowns", None))
    base["grade"] = {"value": "D" if status in ("abstained", "refused") else "B",
                     "label": "Conversation" if status == "answered" else base["grade"]["label"],
                     "reasons": ["handled by Veloxa's governed conversation layer"]}
    base["read_basis"] = {"batch_seq": 0, "banner": "No company records were read"}
    return base | extra


def _store(conn, tenant_id, principal, now, question, result) -> dict:
    """Record through the answer pipeline (threads, explanations and audit keep working). Statuses the answer table
    does not know (action_preview, clarify) are stored as their nearest answer status; the response keeps its own."""
    from . import ledger

    basis = resolve_basis(tenant_id, ledger.sealed_releases(conn, tenant_id), Mode.CURRENT, None, "UTC", now)
    shown = result["status"]
    stored = {"action_preview": "answered", "clarify": "abstained"}.get(shown, shown)
    base = {"answer_id": str(uuid.uuid4()), "pipeline_version": "assistant-1", "question": question,
            "diagnostics": {"stage": "assistant", "intent": result.get("intent")}}
    out = pipeline._record(conn, tenant_id, principal, now, basis, base, {**result, "status": stored})
    return {**out, "status": shown}


def respond(conn, tenant_id, principal, tz: str, question: str, now, *, state: dict, thread_last: str | None = None,
            **time_kw) -> dict:
    q = question.strip()
    if SMALL.match(q):                                                   # 1
        text = ("You're welcome." if re.match(r"\s*(thanks?|thank you|cheers)", q, re.I) else
                "Hello. " + HELP if re.match(r"\s*(hi|hello|hey|good|namaste)", q, re.I) else HELP)
        res = _simple("answered", text, "I read this as conversation, so no company record was read.",
                      intent="small_talk")
        return _store(conn, tenant_id, principal, now, q, res)
    if MORE.match(q) and thread_last:                                    # 2
        out = pipeline.answer(conn, tenant_id, tz, principal, thread_last, now, **time_kw)
        systems = sorted({e.get("system", "").replace("_", " ") for e in out.get("evidence", [])} - {""})
        extra = ([f"It rests on {len(out.get('evidence', []))} record(s) from {', '.join(systems)}."] if systems
                 else []) + [f"Still unknown: {u.rstrip('.')}." for u in (out.get("unknowns") or [])[:3]]
        out["answer"] = (f"More on “{thread_last}”:\n\n" + (out.get("answer") or "")
                         + ("\n\n" + " ".join(extra) if extra else ""))
        out["interpretation"] = f"A follow-up to “{thread_last}”. " + (out.get("interpretation") or "")
        return out
    if jira_chat.asks(q) and "jira_permission_for" in state:             # 3
        allowed = state["jira_permission_for"](conn, principal)
        res = (jira_chat.handle(state["jira_get"](), str(tenant_id), principal.principal_id, q) if allowed else
               jira_chat._result("refused", "Jira actions need workspace administration or the company-reader grant. "
                                 "Ask an administrator for access.", "I read this as a Jira request."))
        from . import audit
        from .db import tenant_tx

        with tenant_tx(conn, tenant_id, principal.principal_id):
            audit.append(conn, tenant_id, "jira.chat", {"status": res["status"],
                                                        "operation": (res.get("action") or {}).get("operation"),
                                                        "key": (res.get("action") or {}).get("key")})
        res["intent"] = "jira_action"
        if res["status"] == "refused":
            res["refusal"] = {"policy": "JIRA-PERMISSION", "title": "Jira permission", "reason": res["answer"],
                              "alternative": None}
        return _store(conn, tenant_id, principal, now, q, res)
    m = REVIEW.search(q)
    if m:                                                                # 5: a RECORDED request, not a system action;
        topic = m.group("topic") or m.group("topic2") or ""              # its subject is privacy-screened
        if policy.prescreen(topic, []) is None:
            return _store(conn, tenant_id, principal, now, q, _review_preview(conn, tenant_id, principal, q, m))
    refusal = policy.prescreen(q, [])                                    # names are re-checked by the pipeline too
    if refusal is None and WHATIF.search(q):                             # 4
        out = _scenario(conn, tenant_id, tz, q, now)
        if out is not None:
            return _store(conn, tenant_id, principal, now, q, out)
    m = INVESTIGATE.match(q)
    if refusal is None and m:                                            # 6
        topic = m.group("topic")
        out = pipeline.answer(conn, tenant_id, tz, principal, topic, now, **time_kw)   # the subject itself first
        if out.get("status") in ("abstained", "refused") or (out.get("diagnostics") or {}).get("stage") != "routed":
            alt = pipeline.answer(conn, tenant_id, tz, principal, f"What are the risks and concerns about {topic}?",
                                  now, **time_kw)
            out = alt if alt.get("status") not in ("abstained", "refused") else out
        out["interpretation"] = f"Investigating {topic}: what the records show, why it matters and what is unknown. " \
            + (out.get("interpretation") or "")
        out["follow_ups"] = [f"What if {topic} moves by 2 weeks?", f"Request a review of {topic}",
                             *(out.get("follow_ups") or [])][:4]
        return out
    return pipeline.answer(conn, tenant_id, tz, principal, q, now, **time_kw)   # 7


def _scenario(conn, tenant_id, tz, q, now):
    from . import ledger
    from .risk import scenario_engine

    basis = resolve_basis(tenant_id, ledger.sealed_releases(conn, tenant_id), Mode.CURRENT, None, tz, now)
    subs = scenario_engine.subjects(conn, basis, tz)
    changes = scenario_engine.parse(q, [s["name"] for s in subs])
    if not changes:
        names = ", ".join(s["name"] for s in subs[:6]) or "none in the records"
        return _simple("clarify", "Which release or commitment should move, and by how much? For example: “what "
                       f"if {subs[0]['name'] if subs else 'the release'} moves by 3 weeks?” Known: {names}.",
                       "I read this as a what-if scenario, but no subject and shift were recognised.",
                       intent="scenario", options=[f"What if {s['name']} moves by 2 weeks?" for s in subs[:3]])
    try:
        r = scenario_engine.run(conn, basis, tz, changes)
    except scenario_engine.ScenarioError as e:
        return _simple("abstained", str(e), "I read this as a what-if scenario.", intent="scenario")
    lines = []
    for c in r["changes"]:
        lines.append(f"**{c['subject']}**: {c['assumption']}")
        lines += [f"- {x}" for x in c["calculations"]]
        ai = c["affected_issues"]
        if ai["total"]:
            lines.append(f"- {ai['open']} of {ai['total']} tracker issues are still open.")
        lines += [f"- Blocked by {d['blocked_by']} ({d['issues']} issues)." for d in c["dependencies"]]
    if r["order_after_changes"]:
        lines.append(f"Order after the changes: {r['order_after_changes']}.")
    return _simple("answered", "This is a hypothesis, isolated from real commitments; nothing is saved.\n\n"
                   + "\n".join(lines), "I read this as a what-if scenario (read-only).", intent="scenario",
                   unknowns=r["unknowns"], action={"operation": "scenario", "result": r},
                   evidence=[], read_basis={"batch_seq": basis.batch_seq,
                                            "banner": f"Using records up to release {basis.batch_seq} (current)"})


def _review_preview(conn, tenant_id, principal, q, m) -> dict:
    """A recorded internal request, previewed. HR-related requests stay unverified reports pending HR review; nothing
    official is changed and no one is contacted by Veloxa."""
    from .db import tenant_tx

    dept_word = (m.group("dept") or m.group("dept2") or "").lower()
    topic = (m.group("topic") or m.group("topic2") or "").strip()
    with tenant_tx(conn, tenant_id, principal.principal_id):
        depts = [dict(r) for r in conn.execute("SELECT department_id, name FROM directory.department WHERE"
                                               " tenant_id = %s ORDER BY name", (tenant_id,)).fetchall()]
    if not depts:
        return _simple("abstained", "No departments are configured in this workspace, so a review request has no one "
                       "to go to. An administrator adds departments in Administration.",
                       "I read this as an internal review request.", intent="review_request")
    want = "people" if HR.fullmatch(dept_word or "-") or dept_word in ("people ops", "hr") else dept_word
    hit = [d for d in depts if want and (want in d["department_id"].lower() or want in d["name"].lower())]
    if len(hit) != 1:
        return _simple("clarify", "Which department should review this? " + ", ".join(d["name"] for d in depts) + ".",
                       "I read this as an internal review request, but the department is not clear.",
                       intent="review_request",
                       options=[f"Request a {d['name']} review of {topic or 'this'}" for d in depts[:4]])
    if not topic:
        return _simple("clarify", f"What should {hit[0]['name']} review? Describe the work item or record.",
                       "I read this as an internal review request without a subject.", intent="review_request")
    hr = HR.search(dept_word or "") is not None or hit[0]["department_id"].startswith("people")
    text = topic[0].upper() + topic[1:]
    preview = {"operation": "internal_request", "department_id": hit[0]["department_id"],
               "department": hit[0]["name"], "request": text, "kind": "review",
               "status_after_confirmation": "submitted (pending review)",
               "notes": (["This is an unverified report until People Ops reviews it; no HR record is changed.",
                          "Do not include health, pay, leave reasons or other sensitive personal details."]
                         if hr else []) + ["Veloxa records the request; it does not contact anyone."]}
    preview["preview_id"] = PREVIEWS.put({"tenant": str(tenant_id), "principal": principal.principal_id, **preview})
    return _simple("action_preview", f"Here is the review request I would record for {hit[0]['name']}. Confirm to "
                   "submit it. Nothing has been recorded yet.", "I read this as an internal review request (preview "
                   "only until you confirm).", intent="review_request", action=preview)


def commit_request(conn, tenant_id, principal, preview_id: str, confirm: bool) -> dict:
    from . import product

    if confirm is not True:
        raise ValueError("submitting a request requires explicit confirmation")
    p = PREVIEWS.take(preview_id)
    if not p or p["tenant"] != str(tenant_id) or p["principal"] != principal.principal_id:
        raise ValueError("this preview expired or was already used; prepare the request again")
    if policy.prescreen(p["request"], []) is not None:
        raise ValueError("the request text matches a privacy policy; remove sensitive details and try again")
    out = product.create_clarification(conn, tenant_id, principal.principal_id, p["department_id"],
                                       f"Review request: {p['request']}",
                                       {"kind": p["kind"], "source": "ask", "unverified_report": True}, True)
    return {**out, "department": p["department"]}
