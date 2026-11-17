"""Per-tenant, customer-owned OIDC sign-in (C02; owner recommendation OD-E, NOT yet an approved pilot issuer).

**Provider-neutral.** No identity provider is hard-coded. Each tenant registers its own trusted issuers
(`veloxa tenant add-issuer`: issuer, audience, JWKS URL, algorithms).

**For every bearer token:**
1. Read the `iss` claim (unverified, only to find the configuration).
2. Look the issuer up through `directory.issuer_config` (an exact URL match; inactive issuers are refused).
3. Verify the signature with that issuer's JWKS (rotation, rate-limited refetch, bounded staleness; `jwks.py`).
   Then check the issuer, audience, expiry, not-before and issued-at (`identity.verify_signed_claims`).
4. Map `(issuer, sub)` to a workspace principal through an exact binding created when an invitation was
   accepted. **Membership never comes from an e-mail address or domain.**
5. Require the principal's tenant to equal the issuer's tenant, and the principal to be ACTIVE.

Every failure is a rejection with a reason code. Nothing falls back to "trust the token".
"""

from __future__ import annotations

import base64
import json
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import psycopg

from .identity import KeySource, Rejected, verify_signed_claims
from .jwks import JwksCache, https_fetch, jwks_keys


@dataclass(frozen=True)
class SignIn:
    tenant_id: uuid.UUID
    principal_id: str
    issuer: str
    subject: str


@dataclass(frozen=True)
class VerifiedSubject:
    tenant_id: uuid.UUID
    issuer: str
    subject: str
    email: str | None


def _unverified_issuer(token: str) -> str | None:
    try:
        body = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (IndexError, ValueError, TypeError):
        return None
    iss = claims.get("iss") if isinstance(claims, dict) else None
    return iss if isinstance(iss, str) and iss.startswith("https://") else None


class OidcAuthenticator:
    def __init__(self, fetcher: Callable[[str], Callable[[], dict]] | None = None,
                 clock: Callable[[], float] | None = None):
        self._fetcher, self._clock = fetcher or (lambda url: https_fetch(url)), clock
        self._caches: dict[str, JwksCache] = {}
        self._lock = threading.Lock()

    def _keys(self, issuer: str, audience: str, jwks_url: str, algorithms: list[str]) -> KeySource:
        with self._lock:
            cache = self._caches.get(jwks_url)
            if cache is None:
                kw = {"clock": self._clock} if self._clock else {}
                cache = self._caches[jwks_url] = JwksCache(self._fetcher(jwks_url), **kw)
        ks = jwks_keys(issuer, audience, cache)
        return KeySource(ks.issuer, ks.audience, frozenset(algorithms) & ks.algorithms, ks.verify, ks.verify_header)

    def verify_subject(self, conn: psycopg.Connection, token: str | None, now: datetime) -> VerifiedSubject | Rejected:
        """A cryptographically verified (tenant, issuer, subject), whether or not it is a member yet."""
        iss = _unverified_issuer(token or "")
        if iss is None:
            return Rejected("malformed")
        with conn.transaction():
            rows = conn.execute("SELECT * FROM directory.issuer_config(%s)", (iss,)).fetchall()
        if len(rows) != 1:
            return Rejected("unknown_issuer")
        cfg = rows[0]
        claims = verify_signed_claims(token, self._keys(iss, cfg["audience"], cfg["jwks_url"], cfg["algorithms"]),
                                      now)
        if isinstance(claims, Rejected):
            return claims
        sub = claims.get("sub")
        if not isinstance(sub, str) or not 1 <= len(sub) <= 255:
            return Rejected("no_subject")
        email = claims.get("email") if isinstance(claims.get("email"), str) else None
        return VerifiedSubject(cfg["tenant_id"], iss, sub, email)

    def authenticate(self, conn: psycopg.Connection, token: str | None, now: datetime) -> SignIn | Rejected:
        v = self.verify_subject(conn, token, now)
        if isinstance(v, Rejected):
            return v
        with conn.transaction():
            row = conn.execute("SELECT * FROM directory.principal_for_subject(%s, %s)",
                               (v.issuer, v.subject)).fetchone()
        if row is None:
            return Rejected("not_a_member")
        if row["tenant_id"] != v.tenant_id:
            return Rejected("wrong_tenant")
        return SignIn(v.tenant_id, row["principal_id"], v.issuer, v.subject)
