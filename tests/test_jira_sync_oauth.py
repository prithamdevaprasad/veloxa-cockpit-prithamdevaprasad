"""Jira -> governed data room over the OAuth transport (no API token): submissions stay pending review, unchanged
issues are never re-imported, updates are. Synthetic workspace; mocked Jira (invented keys and values)."""

import uuid

import pytest

from veloxa import connectors, intake, tenancy
from veloxa.db import connect, tenant_tx
from veloxa.storage import LocalContentStore

pytestmark = pytest.mark.db
ADMIN = "person:syn-admin"


class FakeJira:
    """Stands in for jira_oauth.Jira.call (cloud ID, bearer and refresh live there; none of it is needed here)."""

    def __init__(self, issues):
        self.issues, self.calls = issues, []

    def call(self, tenant, method, url, **kw):
        self.calls.append((method, url, kw.get("json", {}).get("jql")))
        jql = kw["json"]["jql"]
        cursor = jql.split('updated >= "')[1].split('"')[0] if "updated >=" in jql else None
        return {"issues": [i for i in self.issues if cursor is None or i["fields"]["updated"] >= cursor]}


def issue(key, updated, status="To Do"):
    return {"id": key.split("-")[1], "key": key, "fields": {
        "summary": f"Synthetic {key}", "status": {"name": status}, "assignee": {"displayName": "Dev One"},
        "created": "2099-01-01T00:00:00Z", "updated": updated, "duedate": None,
        "comment": {"comments": [{"body": "synthetic note"}]}, "secret_custom_field": "never kept"}}


@pytest.fixture
def room(db, tmp_path):
    slug = f"jsync-{uuid.uuid4().hex[:8]}"
    with connect(db.admin_dsn) as a, connect(db.dsn("app")) as app:
        tid = tenancy.create_tenant(a, slug, "Asia/Kolkata", "Sync Co", synthetic=True)
        tenancy.bootstrap_synthetic_admin(a, tid, ADMIN, "Admin")
        tenancy.add_synthetic_member(a, tid, "person:authoriser", "Authoriser")
        tenancy.create_department(app, tid, ADMIN, "engineering", "Engineering")
        tenancy.grant(app, tid, ADMIN, "department", "engineering", "contributor", ADMIN, "syncs Jira")
        tenancy.grant(app, tid, ADMIN, "tenant", str(tid), "admission_authoriser", "person:authoriser", "authorises")
        intake.authorise_source_type(app, tid, "person:authoriser", source_type="jira_sync", title="Jira issues",
                                     department_id="engineering", kind="export", system="jira",
                                     data_class="internal", records="rows", file_glob="*.jsonl", contract=None,
                                     key_columns=["key"], decision_record="SYN-JIRA-1 (synthetic)")
    return {"tenant": tid, "store": LocalContentStore(tmp_path / "originals")}


def run(db, room, fake, mode="incremental"):
    con = connectors.oauth_connector(fake, str(room["tenant"]), ["ENG"])
    with connect(db.dsn("app")) as app:
        return connectors.sync(app, room["store"], room["tenant"], con, actor=ADMIN, department_id="engineering",
                               source_type="jira_sync", mode=mode)


def test_oauth_sync_submits_for_review_skips_unchanged_and_resubmits_updates(db, room):
    fake = FakeJira([issue("ENG-1", "2099-01-02T00:00:00Z"), issue("ENG-2", "2099-01-03T00:00:00Z")])
    first = run(db, room, fake, "initial")
    assert first["status"] == "ok" and first["fetched"] == 2 and first["changed"] == 2 and first["submission_id"]
    again = run(db, room, fake)
    assert again["status"] == "ok" and again["changed"] == 0 and again["submission_id"] is None     # no duplicates
    fake.issues[1] = issue("ENG-2", "2099-01-04T00:00:00Z", "Done")
    upd = run(db, room, fake)
    assert upd["changed"] == 1 and upd["submission_id"]
    with connect(db.admin_dsn) as a, tenant_tx(a, room["tenant"]):
        states = [r["state"] for r in a.execute("SELECT state FROM intake.submission_state").fetchall()]
        admitted = a.execute("SELECT count(*) AS n FROM ledger.source_record WHERE system = 'jira'").fetchone()["n"]
    assert len(states) == 2 and "admitted" not in states and admitted == 0      # pending review, never admitted here
    assert all(c[0] == "POST" and c[1] == "/rest/api/3/search/jql" for c in fake.calls)


def test_only_allowlisted_fields_and_source_ids_are_kept():
    con = connectors.oauth_connector(FakeJira([]), "t", ["ENG"])
    rec = con._shape(issue("ENG-9", "2099-01-02T00:00:00Z"))
    assert rec["key"] == "ENG-9" and rec["id"] == "9" and rec["updated"] == "2099-01-02T00:00:00Z"
    assert {"status", "assignee", "created", "comment"} <= set(rec["fields"])
    assert "secret_custom_field" not in rec["fields"]
