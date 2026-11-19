"""The unified Ask entry point: governed intents before tools; previews before any recorded request."""

import pytest
from conftest import DEV_TOKEN, api_client
from test_workspace_api import _fixture, synth  # noqa: F401  (pytest fixture)

from veloxa import assistant

H = {"Authorization": f"Bearer {DEV_TOKEN}", "X-Veloxa-Request": "1"}


@pytest.mark.parametrize("q,small", [("Hello", True), ("thanks!", True), ("What can you do?", True),
                                     ("Hello, what is our headcount?", False)])
def test_small_talk_is_only_small_talk(q, small):
    assert bool(assistant.SMALL.match(q)) is small


def test_review_requests_and_notifications_are_recognised_with_their_department_and_subject():
    m = assistant.REVIEW.search("Request a People Ops review of the reported resignation of an engineer")
    assert m.group("dept") == "People Ops" and m.group("topic").startswith("the reported resignation")
    m = assistant.REVIEW.search("Notify engineering about the vendor dependency")
    assert m.group("dept2") == "engineering" and m.group("topic2") == "the vendor dependency"
    assert assistant.REVIEW.search("What is the status of the review?") is None


@pytest.mark.db
def test_conversation_requests_and_follow_ups_over_http(db, monkeypatch, synth):  # noqa: F811
    with api_client(db, monkeypatch, **_fixture(synth["slug"], "person:syn-admin")) as c:
        assert c.post("/v1/admin/departments", headers=H, json={"department_id": "people",
                                                                "name": "People Operations"}).status_code == 200
        hi = c.post("/v1/ask", headers=H, json={"question": "Hello"}).json()
        assert hi["status"] == "answered" and hi["evidence"] == [] and "I'm Veloxa" in hi["answer"]
        r = c.post("/v1/ask", headers=H, json={"question": "Request a People Ops review of the reported "
                                                           "resignation of a platform engineer"}).json()
        assert r["status"] == "action_preview" and r["action"]["department"] == "People Operations"
        assert any("unverified report" in n for n in r["action"]["notes"])
        no = c.post("/v1/requests/commit", headers=H, json={"preview_id": r["action"]["preview_id"],
                                                            "confirm": False})
        assert no.status_code == 409
        r = c.post("/v1/ask", headers=H, json={"question": "Request a People Ops review of the reported "
                                                           "resignation of a platform engineer"}).json()
        ok = c.post("/v1/requests/commit", headers=H, json={"preview_id": r["action"]["preview_id"],
                                                            "confirm": True})
        assert ok.status_code == 200 and ok.json()["state"] == "submitted"
        again = c.post("/v1/requests/commit", headers=H, json={"preview_id": r["action"]["preview_id"],
                                                               "confirm": True})
        assert again.status_code == 409                                              # one-time
        sens = c.post("/v1/ask", headers=H, json={"question": "Request a People Ops review of why she resigned "
                                                              "and her medical leave"}).json()
        assert sens["status"] == "refused"
        t = c.post("/v1/threads", headers=H, json={"title": "t"}).json()["thread_id"]
        c.post(f"/v1/threads/{t}/ask", headers=H, json={"question": "What is our headcount?"})
        more = c.post(f"/v1/threads/{t}/ask", headers=H, json={"question": "Tell me more"}).json()["answer"]
        assert more["answer"].startswith("More on “What is our headcount?”")
