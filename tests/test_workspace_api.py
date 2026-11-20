"""Workspace API over HTTP: the development fixture in a SYNTHETIC workspace, per-tenant OIDC sign-in with
synthetic keys, invitations, administration and refusals. No real identity provider is contacted."""

import base64
import json
import uuid

import pytest
from conftest import API_CLOCK, DEV_TOKEN, api_client
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from veloxa import tenancy
from veloxa.db import connect

pytestmark = pytest.mark.db
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
H = {"Authorization": f"Bearer {DEV_TOKEN}"}


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def jwks():
    n = KEY.public_key().public_numbers()
    return {"keys": [{"kty": "RSA", "kid": "k1", "use": "sig", "alg": "RS256",
                      "n": b64(n.n.to_bytes((n.n.bit_length() + 7) // 8, "big")), "e": b64(n.e.to_bytes(3, "big"))}]}


def id_token(iss, sub, aud="veloxa", exp_in=300, **extra):
    head = b64(json.dumps({"alg": "RS256", "kid": "k1", "typ": "JWT"}).encode())
    now = int(API_CLOCK.timestamp())
    body = b64(json.dumps({"iss": iss, "sub": sub, "aud": aud, "iat": now,
                           "exp": now + exp_in, **extra}).encode())
    sig = KEY.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{body}.{b64(sig)}"


@pytest.fixture
def synth(db):
    slug = f"syn-{uuid.uuid4().hex[:8]}"
    with connect(db.admin_dsn) as a:
        tid = tenancy.create_tenant(a, slug, "Asia/Kolkata", "Synthetic Workspace", synthetic=True)
        tenancy.bootstrap_synthetic_admin(a, tid, "person:syn-admin", "Synthetic Admin")
        tenancy.add_synthetic_member(a, tid, "person:syn-user", "Synthetic User")
        iss = f"https://idp.example.test/{slug}"
        tenancy.add_issuer(a, tid, iss, "veloxa", f"{iss}/jwks")
    return {"slug": slug, "tenant": tid, "iss": iss}


def _fixture(slug, principal):
    return {"VELOXA_DEV_AUTH": "1", "VELOXA_DEV_TOKEN": DEV_TOKEN, "VELOXA_DEV_PRINCIPAL": principal,
            "VELOXA_DEV_TENANT": slug}


def test_me_and_administration_in_a_synthetic_workspace(db, monkeypatch, synth):
    with api_client(db, monkeypatch, **_fixture(synth["slug"], "person:syn-admin")) as c:
        me = c.get("/v1/me", headers=H).json()
        assert me["tenant"]["slug"] == synth["slug"] and me["tenant"]["evidence_access"] == "rebac"
        assert any(r["relation"] == "admin" for r in me["relations"]) and me["access"]["mode"] == "rebac"
        assert c.post("/v1/admin/departments", json={"department_id": "finance", "name": "Finance"},
                      headers=H).status_code == 200
        inv = c.post("/v1/admin/invitations", headers=H, json={
            "email": "fin.head@example.test", "display_name": "Fin Head",
            "roles": [{"resource_type": "department", "resource_id": "finance", "relation": "head"}]}).json()
        assert inv["invitation_token"] and inv["delivery"].startswith("not sent")
        g = c.post("/v1/admin/grants", headers=H, json={
            "resource_type": "evidence_source", "resource_id": "zoho_books", "relation": "read",
            "subject": "department:finance", "subject_relation": "member", "reason": "finance owns the ledger"})
        assert g.status_code == 200
        selfish = c.post("/v1/admin/grants", headers=H, json={
            "resource_type": "tenant", "resource_id": str(synth["tenant"]), "relation": "company_reader",
            "subject": "person:syn-admin", "reason": "x"})
        assert selfish.status_code == 403
        members = c.get("/v1/admin/members", headers=H).json()["members"]
        assert {m["principal_id"] for m in members} >= {"person:syn-admin", "person:syn-user", inv["principal_id"]}
        assert c.get("/v1/departments", headers=H).json()["departments"][0]["department_id"] == "finance"


def test_a_non_admin_is_refused_by_the_database_not_just_the_api(db, monkeypatch, synth):
    with api_client(db, monkeypatch, **_fixture(synth["slug"], "person:syn-user")) as c:
        assert c.get("/v1/me", headers=H).json()["access"]["sources"] == []
        assert c.get("/v1/admin/members", headers=H).status_code == 403
        r = c.post("/v1/admin/departments", json={"department_id": "ops", "name": "Ops"}, headers=H)
        assert r.status_code == 403 and r.json()["error"] == "not_permitted"


def test_the_fixture_never_signs_into_a_real_workspace(db, monkeypatch):
    slug = f"real-{uuid.uuid4().hex[:8]}"
    with connect(db.admin_dsn) as a:
        tenancy.create_tenant(a, slug, "UTC", "Real Co", synthetic=False)
    with api_client(db, monkeypatch, **_fixture(slug, "person:anyone")) as c:
        assert c.get("/v1/me", headers=H).status_code == 403


def test_oidc_invitation_sign_in_and_refusals(db, monkeypatch, synth):
    import veloxa.authz.oidc as oidc

    monkeypatch.setattr(oidc, "https_fetch", lambda url: (lambda: jwks()))
    with connect(db.dsn("app")) as app:
        pid, token = tenancy.invite(app, synth["tenant"], "person:syn-admin", "new.user@example.test", "New User", [])
    with api_client(db, monkeypatch, VELOXA_OIDC="1") as c:
        good = {"Authorization": f"Bearer {id_token(synth['iss'], 'subject-123')}"}
        assert c.get("/v1/me", headers=good).json()["detail"] == "sign-in rejected (not_a_member)"
        acc = c.post("/v1/invitations/accept", json={"invitation_token": token}, headers=good)
        assert acc.status_code == 200 and acc.json()["principal_id"] == pid
        me = c.get("/v1/me", headers=good).json()
        assert me["principal"]["principal_id"] == pid and me["tenant"]["slug"] == synth["slug"]
        assert c.post("/v1/invitations/accept", json={"invitation_token": token}, headers=good).status_code == 403
        for bad, reason in ((id_token(synth["iss"], "subject-123", aud="other"), "wrong_audience"),
                            (id_token(synth["iss"], "subject-123", exp_in=-600), "expired"),
                            (id_token("https://unknown.example.test", "subject-123"), "unknown_issuer")):
            r = c.get("/v1/me", headers={"Authorization": f"Bearer {bad}"})
            assert r.status_code == 401 and reason in r.json()["detail"]
        # A same-domain e-mail claim grants nothing: membership comes only from the bound (issuer, subject).
        other = {"Authorization": f"Bearer {id_token(synth['iss'], 'someone-else', email='new.user@example.test')}"}
        assert c.get("/v1/me", headers=other).status_code == 401
    with connect(db.dsn("app")) as app:
        tenancy.set_status(app, synth["tenant"], "person:syn-admin", pid, "suspended", "test")
    with api_client(db, monkeypatch, VELOXA_OIDC="1") as c:
        assert c.get("/v1/me", headers=good).status_code == 403
