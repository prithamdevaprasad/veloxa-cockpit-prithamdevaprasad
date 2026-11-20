"""GitHub App connector with a mocked GitHub: narrowed read-only tokens, renewal, allowlist, pagination, evidence
semantics, webhook signature and replay protection. No network, no private key."""

import hashlib
import hmac

import pytest

from veloxa import github_app as ga

REPO = {"id": 7, "full_name": "o/r", "private": False, "default_branch": "main", "html_url": "u"}


class R:
    def __init__(self, status, body, headers=None):
        self.status_code, self._b, self.headers = status, body, headers or {}

    def json(self):
        return self._b


class FakeGitHub:
    def __init__(self, perms="read"):
        self.token_bodies, self.perms, self.n_tokens = [], perms, 0

    def request(self, method, url, headers=None, json=None):
        assert method in ("GET", "POST")
        if url.endswith("/access_tokens"):
            self.n_tokens += 1
            self.token_bodies.append(json)
            return R(201, {"token": f"T{self.n_tokens}", "expires_at": "2030-01-01T01:00:00Z",
                           "permissions": {k: self.perms for k in json["permissions"]}})
        assert method == "GET"
        if "/installation/repositories" in url:
            return R(200, {"total_count": 1, "repositories": [REPO]})
        if url.endswith("/repos/o/r"):
            return R(200, {"html_url": "u", "pushed_at": "2030-01-01T00:00:00Z", "default_branch": "main",
                           "owner": {"login": "o"}})
        if "/commits?" in url and "page=2" not in url:
            return R(200, [{"sha": "a" * 40, "commit": {"message": "fix", "author": {"date": "2030-01-01T00:00:00Z"},
                                                        "committer": {"date": "2030-01-01T00:00:00Z"}}}],
                     {"link": f'<{url}&page=2>; rel="next"'})
        if "/commits?" in url:
            later = {"date": "2030-01-02T00:00:00Z"}
            return R(200, [{"sha": "b" * 40, "commit": {"message": "x", "committer": later}}])
        if "/issues?" in url:
            return R(200, [{"number": 1, "title": "i", "updated_at": "2030-01-01T00:00:00Z"},
                           {"number": 2, "pull_request": {}, "updated_at": "2030-01-01T00:00:00Z"}])
        if "/actions/runs" in url:
            return R(200, {"workflow_runs": [{"id": 9, "conclusion": "success", "updated_at": "2030-01-01T00:00:00Z"}]})
        if url.endswith("/check-runs?per_page=100"):
            return R(200, {"check_runs": []})
        if url.endswith("/status"):
            return R(200, {"statuses": [], "sha": "a" * 40})
        return R(200, [])


def gh(fake=None, clock=None, secret=None):
    c = ga.Config("1", "2", "unused", secret)
    return ga.GitHub(c, fake or FakeGitHub(), clock or (lambda: 1_800_000_000), store_dir="", signer=lambda m: b"s")


def test_nothing_is_read_before_an_explicit_allowlist():
    g = gh()
    with pytest.raises(ga.GitHubError, match="Select at least one repository"):
        g.fetch("t", None)
    with pytest.raises(ga.GitHubError, match="can see"):
        g.set_allowlist("t", [999])


def test_tokens_are_narrowed_to_repos_and_read_permissions_and_renewed():
    fake = FakeGitHub()
    now = [1_800_000_000]
    g = gh(fake, lambda: now[0])
    g.set_allowlist("t", [7])
    g.fetch("t", None)
    body = [b for b in fake.token_bodies if "repository_ids" in b][0]
    assert body["repository_ids"] == [7] and set(body["permissions"].values()) == {"read"}
    assert set(body["permissions"]) == set(ga.READ_PERMISSIONS)
    n = fake.n_tokens
    g.token((7,))
    assert fake.n_tokens == n                                              # cached
    now[0] = 1_893_459_600 - 200                                           # within 5 min of expiry: renewed
    g.token((7,))
    assert fake.n_tokens == n + 1


def test_a_token_with_write_access_is_refused():
    g = gh(FakeGitHub(perms="write"))
    with pytest.raises(ga.GitHubError) as e:
        g.token((7,))
    assert e.value.code == "token_not_read_only"


def test_fetch_paginates_keeps_provenance_and_evidence_semantics():
    g = gh()
    g.set_allowlist("t", [7])
    recs, info = g.fetch("t", None)
    commits = [r for r in recs if r["kind"] == "commit"]
    assert len(commits) == 2                                               # followed rel="next"
    assert all(r["permission_scope"]["permissions"] == "read" and r["source_id"].startswith("github:o/r:")
               for r in recs)
    assert [r["data"]["number"] for r in recs if r["kind"] == "issue"] == [1]   # PRs not double-counted as issues
    assert "not proof of deployment" in [r for r in recs if r["kind"] == "workflow_run"][0]["data"]["note"]
    assert "does not prove" in commits[0]["data"]["note"]
    assert info["cursor"] == "2030-01-02T00:00:00Z"


def test_webhook_signature_and_replay_protection():
    g = gh(secret="s3cret")
    body = b'{"repository":{"id":7}}'
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert g.verify_webhook(body, sig, "d1") is True
    assert g.verify_webhook(body, sig, "d1") is False                       # replay
    assert g.verify_webhook(body, "sha256=00", "d2") is False               # forged
    assert gh().verify_webhook(body, sig, "d3") is False                    # no secret configured: refuse
