"""Connected-source answers: GitHub, Jira and Gmail rows admitted through the data room are answered in plain
sentences with citations, and e-mail is only ever visible to the people on the message."""

import base64
import json
import uuid

import pytest
from conftest import DEV_TOKEN

from veloxa import chunking, gmail_oauth, intake, tenancy
from veloxa.ask import connectors
from veloxa.db import connect
from veloxa.storage import LocalContentStore

H = {"Authorization": f"Bearer {DEV_TOKEN}"}
ADMIN = "person:syn-admin"


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


# ------------------------------------------------------------------ unit: message text and e-mail indexing
def test_message_text_prefers_plain_text_strips_html_and_never_reads_attachments():
    payload = {"mimeType": "multipart/mixed", "parts": [
        {"mimeType": "multipart/alternative", "parts": [
            {"mimeType": "text/plain", "body": {"data": _b64("Hello Casey,\r\n\r\nGA stays at 15 December.")}},
            {"mimeType": "text/html", "body": {"data": _b64("<p>Hello <b>Casey</b></p>")}}]},
        {"mimeType": "text/plain", "filename": "secret.txt", "body": {"data": _b64("ATTACHMENT TEXT")}}]}
    text = gmail_oauth.message_text(payload)
    assert text == "Hello Casey,\n\nGA stays at 15 December." and "ATTACHMENT" not in text
    html_only = {"mimeType": "text/html", "body": {"data": _b64(
        "<style>x{}</style><p>Quote valid until 4 December.</p><div>Thanks &amp; regards</div>")}}
    assert gmail_oauth.message_text(html_only) == "Quote valid until 4 December.\n Thanks & regards"
    long = {"mimeType": "text/plain", "body": {"data": _b64("x" * 50_000)}}
    assert len(gmail_oauth.message_text(long)) == gmail_oauth.BODY_LIMIT


def test_gmail_rows_index_as_private_messages_with_their_parties():
    title, when, parties, units = chunking.gmail_units({
        "subject": "Runway update", "date": "Tue, 10 Nov 2026 10:10:00 +0530",
        "from": "Jordan <jordan@example.test>", "to": "CEO <CEO@example.test>", "cc": None,
        "body": "Runway is 13 months.\n\nBurn is up.", "snippet": "Runway is 13"})
    assert title == "Runway update" and when.year == 2026
    assert parties == ["ceo@example.test", "jordan@example.test"]
    assert [u[2] for u in units] == ["Runway is 13 months.", "Burn is up."] and units[0][0] == "message"


def test_connector_questions_are_recognised_without_catching_ordinary_ceo_questions():
    for q in ("What changed recently in the GitHub repository?", "What pull requests are open?",
              "Which Jira tickets are overdue?", "What did the email from Jordan say?", "Are the CI builds failing?"):
        assert connectors.asks(q), q
    for q in ("What did we commit to the board?", "What has changed recently?", "What is the biggest risk?",
              "Which issues could cause the most damage?", "Give me an executive summary of the company."):
        assert not connectors.asks(q), q


# ------------------------------------------------------------------ end to end through the data room


GITHUB_ROWS = [
    {"source": "github", "kind": "repository", "repository": "acme/atlas",
     "source_id": "github:acme/atlas:repository:1",
     "version": "1", "actor": "dev1", "created_at": "2026-10-01T00:00:00Z", "updated_at": "2026-11-18T00:00:00Z",
     "data": {"default_branch": "main"}},
    {"source": "github", "kind": "pull_request", "repository": "acme/atlas",
     "source_id": "github:acme/atlas:pull_request:7",
     "version": "2026-11-18T00:00:00Z", "actor": "dev1", "created_at": "2026-11-17T00:00:00Z",
     "updated_at": "2026-11-18T00:00:00Z",
     "data": {"number": 7, "title": "Cache route queries", "state": "open", "merged_at": None}},
    {"source": "github", "kind": "commit", "repository": "acme/atlas", "source_id": "github:acme/atlas:commit:abc",
     "version": "abc", "actor": "dev2", "created_at": "2026-11-16T09:00:00Z", "updated_at": "2026-11-16T09:00:00Z",
     "data": {"sha": "abc", "message": "Raise connection pool to 200\n\nFixes the outage"}},
    {"source": "github", "kind": "workflow_run", "repository": "acme/atlas",
     "source_id": "github:acme/atlas:workflow_run:9", "version": "9", "actor": "dev2",
     "created_at": "2026-11-16T09:05:00Z", "updated_at": "2026-11-16T09:10:00Z",
     "data": {"name": "CI", "status": "completed", "conclusion": "failure", "head_branch": "main"}},
]
JIRA_ROWS = [
    {"Key": "ATL-1", "Summary": "Load test at 3x peak", "Status": "In Progress", "Assignee": "Taylor",
     "Due date": "2026-11-27", "Project": "ATL", "Issue type": "Task", "Created": "2026-11-01"},
    {"Key": "ATL-2", "Summary": "Security review sign-off", "Status": "Done", "Project": "ATL",
     "Issue type": "Task", "Created": "2026-11-01"},
]
GMAIL_ROWS = [
    {"source": "gmail", "message_id": "m1", "thread_id": "t1", "from": "Jordan Sample <jordan@example.test>",
     "to": "Casey Example <ceo@example.test>", "cc": None, "subject": "CloudCore reserved-instance renewal",
     "date": "Mon, 16 Nov 2026 15:30:00 +0530", "labels": ["Veloxa-Test"], "snippet": "My recommendation",
     "body": "Casey,\n\nMy recommendation is the 1-year CloudCore option. The quote expires on "
             "4 December.\n\nJordan", "locator": "gmail:ceo@example.test/messages/m1",
     "evidence_kind": "communication (not verified fact)", "internal_date": "1000"},
    {"source": "gmail", "message_id": "m2", "thread_id": "t2", "from": "Riley Lead <riley@example.test>",
     "to": "Casey Example <ceo@example.test>", "cc": None, "subject": "Atlas weekly update",
     "date": "Mon, 2 Nov 2026 09:15:00 +0530", "labels": ["Veloxa-Test"], "snippet": "Weekly update",
     "body": "Casey,\n\nOpen risk: the routing-api latency regression is not fixed. The p95 is 840 ms against "
             "our 500 ms target.\n\nRiley", "locator": "gmail:ceo@example.test/messages/m2",
     "evidence_kind": "communication (not verified fact)", "internal_date": "2000"},
    {"source": "gmail", "message_id": "m3", "thread_id": "t3", "from": "Riley Lead <riley@example.test>",
     "to": "Casey Example <ceo@example.test>", "cc": None, "subject": "routing-api performance checkpoint",
     "date": "Fri, 20 Nov 2026 11:00:00 +0530", "labels": ["Veloxa-Test"], "snippet": "Checkpoint",
     "body": "Casey,\n\nThe routing-api p95 latency is now 610 ms, still above the 500 ms target. The caching change "
             "lands on 24 November.\n\nRiley", "locator": "gmail:ceo@example.test/messages/m3",
     "evidence_kind": "communication (not verified fact)", "internal_date": "3000"},
    {"source": "gmail", "message_id": "m4", "thread_id": "t4", "from": "Kim Buyer <kim@northwind.example.test>",
     "to": "Casey Example <ceo@example.test>", "cc": None, "subject": "Northwind after the outage",
     "date": "Tue, 17 Nov 2026 13:10:00 +0530", "labels": ["Veloxa-Test"], "snippet": "Northwind",
     "body": "Dear Casey,\n\nTo proceed with our renewal on 31 December, we need a 99.9% monthly uptime commitment "
             "and a confirmed date for multi-depot routing. Can you confirm by 24 November?\n\nKim",
     "locator": "gmail:ceo@example.test/messages/m4",
     "evidence_kind": "communication (not verified fact)", "internal_date": "2500"},
]


@pytest.fixture
def ws(db, tmp_path):
    slug = f"ca-{uuid.uuid4().hex[:8]}"
    with connect(db.admin_dsn) as a, connect(db.dsn("app")) as app:
        tid = tenancy.create_tenant(a, slug, "UTC", "Connector Co", synthetic=True)
        tenancy.bootstrap_synthetic_admin(a, tid, ADMIN, "Admin")
        tenancy.add_synthetic_member(a, tid, "person:ceo", "CEO", "ceo@example.test")
        tenancy.add_synthetic_member(a, tid, "person:other", "Other", "other@example.test")
        for p in ("reviewer", "authoriser"):
            tenancy.add_synthetic_member(a, tid, f"person:{p}", p)
        tenancy.create_department(app, tid, ADMIN, "engineering", "Engineering")
        g = lambda *x: tenancy.grant(app, tid, ADMIN, *x)  # noqa: E731
        g("department", "engineering", "contributor", "person:syn-admin", "x")
        g("department", "engineering", "reviewer", "person:reviewer", "x")
        g("tenant", str(tid), "admission_authoriser", "person:authoriser", "x")
        g("tenant", str(tid), "company_reader", "person:ceo", "x")
        g("tenant", str(tid), "company_reader", "person:other", "x")
    return {"slug": slug, "tenant": tid, "store": LocalContentStore(tmp_path / "originals"),
            "ingest": db.dsn("ingest"), "projection": db.dsn("projection")}


def _client(db, monkeypatch, ws, who):
    from fastapi.testclient import TestClient

    from veloxa.api import PRIVILEGED_ENV, create_app

    for n in PRIVILEGED_ENV + ("VELOXA_OIDC",):
        monkeypatch.delenv(n, raising=False)
    for k, v in {"VELOXA_DEV_AUTH": "1", "VELOXA_DEV_TOKEN": DEV_TOKEN, "VELOXA_DEV_PRINCIPAL": who,
                 "VELOXA_DEV_TENANT": ws["slug"]}.items():
        monkeypatch.setenv(k, v)
    return TestClient(create_app(dsn=db.dsn("app"), store_factory=lambda: ws["store"]))


def _work(ws):
    with connect(ws["ingest"]) as ing, connect(ws["projection"]) as prj:
        return intake.work_once(ing, prj, ws["store"])


def _admit(db, monkeypatch, ws, source_type, system, data_class, key, rows):
    with _client(db, monkeypatch, ws, "person:authoriser") as c:
        assert c.post("/v1/admin/source-types", headers=H, json={
            "source_type": source_type, "title": source_type, "department_id": "engineering", "kind": "export",
            "system": system, "data_class": data_class, "records": "rows", "file_glob": "*.jsonl",
            "key_columns": [key], "decision_record": "SYN-CONN-1 (synthetic)"}).status_code == 200
    body = "\n".join(json.dumps(r) for r in rows).encode()
    with _client(db, monkeypatch, ws, ADMIN) as c:
        r = c.post("/v1/submissions", headers=H, json={
            "department_id": "engineering", "source_type": source_type, "title": source_type,
            "files": [{"file_name": f"{source_type}.jsonl", "content_base64": base64.b64encode(body).decode()}]})
        assert r.status_code == 200, r.text
        sid = r.json()["submission_id"]
    assert _work(ws)["validated"] == 1
    with _client(db, monkeypatch, ws, "person:reviewer") as c:
        assert c.post(f"/v1/submissions/{sid}/decision", headers=H,
                      json={"decision": "accept", "reason": "checked"}).status_code == 200
    with _client(db, monkeypatch, ws, "person:authoriser") as c:
        assert c.post(f"/v1/submissions/{sid}/decision", headers=H,
                      json={"decision": "authorise", "reason": "approved"}).json()["state"] == "authorised"
    assert _work(ws)["admitted"] == 1


@pytest.mark.db
def test_connected_sources_answer_in_sentences_and_mail_stays_with_its_parties(db, monkeypatch, ws):
    _admit(db, monkeypatch, ws, "eng_github", "github_app", "internal", "source_id", GITHUB_ROWS)
    _admit(db, monkeypatch, ws, "eng_jira", "jira_oauth", "internal", "Key", JIRA_ROWS)
    _admit(db, monkeypatch, ws, "comms_gmail", "gmail_oauth", "confidential", "message_id", GMAIL_ROWS)

    def ask(c, q):
        return c.post("/v1/ask", headers=H, json={"question": q}).json()

    with _client(db, monkeypatch, ws, "person:ceo") as c:
        a = ask(c, "What pull requests are open in GitHub?")
        assert "#7" in a["answer"] and "Cache route queries" in a["answer"] and "⟦" not in a["answer"]
        assert a["evidence"] and all(e["system"] == "github_app" for e in a["evidence"])
        a = ask(c, "What changed recently in the GitHub repository?")
        assert "Raise connection pool to 200" in a["answer"] and "ARR" not in a["answer"]
        a = ask(c, "Are the GitHub CI builds failing?")
        assert "1 failure" in a["answer"] and a["evidence"]
        a = ask(c, "Which Jira tickets are open?")
        assert "ATL-1" in a["answer"] and "ATL-2" not in a["answer"] and "Load test at 3x peak" in a["answer"]
        a = ask(c, "What did the email from Jordan say about the CloudCore renewal?")
        assert "1-year CloudCore option" in a["answer"], a["answer"]
        assert "Jordan Sample, 16 November 2026" in a["answer"] and "⟦" not in a["answer"]
        assert {e["system"] for e in a["evidence"]} == {"gmail_oauth"}
        rid = a["evidence"][0]["record_id"]
        from urllib.parse import quote

        assert c.get(f"/v1/evidence/{quote(rid, safe='')}", headers=H).status_code == 200
        # No "email" in the question: something specific that only the mail discusses is still answered from it
        a = ask(c, "What does Northwind need before it will renew?")
        assert "99.9% monthly uptime" in a["answer"] and "multi-depot" in a["answer"], a["answer"]
        a = ask(c, "What is the routing-api p95 latency now?")              # newest message first
        assert a["answer"].index("610 ms") < a["answer"].find("840 ms") or "840" not in a["answer"], a["answer"]
        a = ask(c, "Which decisions need my approval?")                    # asks in the CEO's own mail are listed
        assert "Asked of you in your e-mail" in a["answer"] and "Northwind after the outage" in a["answer"], a["answer"]
        a = ask(c, "What is the biggest risk to the company?")             # a general question stays a briefing
        assert all(e.get("system") != "gmail_oauth" for e in a.get("evidence") or [])
    with _client(db, monkeypatch, ws, "person:other") as c:                 # not on the message
        a = ask(c, "What did the email from Jordan say about the CloudCore renewal?")
        assert "1-year CloudCore" not in a["answer"]
        assert all(e.get("system") != "gmail_oauth" for e in a.get("evidence") or [])
        assert c.get(f"/v1/evidence/{quote(rid, safe='')}", headers=H).status_code == 404
        a = ask(c, "What does Northwind need before it will renew?")
        assert "99.9%" not in a["answer"]
        a = ask(c, "Which decisions need my approval?")
        assert "Asked of you" not in a["answer"] and "Northwind" not in a["answer"]


def test_email_quotes_whole_sentences_from_hard_wrapped_mail():
    body = ("Casey,\n\nWe need a decision from you by Friday on whether we cut the\nmulti-depot feature. Northwind "
            "(our second-largest account, Rs. several units a year) told me\nthis morning.\n\nMy decisions:\n\n"
            "1. Multi-depot is **out of GA**. GA stays at 15 December 2026.\n2. The credit is approved.\n\nFrom: X")
    units = connectors._sentences(body)
    assert "Northwind (our second-largest account, Rs. several units a year) told me this morning." in units
    assert "Multi-depot is out of GA." in units and "My decisions:" in units and not any("From:" in u for u in units)
    assert connectors._terms("What does Northwind need before it will renew?") == ["northwind", "renew"]
    assert connectors._hit("worst-case", {"worst", "case"}, "the worst case is small")


def test_a_lead_in_ask_brings_the_items_that_follow(monkeypatch):
    msg = {"record_id": "r1", "payload": {"subject": "Weekly summary", "date": "Fri, 20 Nov 2026 18:30:00 +0530",
           "internal_date": "1", "body": "Hi,\n\nStill waiting on you:\n1. The renewal quote.\n2. The launch date.\n"}}
    monkeypatch.setattr(connectors, "_messages", lambda router: [msg])
    (text, rid, _), = connectors.pending_asks(object())
    assert rid == "r1" and text.endswith("Still waiting on you: The renewal quote.; The launch date.")
