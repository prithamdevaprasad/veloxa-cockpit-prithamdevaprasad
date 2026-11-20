"""Migration 0026: a named single-approver exception. Synthetic tenant only.

Without an exception the two-person rule holds (a submitter cannot review; the authoriser must differ from the
submitter and the reviewer). With an operator-recorded exception for (source type, person), that person may review
and authorise that source type alone; every other source type keeps the rule, and revoking restores it."""

import uuid

import psycopg
import pytest

from veloxa import intake, tenancy
from veloxa.db import connect, tenant_tx
from veloxa.storage import LocalContentStore

pytestmark = pytest.mark.db
ME = "person:syn-owner"


@pytest.fixture
def room(db, tmp_path):
    with connect(db.admin_dsn) as a, connect(db.dsn("app")) as app:
        tid = tenancy.create_tenant(a, f"solo-{uuid.uuid4().hex[:8]}", "Asia/Kolkata", "Solo Co", synthetic=True)
        tenancy.bootstrap_synthetic_admin(a, tid, ME, "Owner")
        tenancy.create_department(app, tid, ME, "engineering", "Engineering")
        for rel in ("contributor", "reviewer"):
            tenancy.grant(app, tid, ME, "department", "engineering", rel, ME, "synthetic setup")
        with tenant_tx(a, tid):
            tenancy.grant_as_operator(a, tid, "tenant", str(tid), "admission_authoriser", ME, "SYN-SOLO-1 synthetic")
        for st in ("mail_feed", "other_feed"):
            intake.authorise_source_type(app, tid, ME, source_type=st, title=st, department_id="engineering",
                                         kind="export", system=st, data_class="internal", records="rows",
                                         file_glob="*.jsonl", contract=None, key_columns=["key"],
                                         decision_record="SYN-SOLO-1 (synthetic)")
    return {"tenant": tid, "store": LocalContentStore(tmp_path / "o")}


def submit(db, room, st):
    with connect(db.dsn("app")) as app:
        return intake.upload(app, room["store"], room["tenant"], ME, "engineering", st, st,
                             [intake.Upload("x.jsonl", b'{"key": "a"}\n')])


def ready(db, room, sid):
    with connect(db.admin_dsn) as a, tenant_tx(a, room["tenant"], "system:intake"):
        a.execute("INSERT INTO intake.submission_event (tenant_id, submission_id, state, actor) VALUES (%s, %s,"
                  " 'awaiting_review', 'system:intake')", (room["tenant"], sid))


def decide(db, room, sid, d):
    with connect(db.dsn("app")) as app, tenant_tx(app, room["tenant"], ME):
        return app.execute("SELECT intake.decide(%s, %s, 'checked by the owner') AS s", (sid, d)).fetchone()["s"]


def exception(db, room, st, active):
    with connect(db.admin_dsn) as a, tenant_tx(a, room["tenant"]):
        a.execute("INSERT INTO intake.single_approver_exception (tenant_id, source_type, principal_id, active,"
                  " decision_record, granted_by) VALUES (%s, %s, %s, %s, 'SYN-SOLO-1 synthetic decision',"
                  " 'operator')", (room["tenant"], st, ME, active))


def test_two_person_rule_holds_without_an_exception(db, room):
    sid = submit(db, room, "mail_feed")
    ready(db, room, sid)
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="submitter cannot review"):
        decide(db, room, sid, "accept")


def test_named_exception_allows_one_person_for_that_source_type_only(db, room):
    exception(db, room, "mail_feed", True)
    sid = submit(db, room, "mail_feed")
    ready(db, room, sid)
    assert decide(db, room, sid, "accept") == "awaiting_authorisation"
    assert decide(db, room, sid, "authorise") == "authorised"
    other = submit(db, room, "other_feed")                       # another source type keeps the rule
    ready(db, room, other)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        decide(db, room, other, "accept")
    exception(db, room, "mail_feed", False)                     # revoked: the rule is back
    again = submit(db, room, "mail_feed")
    ready(db, room, again)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        decide(db, room, again, "accept")


def test_the_application_role_cannot_grant_itself_an_exception(db, room):
    with pytest.raises(psycopg.errors.InsufficientPrivilege), connect(db.dsn("app")) as app, \
            tenant_tx(app, room["tenant"], ME):
        app.execute("INSERT INTO intake.single_approver_exception (tenant_id, source_type, principal_id, active,"
                    " decision_record, granted_by) VALUES (%s, 'mail_feed', %s, true, 'self-granted attempt',"
                    " %s)", (room["tenant"], ME, ME))
