"""Saved threads, contextual Ask, agent updates from persisted sessions, and department clarifications
(migration 0020). SYNTHETIC D1 and a synthetic `syn-` agent only."""

import uuid

import psycopg
import pytest
from agent_fixtures import grant_source, syn_agent
from test_agent_runtime import ask as agent_ask
from test_agent_runtime import env, publish, runtime  # noqa: F401  (fixture re-export)

from veloxa import product, tenancy, tools
from veloxa.db import connect, tenant_tx

pytestmark = pytest.mark.db
IST = "Asia/Kolkata"
ALPHA = tools.Principal("person:T001", "executive", "alpha@example.test")
BETA = tools.Principal("person:T002", "authenticated", "beta@example.test")


def _now():
    from veloxa.releases import current_time

    return current_time()


def test_a_thread_is_the_persisted_sequence_of_recorded_answers_and_is_private(db, app_conn, env):  # noqa: F811
    tenant, _ = env
    tid = product.create_thread(app_conn, tenant, ALPHA.principal_id, "Headcount review")
    r1 = product.ask_in_thread(app_conn, tenant, ALPHA, IST, tid, "What is our headcount?", _now())
    r2 = product.ask_in_thread(app_conn, tenant, ALPHA, IST, tid, "What is the ledger spend?", _now())
    assert (r1["seq"], r2["seq"]) == (1, 2)
    t = product.get_thread(app_conn, tenant, ALPHA.principal_id, tid)
    assert [x["question"] for x in t["turns"]] == ["What is our headcount?", "What is the ledger spend?"]
    assert t["turns"][0]["answer"]["audit_id"] == r1["answer"]["audit_id"]
    product.thread_event(app_conn, tenant, ALPHA.principal_id, tid, "renamed", "People and spend")
    assert product.list_threads(app_conn, tenant, ALPHA.principal_id)[0]["title"] == "People and spend"
    assert product.get_thread(app_conn, tenant, BETA.principal_id, tid) is None          # owner only (RLS)
    with pytest.raises(product.ProductError):
        product.ask_in_thread(app_conn, tenant, BETA, IST, tid, "What is our headcount?", _now())
    product.thread_event(app_conn, tenant, ALPHA.principal_id, tid, "archived")
    assert product.list_threads(app_conn, tenant, ALPHA.principal_id) == []
    assert product.list_threads(app_conn, tenant, ALPHA.principal_id, archived=True)[0]["thread_id"] == tid


def test_another_principal_cannot_attach_someone_elses_answer(db, app_conn, env):  # noqa: F811
    tenant, _ = env
    mine = product.create_thread(app_conn, tenant, BETA.principal_id, "Mine")
    theirs = product.ask_in_thread(app_conn, tenant, ALPHA, IST,
                                   product.create_thread(app_conn, tenant, ALPHA.principal_id, "A"),
                                   "What is our headcount?", _now())
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with tenant_tx(app_conn, tenant, BETA.principal_id):
            app_conn.execute("INSERT INTO runtime.thread_answer (tenant_id, thread_id, seq, answer_id)"
                             " VALUES (%s, %s, 1, %s)", (tenant, mine, theirs["answer"]["audit_id"]))


def test_follow_ups_are_asked_about_the_thread_subject_visibly():
    assert product.contextual_question("Why did it change?", {"subject": "Project Falcon"}) == \
        "Why did it change (regarding Project Falcon)?"
    assert product.contextual_question("What is Project Falcon?", {"subject": "Project Falcon"}) == \
        "What is Project Falcon?"
    assert product.contextual_question("Why did it change?", {}) == "Why did it change?"


def test_an_update_summarises_a_real_session_and_is_marked_synthetic(db, app_conn, env):  # noqa: F811
    tenant, basis = env
    cfg = publish(db, tenant, syn_agent("people_ops"))
    grant_source(db, tenant, "keka_hrms")
    rt = runtime(app_conn)
    sid = rt.open_session(tenant, ALPHA, cfg.agent_id, basis)["session_id"]
    with pytest.raises(product.ProductError, match="no answered turn"):
        product.publish_update(app_conn, tenant, ALPHA.principal_id, sid)
    turn = agent_ask(rt, tenant, sid, "What is our headcount?")
    assert turn["status"] in ("answered", "qualified")
    u = product.publish_update(app_conn, tenant, ALPHA.principal_id, sid, project="Synthetic org")
    assert u["synthetic"] is True and u["evidence"] and u["session_id"] == str(sid)
    assert u["summary"] == " ".join(c["text"] for c in turn["claims"][:2])
    assert product.list_updates(app_conn, tenant, ALPHA.principal_id)[0]["update_id"] == u["update_id"]
    assert product.list_updates(app_conn, tenant, BETA.principal_id) == []               # not their session
    with pytest.raises(product.ProductError, match="no such session"):                   # RLS: not even visible
        product.publish_update(app_conn, tenant, BETA.principal_id, sid)


def test_clarifications_suggest_the_department_head_and_only_the_department_answers(db, app_conn, env):  # noqa: F811
    tenant, _ = env
    with connect(db.admin_dsn) as a, tenant_tx(a, tenant, "operator"):
        a.execute("INSERT INTO directory.department (tenant_id, department_id, name, created_by)"
                  " VALUES (%s, 'testing', 'Testing', 'operator') ON CONFLICT DO NOTHING", (tenant,))
        a.execute("INSERT INTO directory.principal (tenant_id, principal_id, display_name) VALUES (%s, 'person:T002',"
                  " 'Synthetic head') ON CONFLICT DO NOTHING", (tenant,))
        a.execute("INSERT INTO directory.principal_event (tenant_id, principal_id, event, actor)"
                  " VALUES (%s, 'person:T002', 'activated', 'operator')", (tenant,))
        tenancy.grant_as_operator(a, tenant, "department", "testing", "head", "person:T002", "synthetic head")
    req = product.create_clarification(app_conn, tenant, ALPHA.principal_id, "testing",
                                       "Can Testing clarify why the milestone changed?", {"kind": "update"}, True)
    assert req["state"] == "submitted" and req["suggested_recipient"]["principal_id"] == "person:T002"
    assert req["delivery"].startswith("not sent")
    with pytest.raises(product.ProductError, match="head or reviewer"):
        product.clarification_event(app_conn, tenant, ALPHA.principal_id, req["request_id"], "answered", "x")
    with pytest.raises(product.ProductError, match="submission"):
        product.clarification_event(app_conn, tenant, "person:T002", req["request_id"], "answered", "see upload",
                                    str(uuid.uuid4()))
    assert product.clarification_event(app_conn, tenant, "person:T002", req["request_id"], "answered",
                                       "Answered in the weekly update") == "answered"
    seen = product.list_clarifications(app_conn, tenant, "person:T002")
    assert seen and seen[0]["state"] == "answered"
    with pytest.raises(product.ProductError):
        product.create_clarification(app_conn, tenant, ALPHA.principal_id, "nope", "x?", {}, False)
