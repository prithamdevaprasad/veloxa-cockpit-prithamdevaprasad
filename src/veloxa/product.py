"""Saved Ask threads, contextual questions, agent updates and department clarifications (migration 0020).

Everything is a persisted record:
- **Threads:** a thread is the ordered sequence of answers recorded by the existing Ask pipeline
  (`runtime.answer`), never a client-side transcript.
- **Agent updates:** summarised ONLY from the executions and citations a real agent session persisted. A
  synthetic (`syn-`) agent's update carries `synthetic = true`, shown as DEMO.
- **Clarification requests:** addressed to a department. The suggested recipient is the department head from the
  authorised directory. Veloxa contacts nobody, and an answer meant as evidence must arrive as a data-room
  submission.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime

import psycopg

from . import audit, tools
from .db import tenant_tx

PRONOUN = re.compile(r"\b(it|this|that|they|them|its|their|these|those)\b", re.I)


class ProductError(Exception):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def _j(row):
    return {k: (str(v) if isinstance(v, uuid.UUID) else v.isoformat() if isinstance(v, datetime) else v)
            for k, v in dict(row).items()}


# ------------------------------------------------------------------ threads
def create_thread(conn, tenant_id, owner: str, title: str, context: dict | None = None) -> str:
    tid = uuid.uuid4()
    with tenant_tx(conn, tenant_id, owner):
        conn.execute("INSERT INTO runtime.thread (tenant_id, thread_id, owner_id, title, context)"
                     " VALUES (%s, %s, %s, %s, %s::jsonb)", (tenant_id, tid, owner, title[:200],
                                                            json.dumps(context or {})))
        audit.append(conn, tenant_id, "thread.created", {"thread": str(tid), "context": (context or {}).get("kind")})
    return str(tid)


def list_threads(conn, tenant_id, owner: str, archived: bool = False) -> list[dict]:
    with tenant_tx(conn, tenant_id, owner):
        rows = conn.execute("SELECT * FROM runtime.thread_current WHERE tenant_id = %s AND archived = %s"
                            " ORDER BY coalesce(last_at, created_at) DESC", (tenant_id, archived)).fetchall()
    return [_j(r) for r in rows]


def get_thread(conn, tenant_id, owner: str, thread_id: str) -> dict | None:
    with tenant_tx(conn, tenant_id, owner):
        t = conn.execute("SELECT * FROM runtime.thread_current WHERE tenant_id = %s AND thread_id = %s",
                         (tenant_id, thread_id)).fetchone()
        if t is None:
            return None
        answers = conn.execute(
            "SELECT ta.seq, ta.added_at, a.question, a.answer FROM runtime.thread_answer ta JOIN runtime.answer a"
            " USING (tenant_id, answer_id) WHERE ta.tenant_id = %s AND ta.thread_id = %s ORDER BY ta.seq",
            (tenant_id, thread_id)).fetchall()
    return {**_j(t), "turns": [{"seq": a["seq"], "question": a["question"], "asked_at": a["added_at"].isoformat(),
                                "answer": a["answer"]} for a in answers]}


def contextual_question(question: str, context: dict) -> str:
    """A follow-up that refers back ("why did it change?") is asked about the thread's subject. Deterministic and
    visible: the question actually asked is stored and shown."""
    subject = (context or {}).get("subject")
    if subject and PRONOUN.search(question) and subject.lower() not in question.lower():
        return f"{question.rstrip('?')} (regarding {subject})?"
    return question


def ask_in_thread(conn, tenant_id, principal: tools.Principal, tz: str, thread_id: str, question: str,
                  now: datetime, state: dict | None = None, **time_kw) -> dict:
    t = get_thread(conn, tenant_id, principal.principal_id, thread_id)
    if t is None:
        raise ProductError("not_found", "no such thread")
    asked = contextual_question(question, t["context"])
    with tenant_tx(conn, tenant_id, principal.principal_id):
        last = conn.execute("SELECT a.question FROM runtime.thread_answer t JOIN runtime.answer a ON a.tenant_id ="
                            " t.tenant_id AND a.answer_id = t.answer_id WHERE t.tenant_id = %s AND t.thread_id = %s"
                            " ORDER BY t.seq DESC LIMIT 1", (tenant_id, thread_id)).fetchone()
    from . import assistant

    answer = assistant.respond(conn, tenant_id, principal, tz, asked, now, state=state or {},
                               thread_last=last["question"] if last else None, **time_kw)
    with tenant_tx(conn, tenant_id, principal.principal_id):
        seq = conn.execute("SELECT coalesce(max(seq), 0) + 1 AS s FROM runtime.thread_answer WHERE tenant_id = %s"
                           " AND thread_id = %s", (tenant_id, thread_id)).fetchone()["s"]
        conn.execute("INSERT INTO runtime.thread_answer (tenant_id, thread_id, seq, answer_id) VALUES (%s, %s, %s, %s)",
                     (tenant_id, thread_id, seq, answer["audit_id"]))
    return {"thread_id": thread_id, "seq": seq, "asked": asked, "answer": answer}


def thread_event(conn, tenant_id, owner: str, thread_id: str, event: str, title: str | None = None) -> None:
    with tenant_tx(conn, tenant_id, owner):
        if conn.execute("SELECT 1 FROM runtime.thread WHERE tenant_id = %s AND thread_id = %s",
                        (tenant_id, thread_id)).fetchone() is None:
            raise ProductError("not_found", "no such thread")
        conn.execute("INSERT INTO runtime.thread_event (tenant_id, thread_id, event, title) VALUES (%s, %s, %s, %s)",
                     (tenant_id, thread_id, event, title))


# ------------------------------------------------------------------ agent updates
_KIND_HINTS = (("source_disagreement", r"\b(disagree|conflict|differ)\w*"),
               ("commitment_changed", r"\b(moved|postponed|delayed|slipped|revised|changed)\b"),
               ("dependency_identified", r"\b(depends?|dependency|dependencies|blocked by|waiting on)\b"),
               ("incident_update", r"\b(incident|outage|sev[0-9])\b"))


def publish_update(conn, tenant_id, interviewer: str, session_id: str, project: str | None = None) -> dict:
    """Summarise ONE persisted session into an update: its latest substantive turn, with that turn's citations.
    Refused for a session with no answered turn. Only the session's interviewer can publish (RLS)."""
    with tenant_tx(conn, tenant_id, interviewer):
        s = conn.execute("SELECT s.session_id, s.agent_id, c.category, c.activation FROM runtime.agent_session s"
                         " JOIN runtime.agent_config c USING (tenant_id, agent_id, config_version)"
                         " WHERE s.tenant_id = %s AND s.session_id = %s", (tenant_id, session_id)).fetchone()
        if s is None:
            raise ProductError("not_found", "no such session")
        turns = conn.execute("SELECT status, response FROM runtime.agent_execution WHERE tenant_id = %s"
                             " AND session_id = %s ORDER BY executed_at", (tenant_id, session_id)).fetchall()
    usable = [t for t in turns if t["status"] in ("answered", "qualified", "abstained")]
    if not usable:
        raise ProductError("nothing_to_publish", "the session has no answered turn to summarise")
    last = usable[-1]
    resp = last["response"] or {}
    claims = [c.get("text", "") for c in resp.get("claims", []) if c.get("text")]
    evidence = sorted({e.get("record_id") for e in resp.get("evidence", []) if e.get("record_id")})
    text = " ".join(claims[:2]) or (resp.get("unknowns") or ["No admitted evidence answered this question."])[0]
    kind = "unresolved_question" if last["status"] == "abstained" else next(
        (k for k, rx in _KIND_HINTS if re.search(rx, " ".join(claims), re.I)), "new_finding")
    question = resp.get("message") or "the interview"
    update = {"update_id": str(uuid.uuid4()), "session_id": str(session_id), "agent_id": s["agent_id"],
              "department": s["category"], "kind": kind, "title": (claims[0] if claims else question)[:200],
              "summary": text[:2000], "status": last["status"], "project": project, "evidence": evidence,
              "suggested_follow_up": f"Ask Veloxa about: {question[:150]}",
              "synthetic": s["activation"] == "synthetic_test"}
    with tenant_tx(conn, tenant_id, interviewer):
        conn.execute("INSERT INTO runtime.agent_update (tenant_id, update_id, session_id, agent_id, department, kind,"
                     " title, summary, status, project, evidence, suggested_follow_up, synthetic, interviewer_id)"
                     " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)",
                     (tenant_id, update["update_id"], session_id, s["agent_id"], s["category"], kind, update["title"],
                      update["summary"], last["status"], project, json.dumps(evidence),
                      update["suggested_follow_up"], update["synthetic"], interviewer))
        audit.append(conn, tenant_id, "agent.update_published", {"update": update["update_id"],
                                                                 "session": str(session_id)})
    return update


def list_updates(conn, tenant_id, principal: str, department: str | None = None, limit: int = 50) -> list[dict]:
    with tenant_tx(conn, tenant_id, principal):
        rows = conn.execute("SELECT * FROM runtime.agent_update WHERE tenant_id = %s"
                            " AND (%s::text IS NULL OR department = %s) ORDER BY created_at DESC LIMIT %s",
                            (tenant_id, department, department, limit)).fetchall()
    return [_j(r) for r in rows]


def get_update(conn, tenant_id, principal: str, update_id: str) -> dict | None:
    with tenant_tx(conn, tenant_id, principal):
        r = conn.execute("SELECT * FROM runtime.agent_update WHERE tenant_id = %s AND update_id = %s",
                         (tenant_id, update_id)).fetchone()
    return _j(r) if r else None


# ------------------------------------------------------------------ clarifications
def suggested_recipient(conn, tenant_id, principal: str, department_id: str) -> dict | None:
    """The department head from the authorised directory (a relation, never a guess). None if there is none."""
    with tenant_tx(conn, tenant_id, principal):
        r = conn.execute("SELECT p.principal_id, p.display_name FROM policy.live_tuple t JOIN directory.principal p"
                         " ON p.tenant_id = t.tenant_id AND p.principal_id = 'person:' || t.subject_id"
                         " WHERE t.tenant_id = %s AND t.resource_type = 'department' AND t.resource_id = %s"
                         " AND t.relation = 'head' AND t.subject_type = 'person' ORDER BY p.display_name LIMIT 1",
                         (tenant_id, department_id)).fetchone()
    return dict(r) if r else None


def create_clarification(conn, tenant_id, principal: str, department_id: str, question: str, context: dict,
                         submit: bool) -> dict:
    rid = uuid.uuid4()
    rec = suggested_recipient(conn, tenant_id, principal, department_id)
    try:
        with tenant_tx(conn, tenant_id, principal):
            conn.execute("INSERT INTO runtime.clarification_request (tenant_id, request_id, department_id, question,"
                         " context, suggested_recipient, created_by) VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s)",
                         (tenant_id, rid, department_id, question, json.dumps(context or {}),
                          rec["principal_id"] if rec else None, principal))
            conn.execute("INSERT INTO runtime.clarification_event (tenant_id, request_id, state, actor)"
                         " VALUES (%s, %s, %s, %s)", (tenant_id, rid, "submitted" if submit else "draft", principal))
            audit.append(conn, tenant_id, "clarification.created", {"request": str(rid), "department": department_id})
    except psycopg.errors.ForeignKeyViolation:
        raise ProductError("unknown_department", "no such department") from None
    return {"request_id": str(rid), "state": "submitted" if submit else "draft", "suggested_recipient": rec,
            "delivery": "not sent: Veloxa does not contact anyone; share the request with the recipient"}


def clarification_event(conn, tenant_id, principal: str, request_id: str, state: str, note: str | None = None,
                        submission_id: str | None = None) -> str:
    with tenant_tx(conn, tenant_id, principal):
        r = conn.execute("SELECT * FROM runtime.clarification_current WHERE tenant_id = %s AND request_id = %s",
                         (tenant_id, request_id)).fetchone()
        if r is None:
            raise ProductError("not_found", "no such request")
        if state == "answered":
            head = conn.execute("SELECT intake.has_department_role(%s, %s, %s, ARRAY['head', 'reviewer']) AS ok",
                                (tenant_id, principal, r["department_id"])).fetchone()["ok"]
            if not head:
                raise ProductError("not_permitted", "only the department's head or reviewer can answer")
            if submission_id and conn.execute("SELECT 1 FROM intake.submission WHERE tenant_id = %s AND"
                                              " submission_id = %s", (tenant_id, submission_id)).fetchone() is None:
                raise ProductError("unknown_submission", "the referenced submission does not exist")
        elif r["created_by"] != principal:
            raise ProductError("not_permitted", "only the requester can submit or withdraw")
        conn.execute("INSERT INTO runtime.clarification_event (tenant_id, request_id, state, actor, note,"
                     " submission_id)"
                     " VALUES (%s, %s, %s, %s, %s, %s)", (tenant_id, request_id, state, principal, note, submission_id))
        audit.append(conn, tenant_id, f"clarification.{state}", {"request": str(request_id)})
    return state


def list_clarifications(conn, tenant_id, principal: str) -> list[dict]:
    with tenant_tx(conn, tenant_id, principal):
        rows = conn.execute("SELECT r.*, (SELECT json_agg(json_build_object('state', e.state, 'actor', e.actor,"
                            " 'note', e.note, 'submission_id', e.submission_id, 'at', e.at) ORDER BY e.at)"
                            " FROM runtime.clarification_event e WHERE e.tenant_id = r.tenant_id"
                            " AND e.request_id = r.request_id) AS events FROM runtime.clarification_current r"
                            " WHERE r.tenant_id = %s ORDER BY r.created_at DESC", (tenant_id,)).fetchall()
    return [_j(r) for r in rows]
