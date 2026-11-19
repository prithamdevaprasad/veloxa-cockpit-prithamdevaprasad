"""Gmail through Google OAuth 2.0 (web server flow): connect, choose a scope, read. Read-only, nothing else.

Security model (same backend-for-frontend design as `jira_oauth`):
- Only `https://www.googleapis.com/auth/gmail.readonly` is requested. No send, modify, delete or full-mail scope.
- The client comes from the Google credentials JSON at `VELOXA_GMAIL_CLIENT_FILE` (a path outside the repository).
  It is read by the server only; nothing from it is ever returned, logged or put in an error message.
- The callback URL is `VELOXA_PUBLIC_URL` + `CALLBACK_PATH`; without `VELOXA_PUBLIC_URL` connecting is refused.
- `state` is 256-bit, one-time, 10 minutes, bound to the tenant and the administrator; PKCE S256 is used as well.
- Tokens live only in the shared `Vault` (AES-GCM at rest when configured), file prefix `gmail`.
- After connecting, NOTHING is read until an administrator saves an explicit scope: labels, approved senders, a
  start date and an initial import limit (default 25, maximum 500). Attachments are never fetched.
- Google "Testing" apps can have Gmail refresh tokens expire after 7 days: a refused refresh marks the connection
  `needs_reconnect` (visible in the UI); the grant is kept for audit until disconnected.
- Disconnect revokes the token at Google and forgets it locally.
Messages are evidence of COMMUNICATION, not of truth: they enter the data room as a pending submission and pass the
same review and admission as an upload."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode

from .jira_oauth import OneTime, Vault, _iso

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105  (a URL)
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
CALLBACK_PATH = "/v1/integrations/gmail/callback"
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
STATE_TTL = 10 * 60
MAX_IMPORT = 500
HEADERS = ("From", "To", "Cc", "Subject", "Date", "Message-ID")
RECONNECT = "Gmail access expired or was revoked. Reconnect Gmail in Administration > Integrations."


class GmailError(Exception):
    def __init__(self, message: str, status: int = 502, code: str = "gmail_error"):
        super().__init__(message)
        self.status, self.code = status, code


@dataclass(frozen=True)
class Client:
    client_id: str
    client_secret: str = field(repr=False)
    public_url: str | None

    @staticmethod
    def from_env(env: dict | None = None) -> Client | None:
        env = os.environ if env is None else env
        path = env.get("VELOXA_GMAIL_CLIENT_FILE", "").strip()
        if not path or not Path(path).is_file():
            return None
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        v = d.get("web") or {}
        if not v.get("client_id") or not v.get("client_secret"):
            return None
        return Client(v["client_id"], v["client_secret"], env.get("VELOXA_PUBLIC_URL", "").strip().rstrip("/") or None)

    @property
    def redirect_uri(self) -> str | None:
        return f"{self.public_url}{CALLBACK_PATH}" if self.public_url else None


@dataclass
class Grant:
    access_token: str = field(repr=False)
    refresh_token: str | None = field(repr=False)
    expires_at: float
    scopes: list[str]
    mailbox: str | None = None
    connected_by: str | None = None
    connected_at: float = 0.0
    scope: dict | None = None                 # the administrator's explicit selection; None = nothing is read
    history_id: str | None = None             # incremental cursor
    last_sync: float | None = None
    last_error: str | None = None
    needs_reconnect: bool = False

    def redacted(self) -> dict:
        return {"connected": True, "mailbox": self.mailbox, "scopes": self.scopes, "connected_by": self.connected_by,
                "connected_at": _iso(self.connected_at), "scope": self.scope, "scope_selected": bool(self.scope),
                "last_sync": _iso(self.last_sync), "last_error": self.last_error,
                "needs_reconnect": self.needs_reconnect}


def _scope_query(scope: dict) -> str:
    parts = []
    senders = scope.get("senders") or []
    if senders:
        parts.append("(" + " OR ".join(f"from:{s}" for s in senders) + ")")
    if scope.get("after"):
        parts.append("after:" + scope["after"].replace("-", "/"))
    return " ".join(parts)


def validate_scope(raw: dict) -> dict:
    labels = [str(x).strip() for x in raw.get("labels") or [] if str(x).strip()]
    senders = [str(x).strip().lower() for x in raw.get("senders") or [] if str(x).strip()]
    for s in senders:
        if not re.fullmatch(r"[^@\s()\"]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|@?[A-Za-z0-9.-]+\.[A-Za-z]{2,}", s):
            raise GmailError(f"'{s}' is not an e-mail address or domain.", 422, "bad_scope")
    after = (raw.get("after") or "").strip() or None
    if after and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", after):
        raise GmailError("Start date must be YYYY-MM-DD.", 422, "bad_scope")
    limit = int(raw.get("limit") or 25)
    if not 1 <= limit <= MAX_IMPORT:
        raise GmailError(f"Initial import limit must be between 1 and {MAX_IMPORT}.", 422, "bad_scope")
    if not labels and not senders:
        raise GmailError("Choose at least one label or approved sender; the whole mailbox is never read.", 422,
                         "bad_scope")
    return {"labels": labels, "senders": senders, "after": after, "limit": limit, "attachments": False}


class Gmail:
    def __init__(self, client: Client, vault: Vault | None = None, http=None, clock=time.time):
        self.client, self.clock = client, clock
        self.vault = vault or Vault(prefix="gmail", grant_type=Grant)
        self.states = OneTime(STATE_TTL, clock)
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
            raise GmailError("Set VELOXA_PUBLIC_URL so Veloxa knows its callback URL.", 409, "no_public_url")
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = self.states.put({"tenant": tenant, "principal": principal, "verifier": verifier})
        return AUTH_URL + "?" + urlencode({
            "client_id": self.client.client_id, "redirect_uri": self.client.redirect_uri, "response_type": "code",
            "scope": SCOPE, "state": state, "access_type": "offline", "prompt": "consent",
            "include_granted_scopes": "false", "code_challenge": challenge, "code_challenge_method": "S256"})

    def _token(self, body: dict) -> dict:
        r = self.http.request("POST", TOKEN_URL, data={"client_id": self.client.client_id,
                                                       "client_secret": self.client.client_secret, **body})
        if r.status_code in (400, 401, 403):
            raise GmailError(RECONNECT if body.get("grant_type") == "refresh_token"
                             else "Google refused the authorization. Try connecting again.", 401, "auth_refused")
        if r.status_code >= 400:
            raise GmailError(f"Google token service returned HTTP {r.status_code}.")
        return r.json()

    def callback(self, code: str | None, state: str | None) -> tuple[str, str]:
        ctx = self.states.take(state)
        if not ctx:
            raise GmailError("The sign-in link expired or was already used. Start again.", 400, "bad_state")
        if not code:
            raise GmailError("Google did not return an authorization code.", 400, "no_code")
        t = self._token({"grant_type": "authorization_code", "code": code, "redirect_uri": self.client.redirect_uri,
                         "code_verifier": ctx["verifier"]})
        granted = str(t.get("scope", "")).split()
        if SCOPE not in granted or any("gmail" in s and s != SCOPE for s in granted):
            self._revoke(t.get("refresh_token") or t["access_token"])
            raise GmailError("Google granted a different access level than read-only; nothing was stored.", 409,
                             "wrong_scope")
        g = Grant(t["access_token"], t.get("refresh_token"), self.clock() + float(t.get("expires_in", 3600)),
                  granted, None, ctx["principal"], self.clock())
        self.vault.put(ctx["tenant"], g)
        prof = self.call(ctx["tenant"], "GET", "/profile")
        g = self.vault.get(ctx["tenant"])
        g.mailbox, g.history_id = prof.get("emailAddress"), None
        self.vault.put(ctx["tenant"], g)
        return ctx["tenant"], ctx["principal"]

    def _revoke(self, token: str | None) -> bool:
        if not token:
            return False
        try:
            return self.http.request("POST", REVOKE_URL, data={"token": token}).status_code == 200
        except Exception:                                               # noqa: BLE001 (best effort; forgotten anyway)
            return False

    def status(self, tenant: str) -> dict:
        g = self.vault.get(tenant)
        base = {"client_configured": True, "callback_url": self.client.redirect_uri, "requested_scopes": [SCOPE],
                "token_storage": "encrypted at rest" if self.vault.persistent else "server memory (lost on restart)"}
        return base | (g.redacted() if g else {"connected": False, "mailbox": None, "scope_selected": False,
                                               "needs_reconnect": False})

    def disconnect(self, tenant: str) -> dict:
        g = self.vault.get(tenant)
        revoked = self._revoke((g.refresh_token or g.access_token) if g else None)
        self.vault.drop(tenant)
        return {"connected": False, "revoked_at_google": revoked}

    def set_scope(self, tenant: str, raw: dict) -> dict:
        g = self._grant(tenant)
        g.scope, g.history_id = validate_scope(raw), None        # a new scope starts a new bounded initial import
        self.vault.put(tenant, g)
        return g.redacted()

    def labels(self, tenant: str) -> list[dict]:
        return [{"id": x["id"], "name": x["name"], "type": x.get("type")}
                for x in self.call(tenant, "GET", "/labels").get("labels", [])]

    # ------------------------------------------------------------------ REST
    def _grant(self, tenant: str) -> Grant:
        g = self.vault.get(tenant)
        if not g:
            raise GmailError("Gmail is not connected.", 409, "not_connected")
        if g.needs_reconnect:
            raise GmailError(RECONNECT, 401, "needs_reconnect")
        return g

    def _refresh(self, tenant: str, g: Grant) -> None:
        if not g.refresh_token:
            g.needs_reconnect = True
            self.vault.put(tenant, g)
            raise GmailError(RECONNECT, 401, "needs_reconnect")
        try:
            t = self._token({"grant_type": "refresh_token", "refresh_token": g.refresh_token})
        except GmailError:
            g.needs_reconnect, g.last_error = True, "refresh refused"
            self.vault.put(tenant, g)
            raise GmailError(RECONNECT, 401, "needs_reconnect") from None
        g.access_token, g.expires_at = t["access_token"], self.clock() + float(t.get("expires_in", 3600))
        self.vault.put(tenant, g)

    def call(self, tenant: str, method: str, path: str, **kw) -> dict:
        g = self._grant(tenant)
        if g.expires_at - 60 <= self.clock():
            self._refresh(tenant, g)
        for attempt in range(4):
            r = self.http.request(method, API + path, headers={"Authorization": f"Bearer {g.access_token}"}, **kw)
            if r.status_code == 401 and attempt == 0:
                self._refresh(tenant, g)
                continue
            if r.status_code in (429, 500, 503) and attempt < 3:
                time.sleep(min(2 ** attempt, 8))
                continue
            if r.status_code == 404 and path.startswith("/history"):
                raise GmailError("History cursor expired.", 404, "history_expired")
            if r.status_code >= 400:
                raise GmailError(f"Gmail returned HTTP {r.status_code}.", r.status_code if r.status_code < 500
                                 else 502)
            return r.json()
        raise GmailError("Gmail kept refusing the request (rate limit).", 429, "rate_limited")

    def _message(self, tenant: str, mid: str, scope: dict) -> dict | None:
        m = self.call(tenant, "GET", f"/messages/{mid}", params=[("format", "metadata"),
                                                                *[("metadataHeaders", h) for h in HEADERS]])
        h = {x["name"]: x["value"] for x in m.get("payload", {}).get("headers", [])}
        sender = (re.search(r"<([^>]+)>", h.get("From", "")) or re.search(r"(\S+@\S+)", h.get("From", "")))
        addr = (sender.group(1) if sender else "").lower()
        if scope["senders"] and not any(addr == s or addr.endswith(s if s.startswith("@") else "@" + s)
                                        for s in scope["senders"]):
            return None                                          # outside the approved senders: never stored
        if scope["labels"] and not set(scope["labels"]) & set(m.get("labelIds", [])):
            return None
        return {"source": "gmail", "message_id": m["id"], "thread_id": m.get("threadId"),
                "history_id": m.get("historyId"), "internal_date": m.get("internalDate"),
                "from": h.get("From"), "to": h.get("To"), "cc": h.get("Cc"), "subject": h.get("Subject"),
                "date": h.get("Date"), "rfc822_message_id": h.get("Message-ID"), "labels": m.get("labelIds", []),
                "snippet": m.get("snippet"),
                "locator": f"gmail:{self.vault.get(tenant).mailbox}/messages/{m['id']}",
                "access_scope": {"mailbox": self.vault.get(tenant).mailbox, "labels": scope["labels"],
                                 "senders": scope["senders"]},
                "evidence_kind": "communication (not verified fact)"}

    def fetch(self, tenant: str, advance: bool = True) -> tuple[list[dict], dict]:
        """Bounded initial import, or incremental since the stored historyId (recovering from an expired one)."""
        g = self._grant(tenant)
        if not g.scope:
            raise GmailError("Choose the mailbox scope first; nothing is read before that.", 409, "no_scope")
        scope, ids, info = g.scope, [], {"mode": "initial", "pages": 0}
        if g.history_id:
            info["mode"] = "incremental"
            try:
                token, newest = None, g.history_id
                while True:
                    params = {"startHistoryId": g.history_id, "historyTypes": "messageAdded", "maxResults": 100}
                    if token:
                        params["pageToken"] = token
                    page = self.call(tenant, "GET", "/history", params=params)
                    info["pages"] += 1
                    for h in page.get("history", []):
                        ids += [x["message"]["id"] for x in h.get("messagesAdded", [])]
                    newest = page.get("historyId", newest)
                    token = page.get("nextPageToken")
                    if not token:
                        break
            except GmailError as e:
                if e.code != "history_expired":
                    raise
                info["mode"], info["recovered_from_expired_cursor"] = "initial", True
                ids = []
        if info["mode"] == "initial":
            token = None
            while len(ids) < scope["limit"]:
                params = [("maxResults", min(100, scope["limit"] - len(ids)))]
                if q := _scope_query(scope):
                    params.append(("q", q))
                params += [("labelIds", lb) for lb in scope["labels"][:1]]
                if token:
                    params.append(("pageToken", token))
                page = self.call(tenant, "GET", "/messages", params=params)
                info["pages"] += 1
                ids += [m["id"] for m in page.get("messages", [])]
                token = page.get("nextPageToken")
                if not token:
                    break
            newest = self.call(tenant, "GET", "/profile").get("historyId")
        recs = [r for r in (self._message(tenant, i, scope) for i in dict.fromkeys(ids)) if r]
        if advance:                                    # a preview never moves the cursor
            g = self.vault.get(tenant)
            g.history_id, g.last_sync, g.last_error = newest, self.clock(), None
            self.vault.put(tenant, g)
        info |= {"listed": len(ids), "kept": len(recs), "cursor": newest}
        return recs, info
