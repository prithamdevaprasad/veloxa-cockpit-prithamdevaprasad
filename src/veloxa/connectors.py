"""Source connectors (read-only) that feed the GOVERNED data room. Jira first; a reusable contract for the rest.

A connector never writes evidence. It fetches records from an allowlisted scope, writes them as a JSONL export, and
submits that export through `intake.upload` as a data-room submission (state: pending review). Review, separate
authorisation, admission, sealing and projection then happen exactly as for a manual upload. Each run is recorded in
`runtime.connector_run` (scope, cursor before/after, counts, status, error).

Jira (Atlassian Cloud REST v3, basic auth with an API token; read-only):
- configuration contract (all required, else the connector is DISABLED, never half-enabled):
  `VELOXA_JIRA_BASE_URL`, `VELOXA_JIRA_EMAIL`, `VELOXA_JIRA_API_TOKEN` (backend only), `VELOXA_JIRA_PROJECTS`
  (project-key allowlist), `VELOXA_JIRA_FIELDS` (field allowlist); optional `VELOXA_JIRA_WEBHOOK_SECRET`;
- initial sync (no cursor) and incremental sync (`updated >= cursor`), paginated with `nextPageToken`;
- HTTP 429/503 honour `Retry-After` with bounded exponential backoff; other errors fail the run (recorded);
- idempotent: records are keyed by issue key + `updated`; an unchanged record is not resubmitted;
- a webhook is only a SIGNAL (HMAC-verified) to fetch the authoritative record by key; it is never evidence;
- fields outside the allowlist are dropped; unknown custom fields are never interpreted (K-09).
No credentials are configured in this repository: the connector is tested with a mocked transport only.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass, field
from typing import Protocol

REQUIRED = ("VELOXA_JIRA_BASE_URL", "VELOXA_JIRA_EMAIL", "VELOXA_JIRA_API_TOKEN", "VELOXA_JIRA_PROJECTS",
            "VELOXA_JIRA_FIELDS")


class ConnectorDisabled(Exception):
    pass


class ConnectorFailed(Exception):
    pass


class Connector(Protocol):
    """The reusable contract (GitHub/GitLab, HRMS, finance, CRM, incident systems implement the same shape)."""
    name: str

    def status(self) -> dict: ...

    def fetch(self, cursor: str | None) -> tuple[list[dict], str | None]: ...


@dataclass
class JiraConfig:
    base_url: str
    email: str
    token: str = field(repr=False)
    projects: tuple[str, ...]
    fields: tuple[str, ...]
    webhook_secret: str | None = field(default=None, repr=False)

    @staticmethod
    def from_env(env: dict | None = None) -> JiraConfig:
        e = env if env is not None else os.environ
        missing = [k for k in REQUIRED if not str(e.get(k, "")).strip()]
        if missing:
            raise ConnectorDisabled(f"Jira connector disabled: missing {', '.join(missing)}")
        split = lambda k: tuple(x.strip() for x in e[k].split(",") if x.strip())  # noqa: E731
        return JiraConfig(e["VELOXA_JIRA_BASE_URL"].rstrip("/"), e["VELOXA_JIRA_EMAIL"], e["VELOXA_JIRA_API_TOKEN"],
                          split("VELOXA_JIRA_PROJECTS"), split("VELOXA_JIRA_FIELDS"),
                          e.get("VELOXA_JIRA_WEBHOOK_SECRET") or None)


def jira_status(env: dict | None = None) -> dict:
    """Safe diagnostics: never values of credentials."""
    try:
        c = JiraConfig.from_env(env)
    except ConnectorDisabled as e:
        return {"connector": "jira", "state": "disabled", "reason": str(e)}
    return {"connector": "jira", "state": "configured (not verified until a connection test passes)",
            "projects": list(c.projects), "fields": list(c.fields), "webhook": bool(c.webhook_secret)}


@dataclass
class JiraConnector:
    config: JiraConfig
    http: object = None                       # an httpx.Client (or a mock with the same .request API)
    max_retries: int = 4
    page_size: int = 50
    sleep: object = time.sleep
    name: str = "jira"

    def _client(self):
        if self.http is None:
            import httpx

            auth = base64.b64encode(f"{self.config.email}:{self.config.token}".encode()).decode()
            self.http = httpx.Client(base_url=self.config.base_url, timeout=30.0,
                                     headers={"Authorization": f"Basic {auth}", "Accept": "application/json"})
        return self.http

    def _request(self, method: str, url: str, **kw):
        delay = 1.0
        for attempt in range(self.max_retries + 1):
            r = self._client().request(method, url, **kw)
            if r.status_code in (429, 503) and attempt < self.max_retries:
                wait = float(r.headers.get("Retry-After", delay))
                self.sleep(min(wait, 60.0))
                delay = min(delay * 2, 60.0)
                continue
            if r.status_code >= 400:
                raise ConnectorFailed(f"Jira returned HTTP {r.status_code}")
            return r.json()
        raise ConnectorFailed("Jira rate limit persisted beyond the retry bound")

    def jql(self, cursor: str | None) -> str:
        projects = ", ".join(f'"{p}"' for p in self.config.projects)
        q = f"project in ({projects})"
        return q + (f' AND updated >= "{cursor}"' if cursor else "") + " ORDER BY updated ASC"

    def _shape(self, issue: dict) -> dict:
        f = issue.get("fields", {})
        kept = {k: f.get(k) for k in self.config.fields if k in f}          # allowlist only; nothing interpreted
        rec = {"key": issue["key"], "id": issue.get("id"), "updated": f.get("updated"), "fields": kept}
        if self.config.base_url == "oauth":                 # flat, readable columns (searchable; mapped to claims)
            from .jira_oauth import _plain

            name = lambda x: (x or {}).get("displayName") or (x or {}).get("name") if isinstance(x, dict) else x  # noqa: E731
            comments = (f.get("comment") or {}).get("comments", []) if isinstance(f.get("comment"), dict) else []
            rec.update({
                "Key": issue["key"], "Summary": f.get("summary"), "Status": name(f.get("status")),
                "Assignee": name(f.get("assignee")), "Due date": f.get("duedate"),
                # Jira's own stable account identifier: the key for identity resolution (never the display name)
                "Assignee account": (f.get("assignee") or {}).get("accountId") if isinstance(f.get("assignee"), dict)
                else None,
                "Issue type": name(f.get("issuetype")),
                "Project": issue["key"].split("-")[0], "Created": f.get("created"), "Updated": f.get("updated"),
                "Fix Version": ", ".join(v.get("name", "") for v in f.get("fixVersions") or []) or None,
                "Comments": " | ".join(_plain(c.get("body")) for c in comments)[:4000] or None,
                "Links": ", ".join(((ln.get("outwardIssue") or ln.get("inwardIssue") or {}).get("key") or "")
                                   for ln in f.get("issuelinks") or []) or None})
        return rec

    def fetch(self, cursor: str | None) -> tuple[list[dict], str | None]:
        out, token, newest = [], None, cursor
        while True:
            body = {"jql": self.jql(cursor), "maxResults": self.page_size,
                    "fields": list(dict.fromkeys(["updated", *self.config.fields]))}
            if token:
                body["nextPageToken"] = token
            page = self._request("POST", "/rest/api/3/search/jql", json=body)
            for issue in page.get("issues", []):
                rec = self._shape(issue)
                out.append(rec)
                if rec["updated"] and (newest is None or rec["updated"] > newest):
                    newest = rec["updated"]
            token = page.get("nextPageToken")
            if not token:
                break
        return out, newest

    def fetch_one(self, key: str) -> dict:
        """Webhook follow-up: fetch the authoritative record (the webhook payload itself is never evidence)."""
        if key.split("-")[0] not in self.config.projects:
            raise ConnectorFailed("issue is outside the approved project scope")
        issue = self._request("GET", f"/rest/api/3/issue/{key}", params={"fields": ",".join(self.config.fields)})
        return self._shape(issue)

    def verify_webhook(self, body: bytes, signature: str | None) -> bool:
        if not self.config.webhook_secret or not signature:
            return False
        want = "sha256=" + hmac.new(self.config.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(want, signature)


DEFAULT_SYNC_FIELDS = ("summary", "status", "assignee", "reporter", "issuetype", "priority", "created", "updated",
                       "duedate", "resolutiondate", "labels", "parent", "fixVersions", "comment", "issuelinks")


class _Resp:
    def __init__(self, status: int, body):
        self.status_code, self._body, self.headers = status, body, {}

    def json(self):
        return self._body


class OAuthTransport:
    """The JiraConnector's transport over the Atlassian OAuth connection (cloud ID, bearer token and refresh handled
    by `jira_oauth.Jira`). There is no second credential system: no API token is used or stored."""

    def __init__(self, jira, tenant: str):
        self.jira, self.tenant = jira, tenant

    def request(self, method: str, url: str, **kw):
        from . import jira_oauth

        try:
            return _Resp(200, self.jira.call(self.tenant, method, url, **kw))
        except jira_oauth.JiraError as e:
            return _Resp(e.status if e.status >= 400 else 502, {})


def oauth_connector(jira, tenant: str, projects: list[str], fields: list[str] | None = None) -> JiraConnector:
    """A read-only connector for the given project keys over the OAuth connection (fields default to the work fields:
    keys, status, assignee, timestamps, comments, links)."""
    cfg = JiraConfig("oauth", "oauth", "", tuple(projects), tuple(fields or DEFAULT_SYNC_FIELDS))
    return JiraConnector(cfg, http=OAuthTransport(jira, tenant))


def changed(records: list[dict], seen: dict[str, str]) -> list[dict]:
    """Idempotency: only records whose (key, updated) differs from what was already submitted."""
    return [r for r in records if seen.get(r["key"]) != r["updated"]]


def to_jsonl(records: list[dict]) -> bytes:
    return "".join(json.dumps(r, sort_keys=True, default=str) + "\n" for r in records).encode("utf-8")


def submit(conn, store, tenant_id, name: str, records: list[dict], *, actor: str, department_id: str,
           source_type: str, mode: str, scope: dict, cursor_before: str | None, cursor_after: str | None,
           key: str = "key", version: str = "updated", title: str | None = None) -> dict:
    """The ONE path from any connector into evidence: drop versions already submitted (idempotency), submit the rest
    to the data room as a pending submission (same review, admission and projection as a manual upload), and record
    the run with its scope and cursors."""
    from . import intake
    from .db import tenant_tx

    seen: dict = {}
    if mode != "initial":                                # "initial" = a deliberate full refresh (new versions)
        with tenant_tx(conn, tenant_id, actor):
            rows = conn.execute("SELECT scope FROM runtime.connector_run WHERE tenant_id = %s AND connector = %s"
                                " AND status = 'ok'", (tenant_id, name)).fetchall()
        for r in rows:
            seen |= r["scope"].get("submitted_versions", {})
    new = [r for r in records if seen.get(str(r[key])) != str(r.get(version))]
    sid = None
    if new:
        sid = intake.upload(conn, store, tenant_id, actor, department_id, source_type,
                            title or f"{name} sync {cursor_after or ''}".strip(),
                            [intake.Upload(f"{name}_sync.jsonl", to_jsonl(new))])
    scope = {**scope, "submitted_versions": {str(r[key]): str(r.get(version)) for r in new},
             "submission_id": str(sid) if sid else None}
    with tenant_tx(conn, tenant_id, actor):
        conn.execute("INSERT INTO runtime.connector_run (tenant_id, connector, mode, scope, cursor_before,"
                     " cursor_after, fetched, changed, submitted, status, finished_at)"
                     " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'ok',now())",
                     (tenant_id, name, mode, json.dumps(scope), cursor_before, cursor_after, len(records), len(new),
                      len(new) if sid else 0))
    return {"status": "ok", "fetched": len(records), "changed": len(new), "submission_id": sid,
            "cursor": cursor_after}


def record_failure(conn, tenant_id, name: str, *, actor: str, mode: str, scope: dict, cursor_before, error: str):
    from .db import tenant_tx

    with tenant_tx(conn, tenant_id, actor):
        conn.execute("INSERT INTO runtime.connector_run (tenant_id, connector, mode, scope, cursor_before,"
                     " status, error, finished_at) VALUES (%s,%s,%s,%s,%s,'failed',%s,now())",
                     (tenant_id, name, mode, json.dumps(scope), cursor_before, error))
    return {"status": "failed", "error": error}


def sync(conn, store, tenant_id, connector: JiraConnector, *, actor: str, department_id: str, source_type: str,
         mode: str = "incremental") -> dict:
    """One Jira run: fetch since the last cursor, then `submit`."""
    from .db import tenant_tx

    with tenant_tx(conn, tenant_id, actor):                      # FORCE RLS: every read and write is tenant-scoped
        last = conn.execute("SELECT cursor_after FROM runtime.connector_run WHERE tenant_id = %s AND connector = %s"
                            " AND status = 'ok' ORDER BY started_at DESC LIMIT 1",
                            (tenant_id, connector.name)).fetchone()
    cursor = None if mode == "initial" or not last else last["cursor_after"]
    scope = {"projects": list(connector.config.projects), "fields": list(connector.config.fields)}
    try:
        records, newest = connector.fetch(cursor)
        return submit(conn, store, tenant_id, connector.name, records, actor=actor, department_id=department_id,
                      source_type=source_type, mode=mode, scope=scope, cursor_before=cursor, cursor_after=newest,
                      title=f"Jira sync {newest or ''}".strip())
    except ConnectorFailed as e:
        return record_failure(conn, tenant_id, connector.name, actor=actor, mode=mode, scope=scope,
                              cursor_before=cursor, error=str(e))
