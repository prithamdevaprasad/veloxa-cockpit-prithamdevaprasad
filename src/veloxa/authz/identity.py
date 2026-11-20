"""C02 identity: provider-neutral token validation (ARCHITECTURE.md §10.3, §19; C02).

The production identity provider (OIDC/SAML with SCIM) is NOT selected or integrated: that is an owner choice.
This module is the provider-neutral contract the gateway will use, plus a deterministic HS256 verifier for tests.
A production deployment supplies a `KeySource` for the chosen IdP (asymmetric keys from its JWKS); until then the
API keeps its existing fail-closed behaviour and production authentication stays incomplete.

Validation (every failure is a rejection with a reason code, never a partial identity):
- a compact JWS with exactly three parts and an algorithm the key source allows ('none' is always refused);
- signature, issuer, audience, expiry (with a bounded clock skew), not-before and issued-at;
- a tenant claim equal to the tenant being served (no cross-tenant tokens);
- a subject that is a typed Veloxa principal (`person:<id>`), never an e-mail address or a display title;
- a token identifier (jti) that is not revoked (policy.revoked_token).
Every decision is audited (principal and reason only; never the token).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

import psycopg

from .. import audit
from ..db import tenant_tx

MAX_SKEW = timedelta(seconds=60)


@dataclass(frozen=True)
class Identity:
    principal_id: str
    tenant_id: uuid.UUID
    issuer: str
    expires_at: datetime
    jti: str


@dataclass(frozen=True)
class Rejected:
    reason: str


@dataclass(frozen=True)
class KeySource:
    """What the configured IdP allows: issuer, audience, algorithms and a signature verifier."""
    issuer: str
    audience: str
    algorithms: frozenset[str]
    verify: Callable[[str, bytes, bytes], bool]   # (alg, signing input, signature) -> valid
    # Optional: a verifier that needs the JOSE header (e.g. `kid` for JWKS rotation, authz/jwks.py). Takes precedence.
    verify_header: Callable[[dict, bytes, bytes], bool] | None = None


def hs256_keys(issuer: str, audience: str, secret: bytes) -> KeySource:
    """A deterministic TEST key source (shared secret). Never a production identity provider."""
    if len(secret) < 32:
        raise ValueError("test secret must be at least 32 bytes")

    def verify(alg: str, signing_input: bytes, signature: bytes) -> bool:
        return alg == "HS256" and hmac.compare_digest(
            hmac.new(secret, signing_input, hashlib.sha256).digest(), signature)
    return KeySource(issuer, audience, frozenset({"HS256"}), verify)


def _b64(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def validate(conn: psycopg.Connection, token: str | None, tenant_id: uuid.UUID, keys: KeySource,
             now: datetime) -> Identity | Rejected:
    result = _validate(conn, token, tenant_id, keys, now)
    with tenant_tx(conn, tenant_id):
        audit.append(conn, tenant_id, "auth.accepted" if isinstance(result, Identity) else "auth.rejected",
                     {"principal": result.principal_id} if isinstance(result, Identity) else {"reason": result.reason})
    return result


def verify_signed_claims(token: str | None, keys: KeySource, now: datetime) -> dict | Rejected:
    """The provider-neutral part of validation: compact JWS, allowed algorithm, signature, issuer, audience,
    expiry, not-before and issued-at (bounded skew). Returns the verified claims, or a rejection reason."""
    if not token or token.count(".") != 2:
        return Rejected("malformed")
    head_b64, body_b64, sig_b64 = token.split(".")
    try:
        header, claims, sig = json.loads(_b64(head_b64)), json.loads(_b64(body_b64)), _b64(sig_b64)
    except (ValueError, TypeError):
        return Rejected("malformed")
    alg = header.get("alg") if isinstance(header, dict) else None
    if not isinstance(claims, dict) or alg in (None, "none") or alg not in keys.algorithms:
        return Rejected("algorithm_not_allowed")
    signing_input = f"{head_b64}.{body_b64}".encode()
    ok = (keys.verify_header(header, signing_input, sig) if keys.verify_header is not None
          else keys.verify(alg, signing_input, sig))
    if not ok:
        return Rejected("bad_signature")
    if claims.get("iss") != keys.issuer:
        return Rejected("wrong_issuer")
    aud = claims.get("aud")
    if keys.audience not in (aud if isinstance(aud, list) else [aud]):
        return Rejected("wrong_audience")
    try:
        exp, nbf, iat = (datetime.fromtimestamp(int(claims[k]), now.tzinfo) if k in claims else None
                         for k in ("exp", "nbf", "iat"))
    except (TypeError, ValueError):
        return Rejected("malformed")
    if exp is None or now > exp + MAX_SKEW:
        return Rejected("expired")
    if (nbf and now + MAX_SKEW < nbf) or (iat and now + MAX_SKEW < iat):
        return Rejected("not_yet_valid")
    return claims | {"_exp": exp}


def _validate(conn, token, tenant_id, keys, now) -> Identity | Rejected:
    claims = verify_signed_claims(token, keys, now)
    if isinstance(claims, Rejected):
        return claims
    exp = claims["_exp"]
    if claims.get("tenant") != str(tenant_id):
        return Rejected("wrong_tenant")
    sub, jti = claims.get("sub"), claims.get("jti")
    if not isinstance(sub, str) or not sub.startswith("person:") or "@" in sub:
        return Rejected("subject_not_a_principal")
    if not isinstance(jti, str) or not 8 <= len(jti) <= 200:
        return Rejected("no_token_id")
    with tenant_tx(conn, tenant_id):
        if conn.execute("SELECT 1 FROM policy.revoked_token WHERE tenant_id = %s AND jti = %s",
                        (tenant_id, jti)).fetchone():
            return Rejected("revoked")
    return Identity(sub, tenant_id, keys.issuer, exp, jti)
