"""Browser sign-in: OpenID Connect authorization code flow with PKCE, server-side sessions, local demo entry.

Design (backend-for-frontend; the browser never holds an ID token, access token or fixture token):

- **Providers** come only from configuration. Microsoft Entra ID (`VELOXA_SSO_ENTRA_TENANT`, `_CLIENT_ID`,
  optional `_CLIENT_SECRET`) and, optionally, Google Workspace (`VELOXA_SSO_GOOGLE_CLIENT_ID`, `_CLIENT_SECRET`).
  An unconfigured provider is not offered. Per-tenant OIDC (`VELOXA_OIDC=1`) must also be on: the returned ID
  token is verified by the existing `OidcAuthenticator` against the TENANT'S registered issuer (signature via
  JWKS, issuer, audience = client ID, expiry), then mapped to an invited principal by `(issuer, sub)`. The
  tenant, role and CEO designation are never taken from the browser or from token claims.
- **Login**: `state` (CSRF, bound to an HttpOnly cookie), `nonce` (replay), PKCE S256 verifier; one-time, 10-minute
  pending entries. Optional `VELOXA_SSO_REQUIRE_MFA=1` requires `amr` to contain `mfa` (the IdP enforces MFA).
- **Sessions**: 256-bit random IDs, stored hashed in process memory, 8-hour absolute and 30-minute idle expiry,
  cookie `HttpOnly; SameSite=Lax; Path=/` and `Secure` behind HTTPS. Cookie-authenticated writes must carry the
  `X-Veloxa-Request` header (a cross-site form cannot set it). Sign-out deletes the session server-side.
- **Demo entry**: only with the development fixture (`VELOXA_DEV_AUTH=1`) AND `VELOXA_DEMO_LOGIN=1`, only from a
  loopback client to a localhost host, and only for a synthetic Demo Lab tenant (never the supplied company).
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import os
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from urllib.parse import urlencode

SESSION_COOKIE = "veloxa_session"
LOGIN_COOKIE = "veloxa_login"
CSRF_HEADER = "x-veloxa-request"
ABSOLUTE_TTL = 8 * 3600
IDLE_TTL = 30 * 60
PENDING_TTL = 10 * 60
DEMO_REFUSED_SLUGS = {"veloxa"}                  # the supplied company: never reachable through the demo entry


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    issuer: str
    client_id: str
    client_secret: str | None
    scopes: str = "openid profile email"
    extra: tuple[tuple[str, str], ...] = ()


def providers_from_env(env: Mapping[str, str] | None = None) -> list[Provider]:
    """Configured providers only. Nothing is invented: a provider without its tenant/client ID is absent."""
    env = os.environ if env is None else env
    if env.get("VELOXA_OIDC", "").strip() != "1":
        return []                                   # ID tokens could not be verified per tenant
    out = []
    tenant, cid = env.get("VELOXA_SSO_ENTRA_TENANT", "").strip(), env.get("VELOXA_SSO_ENTRA_CLIENT_ID", "").strip()
    if tenant and cid:
        out.append(Provider("entra", "Microsoft", f"https://login.microsoftonline.com/{tenant}/v2.0", cid,
                            env.get("VELOXA_SSO_ENTRA_CLIENT_SECRET") or None))
    gid = env.get("VELOXA_SSO_GOOGLE_CLIENT_ID", "").strip()
    if gid and env.get("VELOXA_SSO_GOOGLE_CLIENT_SECRET"):
        hd = env.get("VELOXA_SSO_GOOGLE_HD", "").strip()
        out.append(Provider("google", "Google Workspace", "https://accounts.google.com", gid,
                            env["VELOXA_SSO_GOOGLE_CLIENT_SECRET"], extra=(("hd", hd),) if hd else ()))
    return out


def redirect_base(env: Mapping[str, str] | None = None) -> str | None:
    env = os.environ if env is None else env
    base = env.get("VELOXA_PUBLIC_URL", "").strip().rstrip("/")
    return base or None


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _h(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


@dataclass
class Session:
    kind: str                                # "oidc" | "demo"
    principal: str                           # principal_id (oidc) or the fixture's principal (demo)
    tenant_id: str | None
    created: float
    seen: float
    provider: str | None = None
    meta: dict = field(default_factory=dict)


class Store:
    """Pending logins and sessions (process memory; a restart signs everyone out, which fails closed)."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self._pending: dict[str, dict] = {}
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    # ---- pending logins (one-time)
    def begin(self, provider: str, invite: str | None = None) -> tuple[str, str, str, str]:
        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        verifier, challenge = pkce_pair()
        with self._lock:
            self._gc()
            self._pending[_h(state)] = {"provider": provider, "nonce": nonce, "verifier": verifier,
                                        "invite": invite, "at": self.clock()}
        return state, nonce, verifier, challenge

    def take(self, state: str | None) -> dict | None:
        if not state:
            return None
        with self._lock:
            p = self._pending.pop(_h(state), None)
        if p is None or self.clock() - p["at"] > PENDING_TTL:
            return None
        return p

    # ---- sessions
    def create(self, kind: str, principal: str, tenant_id: str | None, provider: str | None = None,
               meta: dict | None = None) -> str:
        sid = secrets.token_urlsafe(32)
        now = self.clock()
        with self._lock:
            self._gc()
            self._sessions[_h(sid)] = Session(kind, principal, tenant_id, now, now, provider, meta or {})
        return sid

    def get(self, sid: str | None) -> Session | None:
        if not sid:
            return None
        now = self.clock()
        with self._lock:
            s = self._sessions.get(_h(sid))
            if s is None:
                return None
            if now - s.created > ABSOLUTE_TTL or now - s.seen > IDLE_TTL:
                del self._sessions[_h(sid)]
                return None
            s.seen = now
            return s

    def expires_at(self, s: Session) -> float:
        return min(s.created + ABSOLUTE_TTL, s.seen + IDLE_TTL)

    def end(self, sid: str | None) -> None:
        if sid:
            with self._lock:
                self._sessions.pop(_h(sid), None)

    def _gc(self) -> None:
        now = self.clock()
        for k in [k for k, p in self._pending.items() if now - p["at"] > PENDING_TTL]:
            del self._pending[k]
        for k in [k for k, s in self._sessions.items()
                  if now - s.created > ABSOLUTE_TTL or now - s.seen > IDLE_TTL]:
            del self._sessions[k]


def authorize_url(endpoint: str, p: Provider, redirect_uri: str, state: str, nonce: str, challenge: str) -> str:
    q = {"response_type": "code", "client_id": p.client_id, "redirect_uri": redirect_uri, "scope": p.scopes,
         "state": state, "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256",
         **dict(p.extra)}
    return f"{endpoint}?{urlencode(q)}"


def is_loopback(host: str | None) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def local_host_header(host: str | None) -> bool:
    name = (host or "").rsplit(":", 1)[0].strip("[]").lower()
    return name in ("localhost", "127.0.0.1", "::1")


def mfa_required(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return env.get("VELOXA_SSO_REQUIRE_MFA", "").strip() == "1"


def demo_enabled(auth_mode: str, env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return auth_mode == "development_fixture" and env.get("VELOXA_DEMO_LOGIN", "").strip() == "1"
