"""Browser sign-in: OIDC authorization code + PKCE against a FAKE identity provider with synthetic keys, server-side
sessions, CSRF, sign-out, expiry, the local demo entry and its restrictions. No real identity provider is contacted
and no real credential exists here."""

import base64
import hashlib
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import API_CLOCK, DEV_TOKEN
from fastapi.testclient import TestClient
from test_workspace_api import id_token, jwks

from veloxa import api_auth, login, tenancy
from veloxa.db import connect

pytestmark = pytest.mark.db
CLIENT_ID = "synthetic-client-id-for-tests"


def _tenant(db, slug=None, issuer_tid=None):
    slug = slug or f"syn-{uuid.uuid4().hex[:8]}"
    issuer_tid = issuer_tid or str(uuid.uuid4())
    iss = f"https://login.microsoftonline.com/{issuer_tid}/v2.0"
    with connect(db.admin_dsn) as a:
        tid = tenancy.create_tenant(a, slug, "Asia/Kolkata", "Synthetic Workspace", synthetic=True)
        tenancy.bootstrap_synthetic_admin(a, tid, "person:syn-admin", "Synthetic Admin")
        tenancy.add_issuer(a, tid, iss, CLIENT_ID, f"{iss}/jwks")
    with connect(db.dsn("app")) as app:
        pid, invite = tenancy.invite(app, tid, "person:syn-admin", "ceo@example.test", "Synthetic CEO", [])
    return {"slug": slug, "tenant": tid, "iss": iss, "entra": issuer_tid, "pid": pid, "invite": invite}


class FakeIdP:
    """Discovery, and a token endpoint that checks PKCE and returns an ID token chosen by the test."""

    def __init__(self, iss):
        self.iss, self.challenge, self.claims, self.token_iss = iss, None, {}, iss
        self.aud, self.exp_in = CLIENT_ID, 300
        self.nonce_override = None

    def __call__(self, url, data=None):
        if url.endswith("/.well-known/openid-configuration"):
            return {"issuer": self.iss, "authorization_endpoint": f"{self.iss}/authorize",
                    "token_endpoint": f"{self.iss}/token"}
        assert data["grant_type"] == "authorization_code" and data["client_id"] == CLIENT_ID
        got = base64.urlsafe_b64encode(hashlib.sha256(data["code_verifier"].encode()).digest()).rstrip(b"=").decode()
        assert got == self.challenge, "PKCE verifier does not match the challenge"
        return {"id_token": id_token(self.token_iss, "entra-subject-1", aud=self.aud, exp_in=self.exp_in,
                                     nonce=self.nonce_override or self.nonce, **self.claims)}


def _app(db, monkeypatch, t, idp=None, **env):
    import veloxa.authz.oidc as oidc
    from veloxa.api import PRIVILEGED_ENV, create_app

    for k in PRIVILEGED_ENV + ("VELOXA_DEV_AUTH", "VELOXA_DEV_TOKEN", "VELOXA_DEV_PRINCIPAL", "VELOXA_DEV_TENANT",
                               "VELOXA_DEMO_LOGIN", "VELOXA_SSO_REQUIRE_MFA", "VELOXA_SSO_GOOGLE_CLIENT_ID"):
        monkeypatch.delenv(k, raising=False)
    base = {"VELOXA_OIDC": "1", "VELOXA_SSO_ENTRA_TENANT": t["entra"], "VELOXA_SSO_ENTRA_CLIENT_ID": CLIENT_ID,
            "VELOXA_PUBLIC_URL": "http://localhost:4173"} if t else {}
    for k, v in {**base, **env}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(oidc, "https_fetch", lambda url: (lambda: jwks()))
    if idp:
        monkeypatch.setattr(api_auth, "HTTP", idp)
    return create_app(dsn=db.dsn("app"), clock=lambda: API_CLOCK)


def _login(c, idp, invite=None):
    r = c.get("/v1/auth/login/entra" + (f"?invite={invite}" if invite else ""), follow_redirects=False)
    assert r.status_code == 303, r.text
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert q["code_challenge_method"] == ["S256"] and q["response_type"] == ["code"]
    assert q["redirect_uri"] == ["http://localhost:4173/v1/auth/callback/entra"]
    idp.challenge, idp.nonce = q["code_challenge"][0], q["nonce"][0]
    return q["state"][0]


def _callback(c, state):
    r = c.get(f"/v1/auth/callback/entra?code=c1&state={state}", follow_redirects=False)
    assert r.status_code == 303
    return r.headers["location"]


def test_entra_code_flow_with_pkce_creates_a_server_side_session_and_signs_out(db, monkeypatch):
    t = _tenant(db)
    idp = FakeIdP(t["iss"])
    with TestClient(_app(db, monkeypatch, t, idp), base_url="http://localhost") as c:
        opts = c.get("/v1/auth/options").json()
        assert opts["providers"] == [{"id": "entra", "label": "Microsoft"}] and opts["demo"] is False
        assert _callback(c, _login(c, idp, t["invite"])) == "/#/overview"
        assert "veloxa_login" not in c.cookies
        sid = c.cookies.get("veloxa_session")
        assert sid and DEV_TOKEN not in sid
        me = c.get("/v1/me").json()                                     # the cookie alone; tenant resolved server-side
        assert me["principal"]["principal_id"] == t["pid"] and me["tenant"]["slug"] == t["slug"]
        assert c.get("/v1/auth/session").json()["method"] == "oidc"
        # the browser cannot choose its tenant: extra hints are ignored
        assert c.get("/v1/me", headers={"X-Tenant-ID": str(uuid.uuid4())}).json()["tenant"]["slug"] == t["slug"]
        # CSRF: a cookie-authenticated write without the custom header is refused before any handler runs
        no_hdr = c.post("/v1/threads", json={"title": "x", "context": {}})
        assert no_hdr.status_code == 403 and no_hdr.json()["error"] == "csrf_header_required"
        ok = c.post("/v1/threads", json={"title": "x", "context": {}}, headers={"X-Veloxa-Request": "1"})
        assert ok.json().get("error") != "csrf_header_required"
        assert c.post("/v1/auth/logout").json() == {"signed_out": True}
        c.cookies.clear()
        c.cookies.set("veloxa_session", sid)                          # a stolen copy of the old cookie
        r = c.get("/v1/me")
        assert r.status_code == 401 and "expired" in r.json()["detail"]


@pytest.mark.parametrize("tamper, code", [
    ({"nonce_override": "another-nonce"}, "rejected_nonce"),
    ({"aud": "someone-elses-client"}, "rejected_wrong_audience"),
    ({"exp_in": -600}, "rejected_expired"),
    ({"token_iss": "https://login.microsoftonline.com/not-registered/v2.0"}, "rejected_unknown_issuer"),
])
def test_tampered_or_invalid_id_tokens_never_create_a_session(db, monkeypatch, tamper, code):
    t = _tenant(db)
    idp = FakeIdP(t["iss"])
    for k, v in tamper.items():
        setattr(idp, k, v)
    with TestClient(_app(db, monkeypatch, t, idp), base_url="http://localhost") as c:
        assert _callback(c, _login(c, idp, t["invite"])) == f"/#/signin?error={code}"
        assert "veloxa_session" not in c.cookies and c.get("/v1/me").status_code == 401


def test_state_binding_replay_uninvited_users_bad_invitations_and_mfa(db, monkeypatch):
    t = _tenant(db)
    idp = FakeIdP(t["iss"])
    with TestClient(_app(db, monkeypatch, t, idp), base_url="http://localhost") as c:
        st = _login(c, idp)
        c.cookies.clear()
        c.cookies.set("veloxa_login", "attacker-state")                # login CSRF: state not bound to this browser
        assert _callback(c, st) == "/#/signin?error=login_expired"
        st = _login(c, idp)
        assert _callback(c, st) == "/#/signin?error=rejected_not_a_member"   # no invitation: no membership
        c.cookies.clear()
        c.cookies.set("veloxa_login", st)
        assert _callback(c, st) == "/#/signin?error=login_expired"           # one-time state
        assert _callback(c, _login(c, idp, "not-a-real-invitation-token-000")) == "/#/signin?error=invalid_invitation"
    with TestClient(_app(db, monkeypatch, t, idp, VELOXA_SSO_REQUIRE_MFA="1"), base_url="http://localhost") as c:
        assert _callback(c, _login(c, idp, t["invite"])) == "/#/signin?error=mfa_required"
        idp.claims = {"amr": ["pwd", "mfa"]}
        assert _callback(c, _login(c, idp, t["invite"])) == "/#/overview"


def test_a_session_is_bound_to_its_tenant_and_expires(db, monkeypatch):
    a, b = _tenant(db), _tenant(db)
    idp = FakeIdP(a["iss"])
    app = _app(db, monkeypatch, a, idp)
    with TestClient(app, base_url="http://localhost") as c:
        _callback(c, _login(c, idp, a["invite"]))
        assert c.get("/v1/me").json()["tenant"]["slug"] == a["slug"] != b["slug"]
        # the other tenant's invitation cannot be redeemed by A's identity provider subject in A's flow
        assert _callback(c, _login(c, idp, b["invite"])) == "/#/signin?error=invalid_invitation"
        store = login.Store(clock=lambda: 0.0)
        sid = store.create("oidc", a["pid"], str(a["tenant"]))
        store.clock = lambda: login.IDLE_TTL + 1.0
        assert store.get(sid) is None                                  # idle expiry
        sid = store.create("oidc", a["pid"], str(a["tenant"]))
        for i in range(1, 20):                                          # active, but past the absolute limit
            store.clock = lambda i=i: login.IDLE_TTL + 1.0 + i * (login.IDLE_TTL - 60)
            store.get(sid)
        assert store.get(sid) is None


def test_unconfigured_providers_are_not_offered_and_cannot_be_used(db, monkeypatch):
    with TestClient(_app(db, monkeypatch, None), base_url="http://localhost") as c:
        assert c.get("/v1/auth/options").json() == {"providers": [], "demo": False, "developer_token": False,
                                                    "sso_ready": False}
        r = c.get("/v1/auth/login/entra", follow_redirects=False)
        assert r.headers["location"] == "/#/signin?error=sso_not_configured"
        assert c.get("/v1/auth/login/google", follow_redirects=False).headers["location"].endswith("sso_not_configured")


def test_demo_entry_only_locally_with_the_explicit_setting_and_only_for_synthetic_demo_data(db, monkeypatch):
    t = _tenant(db)
    fixture = {"VELOXA_DEV_AUTH": "1", "VELOXA_DEV_TOKEN": DEV_TOKEN, "VELOXA_DEV_PRINCIPAL": "person:syn-admin",
               "VELOXA_DEV_TENANT": t["slug"]}
    with TestClient(_app(db, monkeypatch, None, **fixture), base_url="http://localhost",
                    client=("127.0.0.1", 50000)) as c:                   # no VELOXA_DEMO_LOGIN: absent
        assert c.get("/v1/auth/options").json()["demo"] is False and c.post("/v1/auth/demo").status_code == 404
    app = _app(db, monkeypatch, None, VELOXA_DEMO_LOGIN="1", **fixture)
    with TestClient(app, base_url="http://veloxa.example.com", client=("127.0.0.1", 50000)) as c:
        assert c.post("/v1/auth/demo").status_code == 404               # a shared host name: never
    with TestClient(app, base_url="http://localhost", client=("203.0.113.9", 50000)) as c:
        assert c.post("/v1/auth/demo").status_code == 404               # a remote client: never
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000)) as c:
        assert c.get("/v1/auth/options").json()["demo"] is True
        r = c.post("/v1/auth/demo")
        assert r.status_code == 200 and DEV_TOKEN not in r.text
        assert DEV_TOKEN not in r.headers.get("set-cookie", "") and "httponly" in r.headers["set-cookie"].lower()
        me = c.get("/v1/me").json()
        assert me["principal"]["principal_id"] == "person:syn-admin" and me["tenant"]["slug"] == t["slug"]
        assert c.get("/v1/auth/session").json()["method"] == "demo"
        for path in ("/v1/auth/options", "/v1/auth/session", "/v1/me"):
            assert DEV_TOKEN not in c.get(path).text


def test_the_demo_entry_refuses_the_supplied_company_even_locally(db, monkeypatch):
    from veloxa import config
    from veloxa.releases import load_schedule

    primary = config.tenant_uuid(load_schedule().tenant_slug)
    with connect(db.admin_dsn) as a:
        if not a.execute("SELECT 1 FROM directory.tenant WHERE tenant_id = %s", (primary,)).fetchone():
            pytest.skip("the primary tenant is not provisioned in this session database")
    app = _app(db, monkeypatch, None, VELOXA_DEMO_LOGIN="1", VELOXA_DEV_AUTH="1", VELOXA_DEV_TOKEN=DEV_TOKEN,
               VELOXA_DEV_PRINCIPAL="T001")
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000)) as c:
        r = c.post("/v1/auth/demo")
        assert r.status_code == 403 and r.json()["error"] == "demo_not_available"
