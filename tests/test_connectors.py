"""Jira connector with a MOCKED transport (no credentials, no network)."""

import hashlib
import hmac
import json

import httpx
import pytest

from veloxa import connectors as c

ENV = {"VELOXA_JIRA_BASE_URL": "https://example.test", "VELOXA_JIRA_EMAIL": "bot@example.test",
       "VELOXA_JIRA_API_TOKEN": "secret-token-value", "VELOXA_JIRA_PROJECTS": "SYN",
       "VELOXA_JIRA_FIELDS": "summary,status,assignee", "VELOXA_JIRA_WEBHOOK_SECRET": "hook-secret"}


def issue(key, updated, **extra):
    return {"key": key, "fields": {"summary": f"s {key}", "status": {"name": "Open"}, "assignee": None,
                                   "updated": updated, "customfield_99": "uninterpreted", **extra}}


def connector(handler, sleeps=None):
    http = httpx.Client(base_url="https://example.test", transport=httpx.MockTransport(handler))
    return c.JiraConnector(c.JiraConfig.from_env(ENV), http=http, sleep=(sleeps.append if sleeps is not None
                                                                          else lambda s: None))


def test_disabled_without_every_setting_and_status_never_shows_the_token():
    assert c.jira_status({})["state"] == "disabled"
    st = c.jira_status(ENV)
    assert st["projects"] == ["SYN"] and "secret-token-value" not in json.dumps(st)
    assert "secret-token-value" not in repr(c.JiraConfig.from_env(ENV))


def test_initial_sync_paginates_keeps_only_allowlisted_fields_and_advances_the_cursor():
    pages = {None: {"issues": [issue("SYN-1", "2099-01-01T10:00")], "nextPageToken": "p2"},
             "p2": {"issues": [issue("SYN-2", "2099-01-02T10:00")]}}
    seen_jql = []

    def handler(req):
        body = json.loads(req.content)
        seen_jql.append(body["jql"])
        return httpx.Response(200, json=pages[body.get("nextPageToken")])

    recs, cursor = connector(handler).fetch(None)
    assert [r["key"] for r in recs] == ["SYN-1", "SYN-2"] and cursor == "2099-01-02T10:00"
    assert all("customfield_99" not in r["fields"] for r in recs)            # never interpreted (K-09)
    assert 'project in ("SYN")' in seen_jql[0] and "updated >=" not in seen_jql[0]


def test_incremental_sync_uses_the_cursor_and_idempotency_skips_unchanged_records():
    def handler(req):
        assert 'updated >= "2099-01-02T10:00"' in json.loads(req.content)["jql"]
        return httpx.Response(200, json={"issues": [issue("SYN-2", "2099-01-02T10:00"),
                                                    issue("SYN-3", "2099-01-03T10:00")]})

    recs, _ = connector(handler).fetch("2099-01-02T10:00")
    assert [r["key"] for r in c.changed(recs, {"SYN-2": "2099-01-02T10:00"})] == ["SYN-3"]


def test_rate_limits_are_retried_with_retry_after_then_fail_closed():
    calls, sleeps = {"n": 0}, []

    def handler(req):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json={"issues": []})

    assert connector(handler, sleeps).fetch(None) == ([], None) and sleeps == [2.0, 2.0]
    with pytest.raises(c.ConnectorFailed):
        connector(lambda r: httpx.Response(429, headers={"Retry-After": "1"})).fetch(None)
    with pytest.raises(c.ConnectorFailed):
        connector(lambda r: httpx.Response(401)).fetch(None)


def test_a_webhook_is_only_a_verified_signal_and_scope_is_enforced():
    conn = connector(lambda r: httpx.Response(200, json=issue("SYN-9", "2099-02-01T00:00")))
    body = b'{"issue": {"key": "SYN-9"}}'
    good = "sha256=" + hmac.new(b"hook-secret", body, hashlib.sha256).hexdigest()
    assert conn.verify_webhook(body, good) and not conn.verify_webhook(body, "sha256=bad")
    assert conn.fetch_one("SYN-9")["key"] == "SYN-9"                          # the authoritative record is fetched
    with pytest.raises(c.ConnectorFailed):
        conn.fetch_one("OTHER-1")


def test_exports_are_deterministic_jsonl():
    assert c.to_jsonl([{"key": "A", "updated": "x"}]) == b'{"key": "A", "updated": "x"}\n'
