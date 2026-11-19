"""Jira Cloud through Atlassian OAuth 2.0 (3LO): connect, read, and confirmed create/update. Never delete.

Security model (backend-for-frontend):
- The client ID and secret come only from the environment (`ATLASSIAN_CLIENT_ID`, `ATLASSIAN_CLIENT_SECRET`). A
  local env file outside the repository may be named with `VELOXA_ENV_FILE`; only `ATLASSIAN_*` keys are read from it
  and existing environment values win. No path is hard-coded.
- The callback URL is `VELOXA_PUBLIC_URL` + `CALLBACK_PATH`. Without `VELOXA_PUBLIC_URL` connecting is refused: no
  URL is invented.
- `state` is 256-bit, one-time and expires in 10 minutes; it binds the callback to the tenant and the administrator
  who started it (the callback is a top-level browser redirect and carries no Authorization header).
- Tokens live only on the server, in the `Vault`: in process memory, or AES-GCM encrypted at rest when
  `VELOXA_TOKEN_KEY` (32 bytes, urlsafe base64) and `VELOXA_TOKEN_DIR` are both set. They are never returned by
  any endpoint, never logged and never in an error message (`repr=False`, `redacted()`).
- The cloud ID always comes from the accessible-resources API; with several sites the administrator chooses one.
- Writes are two-step: `preview_*` validates against Jira's own create/edit metadata (nothing is invented: unknown
  projects, issue types or fields are refused) and stores a one-time preview; only `commit(preview_id, confirm=True)`
  writes. There is no delete and no workflow transition anywhere in this module.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode

AUTH_URL = "https://auth.atlassian.com/authorize"
TOKEN_URL = "https://auth.atlassian.com/oauth/token"  # noqa: S105  (a URL, not a secret)
RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"
API_BASE = "https://api.atlassian.com/ex/jira/{cloud_id}"
CALLBACK_PATH = "/v1/integrations/jira/callback"
SCOPES = ("read:jira-work", "write:jira-work", "read:jira-user", "offline_access")
STATE_TTL = 10 * 60
PREVIEW_TTL = 10 * 60
ISSUE_FIELDS = ("summary", "status", "assignee", "duedate", "created", "updated", "issuetype", "project",
                "description", "priority")
EXPIRED = "Jira access has expired or was revoked. Reconnect Jira in Administration > Integrations."


class JiraError(Exception):
    """A safe, user-facing error: never contains a token, secret or authorization code."""

    def __init__(self, message: str, status: int = 502, code: str = "jira_error"):
        super().__init__(message)
        self.status, self.code = status, code


def load_env_file(path: str | None = None, env: dict | None = None) -> list[str]:
    """Load ATLASSIAN_* keys from a local env file (outside git). Returns the key NAMES loaded, never values."""
    env = os.environ if env is None else env
    path = path or env.get("VELOXA_ENV_FILE")
    if not path or not Path(path).is_file():
        return []
    loaded = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        if k.startswith("ATLASSIAN_") and not env.get(k):
            env[k] = v.strip().strip('"').strip("'")
            loaded.append(k)
    return loaded


@dataclass(frozen=True)
class Client:
    client_id: str
    client_secret: str = field(repr=False)
    public_url: str | None

    @staticmethod
    def from_env(env: dict | None = None) -> Client | None:
        env = os.environ if env is None else env
        cid, sec = env.get("ATLASSIAN_CLIENT_ID", "").strip(), env.get("ATLASSIAN_CLIENT_SECRET", "").strip()
        if not cid or not sec:
            return None
        return Client(cid, sec, env.get("VELOXA_PUBLIC_URL", "").strip().rstrip("/") or None)

    @property
    def redirect_uri(self) -> str | None:
        return f"{self.public_url}{CALLBACK_PATH}" if self.public_url else None


def env_presence(env: dict | None = None) -> dict:
    env = os.environ if env is None else env
    return {k: bool(env.get(k, "").strip()) for k in ("ATLASSIAN_CLIENT_ID", "ATLASSIAN_CLIENT_SECRET",
                                                      "VELOXA_PUBLIC_URL")}


@dataclass
class Grant:
    access_token: str = field(repr=False)
    refresh_token: str | None = field(repr=False)
    expires_at: float
    scopes: list[str]
    sites: list[dict]                       # [{id, name, url, scopes}] from accessible-resources (no tokens)
    cloud_id: str | None = None
    connected_by: str | None = None
    connected_at: float = 0.0
    last_sync: float | None = None
    last_error: str | None = None
    revoked: bool = False

    def redacted(self) -> dict:
        site = next((s for s in self.sites if s["id"] == self.cloud_id), None)
        return {"connected": not self.revoked, "expired": self.revoked, "site": site, "sites": self.sites,
                "needs_site_choice": self.cloud_id is None and len(self.sites) > 1, "scopes": self.scopes,
                "connected_by": self.connected_by, "connected_at": _iso(self.connected_at),
                "last_sync": _iso(self.last_sync), "last_error": self.last_error}


def _iso(t: float | None) -> str | None:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)) if t else None


class Vault:
    """Per-tenant grants. Memory only, or AES-GCM encrypted files when a key and a directory are configured."""

    def __init__(self, env: dict | None = None):
        env = os.environ if env is None else env
        self._lock = threading.Lock()
        self._mem: dict[str, Grant] = {}
        self._dir = Path(env["VELOXA_TOKEN_DIR"]) if env.get("VELOXA_TOKEN_DIR") else None
        key = env.get("VELOXA_TOKEN_KEY")
        self._key = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4)) if key else None
        if self._key is not None and len(self._key) != 32:
            raise ValueError("VELOXA_TOKEN_KEY must be 32 bytes (urlsafe base64)")

    @property
    def persistent(self) -> bool:
        return bool(self._dir and self._key)

    def _path(self, tenant: str) -> Path:
        return self._dir / f"jira-{tenant}.bin"            # type: ignore[operator]

    def get(self, tenant: str) -> Grant | None:
        with self._lock:
            if tenant in self._mem or not self.persistent or not self._path(tenant).is_file():
                return self._mem.get(tenant)
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM

            raw = self._path(tenant).read_bytes()
            g = Grant(**json.loads(AESGCM(self._key).decrypt(raw[:12], raw[12:], tenant.encode())))
            self._mem[tenant] = g
            return g

    def put(self, tenant: str, g: Grant) -> None:
        with self._lock:
            self._mem[tenant] = g
            if self.persistent:
                from cryptography.hazmat.primitives.ciphers.aead import AESGCM

                self._dir.mkdir(parents=True, exist_ok=True)            # type: ignore[union-attr]
                nonce = secrets.token_bytes(12)
                body = json.dumps(g.__dict__).encode()
                self._path(tenant).write_bytes(nonce + AESGCM(self._key).encrypt(nonce, body, tenant.encode()))

    def drop(self, tenant: str) -> None:
        with self._lock:
            self._mem.pop(tenant, None)
            if self.persistent and self._path(tenant).is_file():
                self._path(tenant).unlink()


class OneTime:
    """One-time, expiring server-side entries (OAuth state, write previews)."""

    def __init__(self, ttl: int, clock=time.time):
        self.ttl, self.clock, self._d, self._lock = ttl, clock, {}, threading.Lock()

    def put(self, value: dict) -> str:
        k = secrets.token_urlsafe(32)
        with self._lock:
            now = self.clock()
            self._d = {a: b for a, b in self._d.items() if b[0] > now}
            self._d[k] = (now + self.ttl, value)
        return k

    def take(self, k: str | None) -> dict | None:
        with self._lock:
            hit = self._d.pop(k or "", None)
        return hit[1] if hit and hit[0] > self.clock() else None


def _adf(text: str) -> dict:
    """Plain text to an Atlassian document (paragraph per blank-line block; no markup is interpreted)."""
    paras = [p for p in text.replace("\r\n", "\n").split("\n\n") if p.strip()] or [""]
    return {"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": p}] if p else []} for p in paras]}


def _plain(adf) -> str:
    if not isinstance(adf, dict):
        return adf or ""
    out = []

    def walk(n):
        if n.get("type") == "text":
            out.append(n.get("text", ""))
        for c in n.get("content", []) or []:
            walk(c)
        if n.get("type") in ("paragraph", "heading", "listItem"):
            out.append("\n")
    walk(adf)
    return "".join(out).strip()


class Jira:
    """OAuth flow and REST calls. `http` is an httpx.Client or a mock with `.request(method, url, **kw)`."""

    def __init__(self, client: Client, vault: Vault, http=None, clock=time.time):
        self.client, self.vault, self.clock = client, vault, clock
        self.states, self.previews = OneTime(STATE_TTL, clock), OneTime(PREVIEW_TTL, clock)
        self._http = http

    @property
    def http(self):
        if self._http is None:
            import httpx

            self._http = httpx.Client(timeout=30.0)
        return self._http

    # ------------------------------------------------------------------ OAuth
    def authorize_url(self, tenant: str, principal: str) -> str:
        if not self.client.redirect_uri:
            raise JiraError("Set VELOXA_PUBLIC_URL so Veloxa knows its callback URL.", 409, "no_public_url")
        state = self.states.put({"tenant": tenant, "principal": principal})
        return AUTH_URL + "?" + urlencode({
            "audience": "api.atlassian.com", "client_id": self.client.client_id, "scope": " ".join(SCOPES),
            "redirect_uri": self.client.redirect_uri, "state": state, "response_type": "code", "prompt": "consent"})

    def _token(self, body: dict) -> dict:
        r = self.http.request("POST", TOKEN_URL, json={"client_id": self.client.client_id,
                                                       "client_secret": self.client.client_secret, **body})
        if r.status_code in (400, 401, 403):
            raise JiraError(EXPIRED if body.get("grant_type") == "refresh_token"
                            else "Atlassian refused the authorization. Try connecting again.", 401, "auth_refused")
        if r.status_code >= 400:
            raise JiraError(f"Atlassian token service returned HTTP {r.status_code}.")
        return r.json()

    def callback(self, code: str | None, state: str | None) -> tuple[str, str]:
        """Exchange the code, discover sites, store the grant. Returns (tenant, principal)."""
        ctx = self.states.take(state)
        if not ctx:
            raise JiraError("The sign-in link expired or was already used. Start again.", 400, "bad_state")
        if not code:
            raise JiraError("Atlassian did not return an authorization code.", 400, "no_code")
        t = self._token({"grant_type": "authorization_code", "code": code, "redirect_uri": self.client.redirect_uri})
        r = self.http.request("GET", RESOURCES_URL, headers={"Authorization": f"Bearer {t['access_token']}",
                                                             "Accept": "application/json"})
        if r.status_code >= 400:
            raise JiraError(f"Could not list Jira sites (HTTP {r.status_code}).")
        sites = [{"id": s["id"], "name": s.get("name"), "url": s.get("url"), "scopes": s.get("scopes", [])}
                 for s in r.json() if any(x.endswith("jira-work") for x in s.get("scopes", []))]
        if not sites:
            raise JiraError("This Atlassian account has no Jira site that granted the requested scopes.", 409,
                            "no_site")
        self.vault.put(ctx["tenant"], Grant(
            t["access_token"], t.get("refresh_token"), self.clock() + float(t.get("expires_in", 3600)),
            str(t.get("scope", "")).split(), sites, sites[0]["id"] if len(sites) == 1 else None,
            ctx["principal"], self.clock()))
        return ctx["tenant"], ctx["principal"]

    def choose_site(self, tenant: str, cloud_id: str) -> dict:
        g = self._grant(tenant, need_site=False)
        if cloud_id not in {s["id"] for s in g.sites}:
            raise JiraError("That site is not one this connection can access.", 422, "unknown_site")
        g.cloud_id = cloud_id
        self.vault.put(tenant, g)
        return g.redacted()

    def status(self, tenant: str) -> dict:
        g = self.vault.get(tenant)
        base = {"client_configured": True, "callback_url": self.client.redirect_uri, "requested_scopes": list(SCOPES),
                "token_storage": "encrypted at rest" if self.vault.persistent else "server memory (lost on restart)"}
        return base | (g.redacted() if g else {"connected": False, "expired": False, "site": None, "sites": [],
                                               "needs_site_choice": False, "last_sync": None, "last_error": None})

    def disconnect(self, tenant: str) -> None:
        """Forget the grant locally. (Revoking the app is done by the user in their Atlassian account settings.)"""
        self.vault.drop(tenant)

    # ------------------------------------------------------------------ REST
    def _grant(self, tenant: str, need_site: bool = True) -> Grant:
        g = self.vault.get(tenant)
        if not g:
            raise JiraError("Jira is not connected.", 409, "not_connected")
        if g.revoked:
            raise JiraError(EXPIRED, 401, "expired")
        if need_site and not g.cloud_id:
            raise JiraError("Choose which Jira site to use.", 409, "choose_site")
        return g

    def _refresh(self, tenant: str, g: Grant) -> None:
        if not g.refresh_token:
            g.revoked, g.last_error = True, EXPIRED
            self.vault.put(tenant, g)
            raise JiraError(EXPIRED, 401, "expired")
        try:
            t = self._token({"grant_type": "refresh_token", "refresh_token": g.refresh_token})
        except JiraError:
            g.revoked, g.last_error = True, EXPIRED
            self.vault.put(tenant, g)
            raise
        g.access_token, g.refresh_token = t["access_token"], t.get("refresh_token", g.refresh_token)  # rotating
        g.expires_at = self.clock() + float(t.get("expires_in", 3600))
        self.vault.put(tenant, g)

    def call(self, tenant: str, method: str, path: str, **kw):
        g = self._grant(tenant)
        if g.expires_at - 60 <= self.clock():
            self._refresh(tenant, g)
        for attempt in (0, 1):
            r = self.http.request(method, API_BASE.format(cloud_id=g.cloud_id) + path,
                                  headers={"Authorization": f"Bearer {g.access_token}", "Accept": "application/json"},
                                  **kw)
            if r.status_code == 401 and attempt == 0:
                self._refresh(tenant, g)
                continue
            break
        if r.status_code == 401:
            g.revoked, g.last_error = True, EXPIRED
            self.vault.put(tenant, g)
            raise JiraError(EXPIRED, 401, "expired")
        if r.status_code >= 400:
            msg = {403: "Jira refused this action for the connected account (permission or scope).",
                   404: "Jira has no such item, or the connected account cannot see it.",
                   429: "Jira is rate limiting requests. Try again shortly."}.get(
                r.status_code, f"Jira returned HTTP {r.status_code}.")
            detail = ""
            try:
                errs = r.json()
                detail = "; ".join([*errs.get("errorMessages", []), *(f"{k}: {v}" for k, v in
                                                                     (errs.get("errors") or {}).items())])
            except ValueError:            # non-JSON body: keep the generic message
                detail = ""
            g.last_error = (msg + (f" {detail}" if detail else ""))[:500]
            self.vault.put(tenant, g)
            raise JiraError(g.last_error, 502 if r.status_code >= 500 else r.status_code, "jira_refused")
        g.last_sync, g.last_error = self.clock(), None
        self.vault.put(tenant, g)
        return r.json() if r.content else {}

    def _site_url(self, tenant: str) -> str:
        g = self._grant(tenant)
        return next((s["url"] for s in g.sites if s["id"] == g.cloud_id), "") or ""

    def _shape(self, tenant: str, i: dict, retrieved: str) -> dict:
        f, site = i.get("fields", {}), self._site_url(tenant)
        name = lambda x: (x or {}).get("displayName") or (x or {}).get("name")  # noqa: E731
        return {"key": i["key"], "url": f"{site}/browse/{i['key']}" if site else None,
                "summary": f.get("summary"), "status": name(f.get("status")), "assignee": name(f.get("assignee")),
                "due_date": f.get("duedate"), "issue_type": name(f.get("issuetype")),
                "project": (f.get("project") or {}).get("key"), "priority": name(f.get("priority")),
                "description": _plain(f.get("description")), "created": f.get("created"),
                "updated": f.get("updated"),
                "source": {"system": "jira", "site": site, "key": i["key"], "retrieved_at": retrieved,
                           "note": "live from Jira; not admitted evidence"}}

    def search(self, tenant: str, jql: str, limit: int = 20) -> list[dict]:
        page = self.call(tenant, "POST", "/rest/api/3/search/jql",
                         json={"jql": jql, "maxResults": max(1, min(limit, 50)), "fields": list(ISSUE_FIELDS)})
        now = _iso(self.clock())
        return [self._shape(tenant, i, now) for i in page.get("issues", [])]

    def issue(self, tenant: str, key: str) -> dict:
        i = self.call(tenant, "GET", f"/rest/api/3/issue/{key}",
                      params={"fields": ",".join([*ISSUE_FIELDS, "comment", "issuelinks"])})
        out = self._shape(tenant, i, _iso(self.clock()))
        f = i.get("fields", {})
        out["comments"] = [{"author": (c.get("author") or {}).get("displayName"), "created": c.get("created"),
                            "updated": c.get("updated"), "body": _plain(c.get("body"))}
                           for c in (f.get("comment") or {}).get("comments", [])]
        out["links"] = [{"type": (ln.get("type") or {}).get("name"),
                         "key": (ln.get("outwardIssue") or ln.get("inwardIssue") or {}).get("key"),
                         "direction": "outward" if ln.get("outwardIssue") else "inward"}
                        for ln in f.get("issuelinks", [])]
        return out

    def projects(self, tenant: str) -> list[dict]:
        r = self.call(tenant, "GET", "/rest/api/3/project/search", params={"maxResults": 50})
        return [{"key": p["key"], "name": p.get("name"), "id": p.get("id")} for p in r.get("values", [])]

    def issue_types(self, tenant: str, project: str) -> list[dict]:
        r = self.call(tenant, "GET", f"/rest/api/3/issue/createmeta/{project}/issuetypes")
        return [{"id": t["id"], "name": t.get("name")} for t in r.get("issueTypes", r.get("values", []))]

    def create_fields(self, tenant: str, project: str, type_id: str) -> dict[str, dict]:
        r = self.call(tenant, "GET", f"/rest/api/3/issue/createmeta/{project}/issuetypes/{type_id}")
        return {f.get("fieldId") or f.get("key"): {"name": f.get("name"), "required": bool(f.get("required")),
                                                   "has_default": bool(f.get("hasDefaultValue"))}
                for f in r.get("fields", r.get("values", []))}

    # ------------------------------------------------------------------ confirmed writes
    def preview_create(self, tenant: str, principal: str, project: str, issue_type_id: str, summary: str,
                       description: str = "", extra: dict | None = None) -> dict:
        extra = extra or {}
        if project not in {p["key"] for p in self.projects(tenant)}:
            raise JiraError(f"Project {project} is not visible to the connected Jira account.", 422, "unknown_project")
        types = {t["id"]: t["name"] for t in self.issue_types(tenant, project)}
        if issue_type_id not in types:
            raise JiraError("That issue type is not available in this project.", 422, "unknown_issue_type")
        meta = self.create_fields(tenant, project, issue_type_id)
        fields = {"project": {"key": project}, "issuetype": {"id": issue_type_id}, "summary": summary.strip(),
                  **({"description": _adf(description)} if description.strip() else {}), **extra}
        unknown = [k for k in fields if k not in meta and k not in ("project", "issuetype")]
        if unknown:
            raise JiraError(f"Jira does not allow these fields here: {', '.join(unknown)}.", 422, "unknown_field")
        missing = [m["name"] or k for k, m in meta.items() if m["required"] and not m["has_default"]
                   and k not in fields]
        if missing:
            raise JiraError(f"Jira requires: {', '.join(missing)}.", 422, "missing_required")
        if not summary.strip():
            raise JiraError("A summary is required.", 422, "missing_required")
        show = {"Project": project, "Issue type": types[issue_type_id], "Summary": summary.strip(),
                **{meta[k]["name"] or k: v for k, v in extra.items()}}
        pid = self.previews.put({"tenant": tenant, "principal": principal, "op": "create", "fields": fields})
        return {"preview_id": pid, "operation": "create", "project": project, "issue_type": types[issue_type_id],
                "fields": show, "description": description.strip() or None, "expires_in_s": PREVIEW_TTL,
                "requires_confirmation": True}

    def preview_update(self, tenant: str, principal: str, key: str, fields: dict | None = None,
                       comment: str = "") -> dict:
        fields = dict(fields or {})
        meta = self.call(tenant, "GET", f"/rest/api/3/issue/{key}/editmeta").get("fields", {})
        unknown = [k for k in fields if k not in meta]
        if unknown:
            raise JiraError(f"Jira does not allow editing these fields on {key}: {', '.join(unknown)}.", 422,
                            "unknown_field")
        if not fields and not comment.strip():
            raise JiraError("Nothing to change.", 422, "empty_update")
        if "description" in fields and isinstance(fields["description"], str):
            fields["description"] = _adf(fields["description"])
        current = self.issue(tenant, key)
        show = {(meta[k].get("name") or k): {"current": current.get(k if k != "duedate" else "due_date"),
                                             "proposed": _plain(v) if isinstance(v, dict) else v}
                for k, v in fields.items()}
        pid = self.previews.put({"tenant": tenant, "principal": principal, "op": "update", "key": key,
                                 "fields": fields, "comment": comment.strip()})
        return {"preview_id": pid, "operation": "update", "key": key, "url": current["url"], "fields": show,
                "comment": comment.strip() or None, "expires_in_s": PREVIEW_TTL, "requires_confirmation": True}

    def commit(self, tenant: str, principal: str, preview_id: str, confirm: bool) -> dict:
        if confirm is not True:
            raise JiraError("Writing to Jira requires explicit confirmation.", 400, "not_confirmed")
        p = self.previews.take(preview_id)
        if not p or p["tenant"] != tenant or p["principal"] != principal:
            raise JiraError("This preview expired or was already used. Preview the change again.", 409, "bad_preview")
        site = self._site_url(tenant)
        if p["op"] == "create":
            r = self.call(tenant, "POST", "/rest/api/3/issue", json={"fields": p["fields"]})
            return {"operation": "create", "key": r.get("key"), "url": f"{site}/browse/{r.get('key')}"}
        if p["fields"]:
            self.call(tenant, "PUT", f"/rest/api/3/issue/{p['key']}", json={"fields": p["fields"]})
        if p["comment"]:
            self.call(tenant, "POST", f"/rest/api/3/issue/{p['key']}/comment", json={"body": _adf(p["comment"])})
        return {"operation": "update", "key": p["key"], "url": f"{site}/browse/{p['key']}",
                "changed": sorted(p["fields"]), "commented": bool(p["comment"])}
