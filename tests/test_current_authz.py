"""Current authorization: C02 token validation, C20 ReBAC tuples, C21 ABAC policy, and their separation from C32
historical evaluation. SYNTHETIC principals, tokens and tuples only (the test HS256 key source is not an IdP)."""

import base64
import hashlib
import hmac
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest

from veloxa.authz import current, identity
from veloxa.authz.current import Resource, abac, authorize
from veloxa.db import tenant_tx

pytestmark = pytest.mark.db
SECRET = b"synthetic-test-signing-secret-0123456789"
KEYS = identity.hs256_keys("https://idp.example.test", "veloxa-api", SECRET)
NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def token(tenant, alg="HS256", key=SECRET, **claims):
    body = {"iss": "https://idp.example.test", "aud": "veloxa-api", "sub": "person:T001", "tenant": str(tenant),
            "exp": int((NOW + timedelta(minutes=5)).timestamp()), "iat": int(NOW.timestamp()),
            "jti": "jti-0000001"} | claims
    head = b64(json.dumps({"alg": alg, "typ": "JWT"}).encode())
    payload = b64(json.dumps({k: v for k, v in body.items() if v is not None}).encode())
    sig = b64(hmac.new(key, f"{head}.{payload}".encode(), hashlib.sha256).digest()) if alg != "none" else ""
    return f"{head}.{payload}.{sig}"


def admin_insert(db, tenant, sql, params):
    with psycopg.connect(db.admin_dsn, autocommit=True) as admin:
        admin.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant),))
        admin.execute(sql, params)


def tuple_(db, tenant, rtype, rid, rel, stype, sid, srel=None):
    tid = uuid.uuid4()
    admin_insert(db, tenant, "INSERT INTO policy.relationship_tuple (tenant_id, tuple_id, resource_type, resource_id,"
                 " relation, subject_type, subject_id, subject_relation, source) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,"
                 "'synthetic test')", (tenant, tid, rtype, rid, rel, stype, sid, srel))
    return tid


# ---------------------------------------------------------------------------------------------- C02 identity


def audit_events(db, tenant):
    with psycopg.connect(db.admin_dsn, autocommit=True) as admin:   # the app role can append audit, never read it
        return admin.execute("SELECT kind, detail FROM audit.event WHERE tenant_id = %s AND kind LIKE 'auth.%%'"
                             " ORDER BY seq", (tenant,)).fetchall()


def test_a_valid_token_yields_a_typed_principal_and_is_audited(db, app_conn, tenant):
    r = identity.validate(app_conn, token(tenant), tenant, KEYS, NOW)
    assert isinstance(r, identity.Identity) and r.principal_id == "person:T001" and r.tenant_id == tenant
    (e,) = [{"kind": k, "detail": d} for k, d in audit_events(db, tenant)]
    assert e["kind"] == "auth.accepted" and e["detail"] == {"principal": "person:T001"}


@pytest.mark.parametrize("claims,reason", [
    ({"exp": int((NOW - timedelta(minutes=5)).timestamp())}, "expired"),
    ({"nbf": int((NOW + timedelta(minutes=5)).timestamp())}, "not_yet_valid"),
    ({"iss": "https://evil.example.test"}, "wrong_issuer"),
    ({"aud": "someone-else"}, "wrong_audience"),
    ({"sub": "ceo@example.test"}, "subject_not_a_principal"),
    ({"sub": "CEO"}, "subject_not_a_principal"),
    ({"jti": None}, "no_token_id"),
    ({"exp": None}, "expired"),
])
def test_invalid_claims_are_rejected_with_a_reason(app_conn, tenant, claims, reason):
    assert identity.validate(app_conn, token(tenant, **claims), tenant, KEYS, NOW) == identity.Rejected(reason)


def test_signature_algorithm_tenant_and_revocation_are_enforced(db, app_conn, tenant):
    assert identity.validate(app_conn, token(tenant, key=b"x" * 40), tenant, KEYS, NOW).reason == "bad_signature"
    assert identity.validate(app_conn, token(tenant, alg="none"), tenant, KEYS, NOW).reason == "algorithm_not_allowed"
    assert identity.validate(app_conn, token(uuid.uuid4()), tenant, KEYS, NOW).reason == "wrong_tenant"
    for junk in (None, "", "a.b", "not.a.token.at.all"):
        assert identity.validate(app_conn, junk, tenant, KEYS, NOW).reason == "malformed"
    admin_insert(db, tenant, "INSERT INTO policy.revoked_token (tenant_id, jti, reason) VALUES (%s, %s, 'test')",
                 (tenant, "jti-0000001"))
    assert identity.validate(app_conn, token(tenant), tenant, KEYS, NOW).reason == "revoked"
    details = [d for _, d in audit_events(db, tenant)]
    assert details and not any("eyJ" in json.dumps(d) for d in details)              # no token in the audit
    assert {"reason": "revoked"} in details
    with pytest.raises(ValueError):
        identity.hs256_keys("i", "a", b"short")


# ---------------------------------------------------------------------------------------------- C20 ReBAC


def test_direct_group_and_revoked_relationships(db, app_conn, tenant):
    store = current.RelationshipStore(app_conn)
    tuple_(db, tenant, "document", "doc-1", "read", "person", "T001")
    tuple_(db, tenant, "document", "doc-2", "read", "group", "finance", "member")
    t = tuple_(db, tenant, "group", "finance", "member", "person", "T002")
    assert store.check(tenant, "person:T001", "read", "document", "doc-1")[0]
    ok, path = store.check(tenant, "person:T002", "read", "document", "doc-2")
    assert ok and len(path) == 2
    assert not store.check(tenant, "person:T002", "read", "document", "doc-1")[0]
    admin_insert(db, tenant, "INSERT INTO policy.relationship_revocation (tenant_id, tuple_id) VALUES (%s, %s)",
                 (tenant, t))
    assert not store.check(tenant, "person:T002", "read", "document", "doc-2")[0]      # revocation is immediate


def test_group_cycles_terminate_and_tenants_never_mix(db, app_conn, tenant):
    tuple_(db, tenant, "group", "a", "member", "group", "b", "member")
    tuple_(db, tenant, "group", "b", "member", "group", "a", "member")
    tuple_(db, tenant, "document", "doc-c", "read", "group", "a", "member")
    assert not current.RelationshipStore(app_conn).check(tenant, "person:T009", "read", "document", "doc-c")[0]
    other = uuid.uuid4()
    with psycopg.connect(db.admin_dsn, autocommit=True) as admin:
        admin.execute("INSERT INTO directory.tenant (tenant_id, slug, synthetic, time_zone) VALUES (%s, %s, true,"
                      " 'UTC')", (other, f"t-{other.hex[:12]}"))
    tuple_(db, tenant, "document", "doc-x", "read", "person", "T001")
    assert not current.RelationshipStore(app_conn).check(other, "person:T001", "read", "document", "doc-x")[0]
    with pytest.raises(psycopg.errors.InsufficientPrivilege), tenant_tx(app_conn, tenant):
        app_conn.execute("INSERT INTO policy.relationship_tuple (tenant_id, tuple_id, resource_type, resource_id,"
                         " relation, subject_type, subject_id, source) VALUES (%s, %s, 'document', 'd', 'read',"
                         " 'person', 'T001', 'api')", (tenant, uuid.uuid4()))       # the API cannot grant


# ---------------------------------------------------------------------------------------------- C21 ABAC


def test_abac_denies_on_missing_or_unknown_attributes_and_protects_pay():
    t = uuid.uuid4()
    doc = Resource("document", "d", t, "confidential")
    assert abac(t, "read", doc, "answer", True).allowed
    assert abac(t, "read", doc, None, True).reason == "purpose_missing_or_undeclared"
    assert abac(t, "read", doc, "gossip", True).reason == "purpose_missing_or_undeclared"
    assert abac(t, "read", Resource("document", "d", t, None), "answer", True).reason == "data_class_unknown"
    assert abac(t, "read", Resource("document", "d", t, "made_up"), "answer", True).reason == "data_class_unknown"
    assert abac(t, "read", Resource("document", "d", uuid.uuid4(), "internal"), "answer", True).reason == \
        "tenant_mismatch"
    assert abac(t, "read", doc, "answer", False).reason == "no_relationship"            # ReBAC AND ABAC
    pay = Resource("pay", "person:T002", t, "sensitive_personal")
    assert abac(t, "read", pay, "answer", True).reason == "protected_class"
    assert abac(t, "read_value", pay, "answer", True).reason == "protected_value_only_as_disclosure_background"
    assert abac(t, "read_value", pay, "disclosure_background", True).allowed
    assert abac(t, "read", doc, "answer", True).policy_version == "abac/1"


def test_authorize_requires_a_typed_principal_and_fails_closed(app_conn, tenant, monkeypatch):
    doc = Resource("document", "d", tenant, "internal")
    for p in (None, "", "user:x", "CEO"):
        assert authorize(app_conn, p, tenant, "read", doc, "answer").reason == "principal_unresolved"

    def broken(*a, **k):
        raise RuntimeError("store down")

    monkeypatch.setattr(current.RelationshipStore, "check", broken)
    assert authorize(app_conn, "person:T001", tenant, "read", doc, "answer").reason == "rebac_unavailable"


def test_current_and_historical_authorization_never_reference_each_other():
    root = Path(current.__file__).resolve().parents[1]
    cur = (root / "authz" / "current.py").read_text(encoding="utf-8")
    hist = (root / "acl" / "historical.py").read_text(encoding="utf-8")
    assert not re.search(r"acl\.historical|HistoricalAcl", cur)
    assert not re.search(r"from \.\.authz|authz\.current", hist)
