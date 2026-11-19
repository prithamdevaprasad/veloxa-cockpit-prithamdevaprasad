"""One source-object registry across routes (migration 0025). Synthetic tenant, invented content.

The brief's cases: the same document uploaded twice; the same document uploaded and discovered by a connector; the
same e-mail from Gmail and as an uploaded .eml; the same Jira issue from polling and a webhook-triggered fetch; the
same commit received repeatedly; a modified version; similar content from different sources. Nothing is merged, each
registration keeps its own route, URL and access scope, and access is never widened."""

import uuid

import pytest

from veloxa import connectors, identity, intake, tenancy
from veloxa.db import connect, tenant_tx
from veloxa.storage import LocalContentStore

pytestmark = pytest.mark.db
ADMIN = "person:syn-admin"
BODY = ("The synthetic release review moved the beta date because the payment adapter tests were failing on the "
        "staging cluster and the owner asked for one more week of soak time before the public launch.")
EML = (b"Message-ID: <abc-123@mail.example.test>\r\nFrom: Ann <ann@example.test>\r\nTo: bo@example.test\r\n"
       b"Subject: Beta date\r\nDate: Mon, 01 Jan 2099 10:00:00 +0000\r\nContent-Type: text/plain\r\n\r\n"
       + BODY.encode())


@pytest.fixture
def room(db, tmp_path):
    slug = f"ident-{uuid.uuid4().hex[:8]}"
    with connect(db.admin_dsn) as a, connect(db.dsn("app")) as app:
        tid = tenancy.create_tenant(a, slug, "Asia/Kolkata", "Ident Co", synthetic=True)
        tenancy.bootstrap_synthetic_admin(a, tid, ADMIN, "Admin")
        tenancy.add_synthetic_member(a, tid, "person:authoriser", "Authoriser")
        tenancy.create_department(app, tid, ADMIN, "engineering", "Engineering")
        tenancy.grant(app, tid, ADMIN, "department", "engineering", "contributor", ADMIN, "uploads")
        tenancy.grant(app, tid, ADMIN, "tenant", str(tid), "admission_authoriser", "person:authoriser", "authorises")
        for st, glob in (("docs", "*.txt"), ("mail", "*.eml"), ("feed", "*.jsonl")):
            intake.authorise_source_type(app, tid, "person:authoriser", source_type=st, title=st,
                                         department_id="engineering", kind="document" if st != "feed" else "export",
                                         system=st, data_class="internal",
                                         records="document" if st != "feed" else "rows", file_glob=glob,
                                         contract=None, key_columns=[] if st != "feed" else ["key"],
                                         decision_record="SYN-IDENT-1 (synthetic)")
    return {"tenant": tid, "store": LocalContentStore(tmp_path / "o")}


def up(db, room, name, data, st="docs"):
    with connect(db.dsn("app")) as app:
        return intake.upload(app, room["store"], room["tenant"], ADMIN, "engineering", st, name,
                             [intake.Upload(name, data)])


def sub(db, room, name, records, mode="incremental"):
    with connect(db.dsn("app")) as app:
        key, ver = {"gmail": ("message_id", "history_id"), "github": ("source_id", "version"),
                    "jira": ("key", "updated")}[name]
        return connectors.submit(app, room["store"], room["tenant"], name, records, actor=ADMIN,
                                 department_id="engineering", source_type="feed", mode=mode, scope={},
                                 cursor_before=None, cursor_after=None, key=key, version=ver)


def rows(db, room):
    with connect(db.admin_dsn) as a, tenant_tx(a, room["tenant"]):
        return a.execute("SELECT route, object_key, classification, related_keys, access_scope, submission_id"
                         " FROM runtime.source_object WHERE tenant_id = %s ORDER BY at", (room["tenant"],)).fetchall()


def test_same_document_uploaded_twice_is_flagged_and_both_submissions_are_kept(db, room):
    s1, s2 = up(db, room, "a.txt", BODY.encode()), up(db, room, "b.txt", BODY.encode())
    r = rows(db, room)
    assert [x["classification"] for x in r] == ["new", "duplicate"] and s1 != s2
    assert {x["access_scope"]["file"] for x in r} == {"a.txt", "b.txt"}            # each keeps its own provenance


def test_same_email_from_gmail_and_as_uploaded_eml_is_one_object(db, room):
    g = {"message_id": "g1", "history_id": "9", "rfc822_message_id": "<ABC-123@mail.example.test>",
         "subject": "Beta date", "from": "Ann <ann@example.test>", "date": "Mon, 01 Jan 2099 10:00:00 +0000",
         "snippet": BODY[:80], "locator": "gmail:box/messages/g1", "access_scope": {"mailbox": "box"}}
    first = sub(db, room, "gmail", [g])
    assert first["changed"] == 1
    up(db, room, "beta.eml", EML, "mail")                                           # the same e-mail, uploaded
    r = rows(db, room)
    assert [(x["route"], x["object_key"], x["classification"]) for x in r] == [
        ("gmail", "email:abc-123@mail.example.test", "new"), ("upload", "email:abc-123@mail.example.test", "duplicate")]
    assert r[0]["access_scope"] == {"mailbox": "box"} and r[1]["access_scope"] == {"file": "beta.eml"}
    # and a Gmail re-delivery (another sync) of the same message is not submitted again
    g2 = dict(g, history_id="10")
    assert sub(db, room, "gmail", [g2])["changed"] == 0


def test_jira_polling_and_webhook_fetch_and_modified_versions(db, room):
    i = {"key": "ENG-7", "id": "7", "updated": "2099-01-02T00:00:00Z", "fields": {"summary": "x", "status": "To Do"}}
    assert sub(db, room, "jira", [i])["changed"] == 1                              # polling
    assert sub(db, room, "jira", [i], mode="initial")["changed"] == 0              # webhook-triggered refetch
    mod = dict(i, updated="2099-01-03T00:00:00Z", fields={"summary": "x", "status": "Done"})
    out = sub(db, room, "jira", [mod])
    assert out["changed"] == 1 and out["identity"]["new_version"] == 1             # a modified version is new


def test_same_commit_received_repeatedly(db, room):
    c = {"source_id": "github:o/r:commit:" + "a" * 40, "version": "a" * 40, "data": {"message": "fix"},
         "url": "u", "permission_scope": {"repository_id": 1}}
    assert sub(db, room, "github", [c])["changed"] == 1
    assert sub(db, room, "github", [c], mode="initial")["changed"] == 0


def test_document_from_upload_and_connector_and_similar_content_are_linked_not_merged(db, room):
    up(db, room, "note.txt", BODY.encode())
    c = {"source_id": "github:o/r:issue:5", "version": "v1", "data": BODY, "url": "u2",
         "permission_scope": {"repository_id": 2}}
    sub(db, room, "github", [c])
    similar = BODY.replace("public launch", "general launch")
    up(db, room, "variant.txt", similar.encode())
    r = rows(db, room)
    by = {x["object_key"]: x for x in r}
    gh = by["github:o/r:issue:5"]
    assert gh["classification"] == "same_content_other_identity"                  # same text, other source: linked
    assert gh["related_keys"] and gh["related_keys"][0].startswith("file:")
    assert gh["access_scope"] == {"repository_id": 2}                              # its own scope, not widened
    var = [x for x in r if x["access_scope"].get("file") == "variant.txt"][0]
    assert var["classification"] == "review_candidate" and var["related_keys"]     # a person decides; nothing merged
    assert len(r) == 3                                                              # every identity kept


def test_identity_is_pure_and_route_independent():
    eml = identity.from_upload("x.eml", EML, "0" * 64)
    assert eml.object_key == identity.message_id_key("<abc-123@MAIL.example.test>")
    assert identity.fingerprint("A  b,c") == identity.fingerprint("a b c")
    assert identity.jaccard(identity.shingles(BODY), identity.shingles(BODY)) == 1.0
    assert identity.jaccard(identity.shingles("too short"), identity.shingles("too short")) == 0.0
