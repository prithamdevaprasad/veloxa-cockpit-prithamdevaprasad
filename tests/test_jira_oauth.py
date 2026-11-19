"""Jira OAuth 2.0 (3LO): flow, token handling, reads, confirmed writes. Mocked transport only: no network, no real
Jira site, no real credentials (every value below is an invented placeholder)."""

import base64
import json
import re
from urllib.parse import parse_qs, urlparse

import pytest

from veloxa import jira_oauth as jo

SECRET = "placeholder-client-secret-xyz"
ACCESS, REFRESH, ACCESS2, REFRESH2 = "acc-token-111", "ref-token-222", "acc-token-333", "ref-token-444"
CLOUD = "11111111-2222-3333-4444-555555555555"
SITE = {"id": CLOUD, "name": "example", "url": "https://example.atlassian.net",
        "scopes": ["read:jira-work", "write:jira-work"]}


class R:
    def __init__(self, status=200, body=None):
        self.status_code, self._b = status, body
        self.content = b"" if body is None else json.dumps(body).encode()

    def json(self):
        if self._b is None:
            raise ValueError("no body")
        return self._b


class Http:
    """Routes (method, url-suffix) to queued responses; records every call."""

    def __init__(self):
        self.calls, self.routes = [], {}

    def on(self, method, suffix, *responses):
        self.routes.setdefault((method, suffix), []).extend(responses)

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        for (m, suffix), q in self.routes.items():
            if m == method and url.endswith(suffix) and q:
                return q.pop(0) if len(q) > 1 else q[0]
        raise AssertionError(f"unexpected {method} {url}")


class Clock:
    t = 1_000_000.0

    def __call__(self):
        return self.t


def make(sites=(SITE,), public="http://localhost:4175"):
    http, clock = Http(), Clock()
    j = jo.Jira(jo.Client("cid-placeholder", SECRET, public), jo.Vault(env={}), http=http, clock=clock)
    http.on("POST", "/oauth/token", R(200, {"access_token": ACCESS, "refresh_token": REFRESH, "expires_in": 3600,
                                            "scope": "read:jira-work write:jira-work offline_access"}))
    http.on("GET", "/accessible-resources", R(200, list(sites)))
    return j, http, clock


def connect(j):
    url = j.authorize_url("tenant-a", "admin-1")
    state = parse_qs(urlparse(url).query)["state"][0]
    return j.callback("auth-code", state)


def test_authorize_url_requests_the_scopes_and_the_configured_callback():
    j, _, _ = make()
    q = parse_qs(urlparse(j.authorize_url("t", "p")).query)
    assert q["redirect_uri"] == ["http://localhost:4175/v1/integrations/jira/callback"]
    assert set(q["scope"][0].split()) == {"read:jira-work", "write:jira-work", "read:jira-user", "offline_access"}
    assert q["audience"] == ["api.atlassian.com"] and q["response_type"] == ["code"] and len(q["state"][0]) >= 40
    assert SECRET not in j.authorize_url("t", "p")


def test_no_callback_url_is_invented_without_a_public_url():
    j, _, _ = make(public=None)
    with pytest.raises(jo.JiraError) as e:
        j.authorize_url("t", "p")
    assert e.value.code == "no_public_url"


def test_state_is_one_time_and_expires():
    j, _, clock = make()
    url = j.authorize_url("t", "p")
    state = parse_qs(urlparse(url).query)["state"][0]
    clock.t += jo.STATE_TTL + 1
    with pytest.raises(jo.JiraError, match="expired"):
        j.callback("code", state)
    with pytest.raises(jo.JiraError):
        j.callback("code", "forged")


def test_cloud_id_comes_from_accessible_resources_and_tokens_never_leave():
    j, http, _ = make()
    assert connect(j) == ("tenant-a", "admin-1")
    s = j.status("tenant-a")
    assert s["connected"] and s["site"]["id"] == CLOUD and not s["needs_site_choice"]
    blob = json.dumps(s) + repr(j.vault.get("tenant-a")) + repr(j.client)
    for secret in (ACCESS, REFRESH, SECRET, "auth-code"):
        assert secret not in blob
    token_call = next(c for c in http.calls if c[1].endswith("/oauth/token"))
    assert token_call[2]["json"]["grant_type"] == "authorization_code"


def test_several_sites_require_a_choice_and_only_known_sites():
    other = {**SITE, "id": "99999999-0000-0000-0000-000000000000", "name": "other"}
    j, _, _ = make(sites=(SITE, other))
    connect(j)
    assert j.status("tenant-a")["needs_site_choice"]
    with pytest.raises(jo.JiraError, match="Choose"):
        j.search("tenant-a", "project = X")
    with pytest.raises(jo.JiraError):
        j.choose_site("tenant-a", "invented-cloud-id")
    assert j.choose_site("tenant-a", other["id"])["site"]["name"] == "other"


def test_expired_access_token_is_refreshed_with_rotation():
    j, http, clock = make()
    connect(j)
    clock.t += 4000
    http.routes[("POST", "/oauth/token")] = [R(200, {"access_token": ACCESS2, "refresh_token": REFRESH2,
                                                     "expires_in": 3600})]
    http.on("POST", "/rest/api/3/search/jql", R(200, {"issues": []}))
    j.search("tenant-a", "project = X")
    g = j.vault.get("tenant-a")
    assert (g.access_token, g.refresh_token) == (ACCESS2, REFRESH2)
    assert http.calls[-1][2]["headers"]["Authorization"] == f"Bearer {ACCESS2}"


def test_revoked_access_shows_a_clear_reconnect_error_without_secrets():
    j, http, _ = make()
    connect(j)
    http.on("POST", "/rest/api/3/search/jql", R(401, {}))
    http.routes[("POST", "/oauth/token")] = [R(400, {"error": "invalid_grant"})]
    with pytest.raises(jo.JiraError) as e:
        j.search("tenant-a", "project = X")
    assert e.value.code == "auth_refused" and "Reconnect" in str(e.value)
    s = j.status("tenant-a")
    assert s["expired"] and "Reconnect" in s["last_error"]
    assert REFRESH not in json.dumps(s)


def test_reads_keep_source_attribution_key_url_and_timestamps():
    j, http, _ = make()
    connect(j)
    issue = {"key": "ENG-7", "fields": {
        "summary": "Fix login", "status": {"name": "In Progress"}, "assignee": {"displayName": "A. Person"},
        "duedate": "2099-01-02", "created": "2099-01-01T00:00:00Z", "updated": "2099-01-01T01:00:00Z",
        "issuetype": {"name": "Task"}, "project": {"key": "ENG"},
        "description": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text",
                                                                                       "text": "Steps"}]}]},
        "comment": {"comments": [{"author": {"displayName": "B"}, "created": "2099-01-01T02:00:00Z",
                                  "body": {"type": "doc", "content": [{"type": "paragraph", "content": [
                                      {"type": "text", "text": "Looks good"}]}]}}]},
        "issuelinks": [{"type": {"name": "Blocks"}, "outwardIssue": {"key": "ENG-8"}}]}}
    http.on("POST", "/rest/api/3/search/jql", R(200, {"issues": [issue]}))
    http.on("GET", "/rest/api/3/issue/ENG-7", R(200, issue))
    [x] = j.search("tenant-a", "project = ENG")
    assert x["url"] == "https://example.atlassian.net/browse/ENG-7" and x["status"] == "In Progress"
    assert x["assignee"] == "A. Person" and x["due_date"] == "2099-01-02" and x["description"] == "Steps"
    assert x["source"]["system"] == "jira" and x["source"]["key"] == "ENG-7" and x["source"]["retrieved_at"]
    d = j.issue("tenant-a", "ENG-7")
    assert d["comments"][0]["body"] == "Looks good" and d["links"] == [{"type": "Blocks", "key": "ENG-8",
                                                                       "direction": "outward"}]
    assert f"/ex/jira/{CLOUD}/" in http.calls[-1][1]


def _meta(http):
    http.on("GET", "/rest/api/3/project/search", R(200, {"values": [{"key": "ENG", "name": "Eng", "id": "1"}]}))
    http.on("GET", "/issue/createmeta/ENG/issuetypes", R(200, {"issueTypes": [{"id": "10001", "name": "Task"}]}))
    http.on("GET", "/issue/createmeta/ENG/issuetypes/10001", R(200, {"fields": [
        {"fieldId": "summary", "name": "Summary", "required": True},
        {"fieldId": "description", "name": "Description", "required": False},
        {"fieldId": "duedate", "name": "Due date", "required": False}]}))


def test_create_is_previewed_against_metadata_and_needs_explicit_confirmation():
    j, http, _ = make()
    connect(j)
    _meta(http)
    with pytest.raises(jo.JiraError, match="not visible"):
        j.preview_create("tenant-a", "admin-1", "INVENTED", "10001", "x")
    with pytest.raises(jo.JiraError, match="issue type"):
        j.preview_create("tenant-a", "admin-1", "ENG", "99999", "x")
    with pytest.raises(jo.JiraError, match="customfield_1"):
        j.preview_create("tenant-a", "admin-1", "ENG", "10001", "x", extra={"customfield_1": "y"})
    p = j.preview_create("tenant-a", "admin-1", "ENG", "10001", "New task", "Body text", {"duedate": "2099-02-01"})
    assert p["requires_confirmation"] and p["project"] == "ENG" and p["issue_type"] == "Task"
    assert p["fields"]["Summary"] == "New task" and p["fields"]["Due date"] == "2099-02-01"
    assert p["description"] == "Body text"
    assert not any(m in ("PUT", "DELETE") or (m == "POST" and u.endswith("/rest/api/3/issue"))
                   for m, u, _ in http.calls)                                    # previewing writes nothing
    with pytest.raises(jo.JiraError, match="confirmation"):
        j.commit("tenant-a", "admin-1", p["preview_id"], False)
    with pytest.raises(jo.JiraError, match="expired or was already used"):
        j.commit("tenant-a", "someone-else", p["preview_id"], True)               # bound to the previewer
    p = j.preview_create("tenant-a", "admin-1", "ENG", "10001", "New task")
    http.on("POST", "/rest/api/3/issue", R(201, {"key": "ENG-9"}))
    assert j.commit("tenant-a", "admin-1", p["preview_id"], True)["key"] == "ENG-9"
    with pytest.raises(jo.JiraError):
        j.commit("tenant-a", "admin-1", p["preview_id"], True)                    # one-time


def test_update_preview_shows_current_and_proposed_and_commits_edit_and_comment():
    j, http, _ = make()
    connect(j)
    http.on("GET", "/rest/api/3/issue/ENG-7/editmeta", R(200, {"fields": {"summary": {"name": "Summary"}}}))
    http.on("GET", "/rest/api/3/issue/ENG-7", R(200, {"key": "ENG-7", "fields": {"summary": "Old"}}))
    with pytest.raises(jo.JiraError, match="assignee"):
        j.preview_update("tenant-a", "admin-1", "ENG-7", {"assignee": {"id": "x"}})
    p = j.preview_update("tenant-a", "admin-1", "ENG-7", {"summary": "New"}, "A note")
    assert p["key"] == "ENG-7" and p["fields"]["Summary"] == {"current": "Old", "proposed": "New"}
    assert p["comment"] == "A note"
    http.on("PUT", "/rest/api/3/issue/ENG-7", R(204))
    http.on("POST", "/rest/api/3/issue/ENG-7/comment", R(201, {"id": "1"}))
    out = j.commit("tenant-a", "admin-1", p["preview_id"], True)
    assert out == {"operation": "update", "key": "ENG-7", "url": "https://example.atlassian.net/browse/ENG-7",
                   "changed": ["summary"], "commented": True}
    assert not any(m == "DELETE" for m, _, _ in http.calls)


def test_no_delete_or_transition_capability_exists():
    names = " ".join(dir(jo.Jira)).lower()
    assert "delete" not in names and "transition" not in names
    from veloxa import api_jira
    src = open(api_jira.__file__, encoding="utf-8").read()
    assert "@app.delete" not in src and "transition" not in src.lower().replace("no transition route", "")


def test_env_file_loader_reads_only_atlassian_keys_and_reports_names(tmp_path):
    f = tmp_path / "local.env"
    f.write_text("ATLASSIAN_CLIENT_ID=abc\nATLASSIAN_CLIENT_SECRET='s3'\nOTHER_SECRET=nope\n# c\n", encoding="utf-8")
    env = {"ATLASSIAN_CLIENT_ID": "kept"}
    assert jo.load_env_file(str(f), env) == ["ATLASSIAN_CLIENT_SECRET"]
    assert env == {"ATLASSIAN_CLIENT_ID": "kept", "ATLASSIAN_CLIENT_SECRET": "s3"}
    assert jo.env_presence(env) == {"ATLASSIAN_CLIENT_ID": True, "ATLASSIAN_CLIENT_SECRET": True,
                                    "VELOXA_PUBLIC_URL": False}


def test_vault_encrypts_tokens_at_rest(tmp_path):
    pytest.importorskip("cryptography")
    key = base64.urlsafe_b64encode(b"k" * 32).decode()
    v = jo.Vault(env={"VELOXA_TOKEN_KEY": key, "VELOXA_TOKEN_DIR": str(tmp_path)})
    v.put("t1", jo.Grant(ACCESS, REFRESH, 1.0, [], [SITE], CLOUD))
    raw = (tmp_path / "jira-t1.bin").read_bytes()
    assert ACCESS.encode() not in raw and REFRESH.encode() not in raw
    again = jo.Vault(env={"VELOXA_TOKEN_KEY": key, "VELOXA_TOKEN_DIR": str(tmp_path)})
    assert again.get("t1").access_token == ACCESS
    assert re.fullmatch(r"jira-t1\.bin", (tmp_path / "jira-t1.bin").name)
