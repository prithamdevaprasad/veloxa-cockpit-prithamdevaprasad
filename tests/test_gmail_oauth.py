"""Gmail read-only connector with a mocked Google: no network, no real mailbox.

Covers: the exact read-only scope and PKCE; one-time state; refusing a broader grant; no read before a scope is
chosen; bounded paginated initial import; approved-sender filtering; history-based incremental sync; recovery from an
expired history cursor; preview never moves the cursor; refresh, refused refresh -> visible reconnect state;
disconnect revokes at Google; per-tenant token isolation."""

from urllib.parse import parse_qsl, urlsplit

import pytest

from veloxa import gmail_oauth as go
from veloxa.jira_oauth import Vault


class R:
    def __init__(self, status, body=None):
        self.status_code, self._b, self.headers = status, body or {}, {}

    def json(self):
        return self._b


class FakeGoogle:
    def __init__(self, n=7):
        self.calls, self.revoked, self.refresh_ok, self.history_expired = [], [], True, False
        self.msgs = {f"m{i}": {"id": f"m{i}", "threadId": f"t{i}", "historyId": str(100 + i), "labelIds": ["INBOX"],
                               "snippet": f"body {i}", "payload": {"headers": [
                                   {"name": "From", "value": f"Ann <ann@{'ok' if i % 2 == 0 else 'other'}.com>"},
                                   {"name": "Subject", "value": f"Subject {i}"}]}} for i in range(n)}
        self.added = []

    def request(self, method, url, **kw):
        self.calls.append((method, url))
        p = dict(kw.get("params") or {}) if not isinstance(kw.get("params"), list) else dict(kw["params"])
        if url == go.TOKEN_URL:
            d = kw["data"]
            if d["grant_type"] == "refresh_token" and not self.refresh_ok:
                return R(400, {"error": "invalid_grant"})
            return R(200, {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600,
                           "scope": self.scope if hasattr(self, "scope") else go.SCOPE})
        if url == go.REVOKE_URL:
            self.revoked.append(kw["data"]["token"])
            return R(200)
        path = url.removeprefix(go.API)
        if path == "/profile":
            return R(200, {"emailAddress": "box@example.test", "historyId": "500"})
        if path == "/messages":
            ids = sorted(self.msgs)
            start = int(p.get("pageToken", 0))
            size = int(p["maxResults"])
            page = ids[start:start + min(size, 3)]
            nxt = start + len(page)
            return R(200, {"messages": [{"id": i} for i in page]} | ({"nextPageToken": str(nxt)} if nxt < len(ids)
                                                                     else {}))
        if path.startswith("/messages/"):
            return R(200, self.msgs[path.rsplit("/", 1)[1]])
        if path == "/history":
            if self.history_expired:
                return R(404)
            return R(200, {"history": [{"messagesAdded": [{"message": {"id": i}}]} for i in self.added],
                           "historyId": "600"})
        if path == "/labels":
            return R(200, {"labels": [{"id": "INBOX", "name": "INBOX", "type": "system"}]})
        return R(404)


@pytest.fixture
def gm():
    fake = FakeGoogle()
    clock = [1000.0]
    g = go.Gmail(go.Client("cid", "secret", "http://127.0.0.1:4175"), Vault(env={}, prefix="gmail",
                                                                          grant_type=go.Grant), fake, lambda: clock[0])
    return g, fake, clock


def _connect(g, tenant="t1"):
    q = dict(parse_qsl(urlsplit(g.authorize_url(tenant, "person:A")).query))
    return g.callback("code", q["state"]), q


def test_authorize_url_is_read_only_with_pkce_and_exact_callback(gm):
    g, _, _ = gm
    (_, _), q = _connect(g)
    assert q["scope"] == "https://www.googleapis.com/auth/gmail.readonly"
    assert q["redirect_uri"] == "http://127.0.0.1:4175/v1/integrations/gmail/callback"
    assert q["code_challenge_method"] == "S256" and q["access_type"] == "offline"
    assert g.status("t1")["mailbox"] == "box@example.test"
    assert "secret" not in repr(g.client) and "AT" not in str(g.status("t1"))


def test_state_is_one_time(gm):
    g, _, _ = gm
    q = dict(parse_qsl(urlsplit(g.authorize_url("t1", "p")).query))
    g.callback("c", q["state"])
    with pytest.raises(go.GmailError, match="expired or was already used"):
        g.callback("c", q["state"])


def test_broader_grant_is_refused_and_revoked(gm):
    g, fake, _ = gm
    fake.scope = go.SCOPE + " https://www.googleapis.com/auth/gmail.modify"
    with pytest.raises(go.GmailError) as e:
        _connect(g)
    assert e.value.code == "wrong_scope" and fake.revoked and g.vault.get("t1") is None


def test_nothing_is_read_before_a_scope_and_scope_is_validated(gm):
    g, _, _ = gm
    _connect(g)
    with pytest.raises(go.GmailError, match="Choose the mailbox scope"):
        g.fetch("t1")
    with pytest.raises(go.GmailError, match="whole mailbox"):
        g.set_scope("t1", {"labels": [], "senders": []})
    with pytest.raises(go.GmailError):
        g.set_scope("t1", {"senders": ["ok.com"], "limit": 10_000})


def test_bounded_initial_import_paginates_filters_senders_and_preview_keeps_cursor(gm):
    g, fake, _ = gm
    _connect(g)
    g.set_scope("t1", {"senders": ["ok.com"], "limit": 5})
    recs, info = g.fetch("t1", advance=False)
    assert info["listed"] == 5 and info["pages"] == 2                      # bounded and paginated
    assert {r["message_id"] for r in recs} == {"m0", "m2", "m4"}           # approved senders only
    assert g.vault.get("t1").history_id is None                            # preview did not advance
    r = recs[0]
    assert r["locator"].startswith("gmail:box@example.test/messages/") and r["thread_id"]
    assert r["evidence_kind"].startswith("communication")
    _, info = g.fetch("t1")
    assert g.vault.get("t1").history_id == "500"


def test_incremental_history_sync_and_expired_cursor_recovery(gm):
    g, fake, _ = gm
    _connect(g)
    g.set_scope("t1", {"senders": ["ok.com"], "limit": 7})
    g.fetch("t1")
    fake.added = ["m6", "m1"]
    recs, info = g.fetch("t1")
    assert info["mode"] == "incremental" and [r["message_id"] for r in recs] == ["m6"]
    fake.history_expired = True
    recs, info = g.fetch("t1")
    assert info.get("recovered_from_expired_cursor") and info["mode"] == "initial"


def test_refused_refresh_shows_reconnect_and_disconnect_revokes(gm):
    g, fake, clock = gm
    _connect(g)
    g.set_scope("t1", {"senders": ["ok.com"]})
    clock[0] += 7200                                                         # token expired: refreshed silently
    g.fetch("t1")
    clock[0] += 7200
    fake.refresh_ok = False                                                  # e.g. the 7-day testing-app expiry
    with pytest.raises(go.GmailError) as e:
        g.fetch("t1")
    assert e.value.code == "needs_reconnect" and g.status("t1")["needs_reconnect"] is True
    out = g.disconnect("t1")
    assert out["revoked_at_google"] and fake.revoked == ["RT"] and g.status("t1")["connected"] is False


def test_tokens_are_isolated_per_tenant(gm):
    g, _, _ = gm
    _connect(g, "t1")
    assert g.status("t2")["connected"] is False
    with pytest.raises(go.GmailError, match="not connected"):
        g.fetch("t2")
