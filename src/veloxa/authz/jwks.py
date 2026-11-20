"""C02 JWKS key source with rotation (ARCHITECTURE.md §10.3; C02). Provider-neutral; NO IdP is selected or wired.

A production deployment points this at the chosen IdP's JWKS endpoint (owner decision OD-E). Until then nothing in
the API uses it, and requester authentication stays fail closed (`auth.py`).

**Rotation behaviour:**
- Keys are cached by `kid`. A token whose `kid` is unknown triggers one refetch, rate-limited to one per
  `min_refresh` seconds, so a flood of unknown `kid`s cannot hammer the IdP. A key the IdP has rotated out is
  dropped at the next fetch.
- The cache is refreshed after `ttl`. If a refresh fails, the last good key set is used until `max_stale`, then
  every token is rejected (fail closed). It never falls back to "no verification".

**Accepted keys and tokens:**
- RS256 (RSA, at least 2048 bits) and ES256 (P-256) only.
- A JWK with private parameters, `use` other than `sig`, or an `alg` that disagrees with the token is refused.
- A token without a `kid` is refused when the set holds more than one key.

`https_fetch` is a small fetcher (HTTPS only, timeout, size cap). Any fetcher returning the parsed JWKS dict can
be supplied instead, for example the hosting platform's client.
"""

from __future__ import annotations

import base64
import json
import threading
import time
import urllib.request
from collections.abc import Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from .identity import KeySource

ALGORITHMS = frozenset({"RS256", "ES256"})
_PRIVATE = {"d", "p", "q", "dp", "dq", "qi", "k"}
MAX_JWKS_BYTES = 256 * 1024


class JwksError(Exception):
    pass


def _b64int(v: str) -> int:
    return int.from_bytes(base64.urlsafe_b64decode(v + "=" * (-len(v) % 4)), "big")


def _load(jwk: dict):
    """(alg, public key) for an acceptable JWK, else None (skipped, never trusted)."""
    if not isinstance(jwk, dict) or _PRIVATE & jwk.keys() or jwk.get("use", "sig") != "sig":
        return None
    try:
        if jwk.get("kty") == "RSA" and jwk.get("alg", "RS256") == "RS256":
            key = rsa.RSAPublicNumbers(_b64int(jwk["e"]), _b64int(jwk["n"])).public_key()
            return ("RS256", key) if key.key_size >= 2048 else None
        if jwk.get("kty") == "EC" and jwk.get("crv") == "P-256" and jwk.get("alg", "ES256") == "ES256":
            return "ES256", ec.EllipticCurvePublicNumbers(_b64int(jwk["x"]), _b64int(jwk["y"]),
                                                          ec.SECP256R1()).public_key()
    except (KeyError, ValueError, TypeError):
        return None
    return None


class JwksCache:
    def __init__(self, fetch: Callable[[], dict], ttl: float = 3600, min_refresh: float = 60,
                 max_stale: float = 86400, clock: Callable[[], float] = time.monotonic):
        self._fetch, self.ttl, self.min_refresh, self.max_stale, self._clock = fetch, ttl, min_refresh, max_stale, clock
        self._lock = threading.Lock()
        self._keys: dict[str, tuple[str, object]] = {}
        self._fetched_at: float | None = None          # last SUCCESSFUL fetch
        self._attempted_at: float | None = None
        self.fetches = 0

    def _refresh(self, now: float) -> None:
        self._attempted_at = now
        self.fetches += 1
        try:
            doc = self._fetch()
            keys = {}
            for jwk in doc.get("keys", []) if isinstance(doc, dict) else []:
                loaded = _load(jwk)
                if loaded and isinstance(jwk.get("kid"), str) and jwk["kid"]:
                    keys[jwk["kid"]] = loaded
        except Exception:                               # noqa: BLE001 - any fetch failure keeps the last good set
            return
        if keys:
            self._keys, self._fetched_at = keys, now

    def key_for(self, kid: str | None):
        now = self._clock()
        with self._lock:
            stale = self._fetched_at is None or now - self._fetched_at >= self.ttl
            unknown = kid is not None and kid not in self._keys
            may_retry = self._attempted_at is None or now - self._attempted_at >= self.min_refresh
            if (stale or unknown) and may_retry:
                self._refresh(now)
            if self._fetched_at is None or now - self._fetched_at >= self.max_stale:
                return None                              # never fetched, or too stale: fail closed
            if kid is None:
                return next(iter(self._keys.values())) if len(self._keys) == 1 else None
            return self._keys.get(kid)


def _verify(found, alg: str, signing_input: bytes, sig: bytes) -> bool:
    if found is None or found[0] != alg:
        return False
    try:
        if alg == "RS256":
            found[1].verify(sig, signing_input, padding.PKCS1v15(), hashes.SHA256())
        else:
            if len(sig) != 64:
                return False
            der = encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big"))
            found[1].verify(der, signing_input, ec.ECDSA(hashes.SHA256()))
        return True
    except InvalidSignature:
        return False


def jwks_keys(issuer: str, audience: str, cache: JwksCache) -> KeySource:
    """A C02 KeySource backed by a rotating JWKS cache (asymmetric keys only)."""
    def verify_header(header: dict, signing_input: bytes, sig: bytes) -> bool:
        kid = header.get("kid")
        if kid is not None and not isinstance(kid, str):
            return False
        return _verify(cache.key_for(kid), header.get("alg"), signing_input, sig)

    return KeySource(issuer, audience, ALGORITHMS, lambda *_: False, verify_header=verify_header)


def https_fetch(url: str, timeout: float = 5.0) -> Callable[[], dict]:
    if not url.startswith("https://"):
        raise JwksError("the JWKS URL must use https")

    def fetch() -> dict:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})  # noqa: S310 - https only
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - scheme checked above (https only)
            body = r.read(MAX_JWKS_BYTES + 1)
        if len(body) > MAX_JWKS_BYTES:
            raise JwksError("JWKS document too large")
        return json.loads(body)
    return fetch
