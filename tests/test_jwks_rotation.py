"""C02 JWKS key source: rotation, refetch rate limit, staleness fail-closed, key-type hygiene.

Keys are generated in-test (synthetic); no identity provider is contacted.
"""

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from veloxa.authz import identity
from veloxa.authz.jwks import JwksCache, JwksError, https_fetch, jwks_keys

ISS, AUD = "https://idp.example.test", "veloxa-api"
NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
RSA_A = rsa.generate_private_key(public_exponent=65537, key_size=2048)
RSA_B = rsa.generate_private_key(public_exponent=65537, key_size=2048)
EC_C = ec.generate_private_key(ec.SECP256R1())


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def b64int(n: int, size: int | None = None) -> str:
    return b64(n.to_bytes(size or (n.bit_length() + 7) // 8, "big"))


def jwk(key, kid):
    pub = key.public_key().public_numbers()
    if isinstance(key, rsa.RSAPrivateKey):
        return {"kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256", "n": b64int(pub.n), "e": b64int(pub.e)}
    return {"kty": "EC", "kid": kid, "crv": "P-256", "alg": "ES256", "x": b64int(pub.x, 32), "y": b64int(pub.y, 32)}


def token(key, kid, tenant, alg=None, **claims):
    alg = alg or ("RS256" if isinstance(key, rsa.RSAPrivateKey) else "ES256")
    body = {"iss": ISS, "aud": AUD, "sub": "person:T001", "tenant": str(tenant), "jti": "jti-0000001",
            "exp": int((NOW + timedelta(minutes=5)).timestamp()), "iat": int(NOW.timestamp())} | claims
    head = {"alg": alg, "typ": "JWT"} | ({"kid": kid} if kid else {})
    si = f"{b64(json.dumps(head).encode())}.{b64(json.dumps(body).encode())}".encode()
    if alg == "RS256":
        sig = key.sign(si, padding.PKCS1v15(), hashes.SHA256())
    else:
        r, s = decode_dss_signature(key.sign(si, ec.ECDSA(hashes.SHA256())))
        sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return f"{si.decode()}.{b64(sig)}"


class Idp:
    """A synthetic JWKS endpoint whose key set the test rotates."""
    def __init__(self, *keys):
        self.keys, self.fail, self.calls = list(keys), False, 0

    def __call__(self):
        self.calls += 1
        if self.fail:
            raise OSError("synthetic IdP outage")
        return {"keys": self.keys}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.mark.db
def test_rotation_accepts_the_new_key_and_drops_the_retired_one(app_conn, tenant):
    idp, clock = Idp(jwk(RSA_A, "a")), Clock()
    keys = jwks_keys(ISS, AUD, JwksCache(idp, ttl=3600, min_refresh=60, clock=clock))
    assert isinstance(identity.validate(app_conn, token(RSA_A, "a", tenant), tenant, keys, NOW), identity.Identity)
    idp.keys = [jwk(RSA_A, "a"), jwk(RSA_B, "b"), jwk(EC_C, "c")]          # the IdP publishes new keys
    clock.t += 61
    assert isinstance(identity.validate(app_conn, token(RSA_B, "b", tenant), tenant, keys, NOW), identity.Identity)
    assert isinstance(identity.validate(app_conn, token(EC_C, "c", tenant), tenant, keys, NOW), identity.Identity)
    idp.keys = [jwk(RSA_B, "b")]                                         # "a" retired
    clock.t += 3601
    assert identity.validate(app_conn, token(RSA_A, "a", tenant), tenant, keys, NOW).reason == "bad_signature"


@pytest.mark.db
def test_signature_kid_and_algorithm_confusion_are_rejected(app_conn, tenant):
    keys = jwks_keys(ISS, AUD, JwksCache(Idp(jwk(RSA_A, "a"), jwk(RSA_B, "b")), clock=Clock()))
    v = lambda t: identity.validate(app_conn, t, tenant, keys, NOW)   # noqa: E731
    assert v(token(RSA_B, "a", tenant)).reason == "bad_signature"          # signed by b, claims kid a
    assert v(token(RSA_A, None, tenant)).reason == "bad_signature"         # no kid with several keys
    assert v(token(EC_C, "a", tenant)).reason == "bad_signature"           # ES256 against an RSA key
    hs = token(RSA_A, "a", tenant).split(".")
    head = b64(json.dumps({"alg": "HS256", "kid": "a"}).encode())
    assert v(f"{head}.{hs[1]}.{hs[2]}").reason == "algorithm_not_allowed"  # HS256 never accepted from a JWKS
    assert v(token(RSA_A, "a", tenant, iss="https://evil.example")).reason == "wrong_issuer"


def test_unknown_kid_refetch_is_rate_limited():
    idp, clock = Idp(jwk(RSA_A, "a")), Clock()
    cache = JwksCache(idp, ttl=3600, min_refresh=60, clock=clock)
    assert cache.key_for("a") is not None and idp.calls == 1
    for _ in range(50):                                                     # a flood of unknown kids
        assert cache.key_for("unknown") is None
    assert idp.calls == 1
    clock.t += 60
    cache.key_for("unknown")
    assert idp.calls == 2


def test_outage_uses_last_good_keys_until_max_stale_then_fails_closed():
    idp, clock = Idp(jwk(RSA_A, "a")), Clock()
    cache = JwksCache(idp, ttl=100, min_refresh=10, max_stale=1000, clock=clock)
    assert cache.key_for("a") is not None
    idp.fail = True
    clock.t += 500
    assert cache.key_for("a") is not None                                  # stale but within max_stale
    clock.t += 600
    assert cache.key_for("a") is None                                      # too stale: fail closed
    assert JwksCache(Idp(), clock=Clock()).key_for("a") is None             # never fetched: fail closed


def test_unsafe_or_weak_jwks_entries_are_never_trusted():
    weak = rsa.generate_private_key(public_exponent=65537, key_size=1024)  # noqa: S505 - must be rejected
    private = jwk(RSA_A, "p") | {"d": "AQAB"}
    enc = jwk(RSA_A, "e") | {"use": "enc"}
    hmac_key = {"kty": "oct", "kid": "o", "k": b64(b"x" * 32)}
    cache = JwksCache(Idp(jwk(weak, "w"), private, enc, hmac_key, jwk(RSA_B, "ok")), clock=Clock())
    assert [k for k in ("w", "p", "e", "o", "ok") if cache.key_for(k)] == ["ok"]


def test_fetcher_requires_https():
    with pytest.raises(JwksError):
        https_fetch("http://idp.example.test/jwks")
    assert callable(https_fetch("https://idp.example.test/jwks"))           # constructed only; never called here
