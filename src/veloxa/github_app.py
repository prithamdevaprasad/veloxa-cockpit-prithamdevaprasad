"""GitHub through a GitHub App installation: read-only repository activity into the governed data room.

Security model:
- Configuration from the environment only: `VELOXA_GITHUB_APP_ID`, `VELOXA_GITHUB_INSTALLATION_ID`,
  `VELOXA_GITHUB_APP_PRIVATE_KEY_PATH` (a PEM outside the repository; read by the server, never returned or logged),
  optional `VELOXA_GITHUB_WEBHOOK_SECRET`. Missing any required value = disabled, never half-enabled.
- App JWT (RS256, 9 minutes) -> installation access token (1 hour), renewed automatically 5 minutes before expiry.
- Every token is REQUESTED NARROWED: only the administrator-allowlisted repository IDs and only the read permissions
  in `READ_PERMISSIONS`, whatever the installation itself was granted. Nothing is read before a repository is
  explicitly allowlisted. There is no write call in this module.
- Records keep the source ID, URL, actor, timestamps, SHA/version and the permission scope they were read under.
- A webhook is only a SIGNAL (HMAC-SHA256 verified, delivery ID replay-protected) to fetch the authoritative object;
  its payload is never evidence. No public webhook URL is registered by Veloxa.
Evidence semantics (K-claims): a commit is not proof a Jira issue is done; a green check run is not a deployment."""

from __future__ import annotations

import base64
import calendar
import hashlib
import hmac
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

API = "https://api.github.com"
READ_PERMISSIONS = {"metadata": "read", "contents": "read", "pull_requests": "read", "issues": "read",
                    "checks": "read", "statuses": "read", "actions": "read", "deployments": "read"}
REQUIRED = ("VELOXA_GITHUB_APP_ID", "VELOXA_GITHUB_INSTALLATION_ID", "VELOXA_GITHUB_APP_PRIVATE_KEY_PATH")
RENEW_BEFORE = 300
MAX_PAGES = 5                                   # bounded: at most 5 x 100 objects per kind per repository per run


class GitHubError(Exception):
    def __init__(self, message: str, status: int = 502, code: str = "github_error"):
        super().__init__(message)
        self.status, self.code = status, code


@dataclass(frozen=True)
class Config:
    app_id: str
    installation_id: str
    key_path: str = field(repr=False)
    webhook_secret: str | None = field(default=None, repr=False)

    @staticmethod
    def from_env(env: dict | None = None) -> Config | None:
        e = os.environ if env is None else env
        if any(not str(e.get(k, "")).strip() for k in REQUIRED):
            return None
        return Config(e["VELOXA_GITHUB_APP_ID"].strip(), e["VELOXA_GITHUB_INSTALLATION_ID"].strip(),
                      e["VELOXA_GITHUB_APP_PRIVATE_KEY_PATH"].strip(), e.get("VELOXA_GITHUB_WEBHOOK_SECRET") or None)


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class GitHub:
    def __init__(self, config: Config, http=None, clock=time.time, store_dir: str | None = None, signer=None):
        self.config, self.clock, self._http, self._signer = config, clock, http, signer
        self._tok: dict[tuple, tuple[str, float]] = {}
        self._lock = threading.Lock()
        self._seen_deliveries: dict[str, float] = {}
        d = store_dir if store_dir is not None else os.environ.get("VELOXA_TOKEN_DIR")
        self._dir = Path(d) if d else None
        self._allow: dict[str, list[int]] = {}

    @property
    def http(self):
        if self._http is None:
            import httpx

            self._http = httpx.Client(timeout=30.0)
        return self._http

    # ------------------------------------------------------------------ auth
    def _jwt(self) -> str:
        now = int(self.clock())
        head = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        body = _b64(json.dumps({"iat": now - 60, "exp": now + 540, "iss": self.config.app_id}).encode())
        msg = f"{head}.{body}".encode()
        if self._signer:
            return f"{head}.{body}.{_b64(self._signer(msg))}"
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        try:
            key = serialization.load_pem_private_key(Path(self.config.key_path).read_bytes(), password=None)
        except (OSError, ValueError):
            raise GitHubError("The GitHub App private key could not be read.", 409, "key_unreadable") from None
        return f"{head}.{body}.{_b64(key.sign(msg, padding.PKCS1v15(), hashes.SHA256()))}"

    def _req(self, method: str, path: str, auth: str, **kw):
        h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "Authorization": auth}
        for attempt in range(4):
            r = self.http.request(method, path if path.startswith("http") else API + path, headers=h, **kw)
            if r.status_code in (429, 502, 503) or (r.status_code == 403 and
                                                     r.headers.get("x-ratelimit-remaining") == "0"):
                if attempt < 3:
                    time.sleep(min(float(r.headers.get("retry-after", 2 ** attempt)), 30))
                    continue
            if r.status_code >= 400:
                raise GitHubError(f"GitHub returned HTTP {r.status_code} for {path.split('?')[0]}.",
                                  r.status_code if r.status_code < 500 else 502)
            return r
        raise GitHubError("GitHub kept rate-limiting the request.", 429, "rate_limited")

    def app_info(self) -> dict:
        a = self._req("GET", "/app", f"Bearer {self._jwt()}").json()
        i = self._req("GET", f"/app/installations/{self.config.installation_id}", f"Bearer {self._jwt()}").json()
        return {"app_slug": a.get("slug"), "app_name": a.get("name"), "installation_id": i.get("id"),
                "account": (i.get("account") or {}).get("login"), "repository_selection": i.get("repository_selection"),
                "installation_permissions": i.get("permissions", {}),
                "excess_permissions": sorted(k for k, v in (i.get("permissions") or {}).items()
                                             if READ_PERMISSIONS.get(k) != v)}

    def token(self, repo_ids: tuple[int, ...] | None) -> str:
        """An installation token narrowed to `repo_ids` and READ_PERMISSIONS; cached and renewed before expiry.
        `None` = metadata-only token for listing what the installation can see."""
        key = repo_ids or ()
        with self._lock:
            hit = self._tok.get(key)
            if hit and hit[1] - RENEW_BEFORE > self.clock():
                return hit[0]
        body = {"permissions": READ_PERMISSIONS if repo_ids else {"metadata": "read"}}
        if repo_ids:
            body["repository_ids"] = list(repo_ids)
        t = self._req("POST", f"/app/installations/{self.config.installation_id}/access_tokens",
                      f"Bearer {self._jwt()}", json=body).json()
        granted = t.get("permissions") or {}
        if any(v != "read" for v in granted.values()):
            raise GitHubError("GitHub issued a token with more than read access; refusing to use it.", 409,
                              "token_not_read_only")
        exp = calendar.timegm(time.strptime(t["expires_at"], "%Y-%m-%dT%H:%M:%SZ"))
        with self._lock:
            self._tok[key] = (t["token"], exp)
        return t["token"]

    # ------------------------------------------------------------------ allowlist
    def _allow_path(self, tenant: str) -> Path | None:
        return self._dir / f"github-allow-{tenant}.json" if self._dir else None

    def allowlist(self, tenant: str) -> list[int]:
        if tenant not in self._allow and (p := self._allow_path(tenant)) and p.is_file():
            self._allow[tenant] = json.loads(p.read_text(encoding="utf-8"))
        return list(self._allow.get(tenant, []))

    def set_allowlist(self, tenant: str, repo_ids: list[int]) -> list[dict]:
        visible = {r["id"]: r for r in self.repositories()}
        unknown = [i for i in repo_ids if i not in visible]
        if unknown:
            raise GitHubError("Only repositories this installation can see may be allowlisted.", 422, "unknown_repo")
        self._allow[tenant] = sorted(set(repo_ids))
        if p := self._allow_path(tenant):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(self._allow[tenant]), encoding="utf-8")
        return [visible[i] for i in self._allow[tenant]]

    def repositories(self) -> list[dict]:
        tok, out, page = self.token(None), [], 1
        while True:
            r = self._req("GET", f"/installation/repositories?per_page=100&page={page}", f"token {tok}").json()
            out += [{"id": x["id"], "full_name": x["full_name"], "private": x["private"],
                     "default_branch": x.get("default_branch"), "url": x.get("html_url")} for x in r["repositories"]]
            if len(out) >= r.get("total_count", 0) or not r["repositories"]:
                return out
            page += 1

    # ------------------------------------------------------------------ fetch
    def _pages(self, path: str, tok: str, key: str | None = None) -> tuple[list[dict], int]:
        out, url, n = [], path + ("&" if "?" in path else "?") + "per_page=100", 0
        while url and n < MAX_PAGES:
            r = self._req("GET", url, f"token {tok}")
            body = r.json()
            out += body[key] if key else body
            n += 1
            url = None
            for part in r.headers.get("link", "").split(","):
                if 'rel="next"' in part:
                    url = part[part.index("<") + 1:part.index(">")]
        return out, n

    def fetch(self, tenant: str, since: str | None) -> tuple[list[dict], dict]:
        ids = tuple(self.allowlist(tenant))
        if not ids:
            raise GitHubError("Select at least one repository first; nothing is read before that.", 409,
                              "no_repository")
        tok = self.token(ids)
        recs, info = [], {"repositories": [], "pages": 0, "since": since}
        repos = {r["id"]: r for r in self.repositories()}
        for rid in ids:
            repo = repos.get(rid)
            if not repo:
                continue
            full, scope = repo["full_name"], {"installation": self.config.installation_id, "repository_id": rid,
                                              "permissions": "read"}
            meta = self._req("GET", f"/repos/{full}", f"token {tok}").json()
            recs.append(_rec("repository", full, str(rid), meta.get("pushed_at"), meta.get("html_url"),
                             (meta.get("owner") or {}).get("login"), meta.get("created_at"), meta.get("pushed_at"),
                             {"default_branch": meta.get("default_branch"), "visibility": meta.get("visibility"),
                              "description": meta.get("description")}, scope))
            s = f"&since={since}" if since else ""
            kinds = {
                "branch": (f"/repos/{full}/branches", None), "commit": (f"/repos/{full}/commits?x=1{s}", None),
                "pull_request": (f"/repos/{full}/pulls?state=all&sort=updated&direction=desc", None),
                "issue": (f"/repos/{full}/issues?state=all{s}", None),
                "release": (f"/repos/{full}/releases", None), "deployment": (f"/repos/{full}/deployments", None),
                "workflow_run": (f"/repos/{full}/actions/runs?x=1" + (f"&created=>={since}" if since else ""),
                                 "workflow_runs")}
            for kind, (path, key) in kinds.items():
                items, n = self._pages(path, tok, key)
                info["pages"] += n
                for x in items:
                    r = _shape(kind, full, x, scope)
                    if r and (not since or kind in ("branch",) or (r["updated_at"] or "") >= since):
                        recs.append(r)
            head = meta.get("default_branch")
            if head:
                cr = self._req("GET", f"/repos/{full}/commits/{head}/check-runs?per_page=100", f"token {tok}").json()
                st = self._req("GET", f"/repos/{full}/commits/{head}/status", f"token {tok}").json()
                for c in cr.get("check_runs", []):
                    recs.append(_rec("check_run", full, str(c["id"]), c.get("completed_at") or c.get("started_at"),
                                     c.get("html_url"), (c.get("app") or {}).get("slug"), c.get("started_at"),
                                     c.get("completed_at"), {"name": c.get("name"), "head_sha": c.get("head_sha"),
                                                             "status": c.get("status"),
                                                             "conclusion": c.get("conclusion"),
                                                             "note": "a check result, not a deployment"}, scope))
                for c in st.get("statuses", []):
                    recs.append(_rec("commit_status", full, str(c["id"]), c.get("updated_at"), c.get("target_url"),
                                     (c.get("creator") or {}).get("login"), c.get("created_at"), c.get("updated_at"),
                                     {"context": c.get("context"), "state": c.get("state"), "sha": st.get("sha")},
                                     scope))
            info["repositories"].append(full)
        info["fetched"] = len(recs)
        info["cursor"] = max((r["updated_at"] for r in recs if r["updated_at"]), default=since)
        return recs, info

    # ------------------------------------------------------------------ webhooks (signal only)
    def verify_webhook(self, body: bytes, signature: str | None, delivery: str | None) -> bool:
        if not self.config.webhook_secret or not signature or not delivery:
            return False
        want = "sha256=" + hmac.new(self.config.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(want, signature):
            return False
        now = self.clock()
        with self._lock:
            self._seen_deliveries = {k: v for k, v in self._seen_deliveries.items() if v > now - 86400}
            if delivery in self._seen_deliveries:
                return False                                              # replay
            self._seen_deliveries[delivery] = now
        return True


def _rec(kind, repo, sid, version, url, actor, created, updated, data, scope) -> dict:
    return {"source": "github", "kind": kind, "repository": repo, "source_id": f"github:{repo}:{kind}:{sid}",
            "version": str(version or ""), "url": url, "actor": actor, "created_at": created, "updated_at": updated,
            "data": data, "permission_scope": scope}


def _shape(kind: str, repo: str, x: dict, scope: dict) -> dict | None:
    login = lambda u: (u or {}).get("login")  # noqa: E731
    if kind == "branch":
        return _rec(kind, repo, x["name"], x["commit"]["sha"], None, None, None, None,
                    {"name": x["name"], "sha": x["commit"]["sha"], "protected": x.get("protected")}, scope)
    if kind == "commit":
        c = x.get("commit", {})
        return _rec(kind, repo, x["sha"], x["sha"], x.get("html_url"), login(x.get("author")) or
                    (c.get("author") or {}).get("name"), (c.get("author") or {}).get("date"),
                    (c.get("committer") or {}).get("date"),
                    {"sha": x["sha"], "message": (c.get("message") or "")[:2000],
                     "parents": [p["sha"] for p in x.get("parents", [])],
                     "note": "a commit does not prove that any issue is complete"}, scope)
    if kind == "pull_request":
        return _rec(kind, repo, str(x["number"]), x.get("updated_at"), x.get("html_url"), login(x.get("user")),
                    x.get("created_at"), x.get("updated_at"),
                    {"number": x["number"], "title": x.get("title"), "state": x.get("state"),
                     "merged_at": x.get("merged_at"), "base": (x.get("base") or {}).get("ref"),
                     "head_sha": (x.get("head") or {}).get("sha"),
                     "reviewers": [login(u) for u in x.get("requested_reviewers", [])]}, scope)
    if kind == "issue":
        if "pull_request" in x:
            return None                                       # the issues API also lists PRs; they come from /pulls
        return _rec(kind, repo, str(x["number"]), x.get("updated_at"), x.get("html_url"), login(x.get("user")),
                    x.get("created_at"), x.get("updated_at"),
                    {"number": x["number"], "title": x.get("title"), "state": x.get("state"),
                     "assignees": [login(u) for u in x.get("assignees", [])],
                     "labels": [lb.get("name") for lb in x.get("labels", [])]}, scope)
    if kind == "release":
        return _rec(kind, repo, str(x["id"]), x.get("published_at") or x.get("created_at"), x.get("html_url"),
                    login(x.get("author")), x.get("created_at"), x.get("published_at"),
                    {"tag": x.get("tag_name"), "name": x.get("name"), "draft": x.get("draft"),
                     "prerelease": x.get("prerelease")}, scope)
    if kind == "deployment":
        return _rec(kind, repo, str(x["id"]), x.get("updated_at"), x.get("url"), login(x.get("creator")),
                    x.get("created_at"), x.get("updated_at"),
                    {"sha": x.get("sha"), "environment": x.get("environment"), "ref": x.get("ref"),
                     "note": "a deployment record; its status is a separate object"}, scope)
    if kind == "workflow_run":
        return _rec(kind, repo, str(x["id"]), x.get("updated_at"), x.get("html_url"), login(x.get("actor")),
                    x.get("created_at"), x.get("updated_at"),
                    {"name": x.get("name"), "event": x.get("event"), "status": x.get("status"),
                     "conclusion": x.get("conclusion"), "head_sha": x.get("head_sha"),
                     "head_branch": x.get("head_branch"), "note": "a CI run is not proof of deployment"}, scope)
    return None
