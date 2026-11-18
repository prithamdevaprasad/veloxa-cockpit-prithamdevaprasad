"""Sign-in endpoints (see `login.py` for the design). Every failure redirects or answers with a reason code; nothing
falls back to a weaker method, and no token or secret is ever returned to the browser."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

from . import login
from .db import connect, tenant_tx


def _https_json(url: str, data: dict | None = None, timeout: float = 10.0) -> dict:
    if not url.startswith("https://"):
        raise ValueError("identity provider endpoints must use HTTPS")
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    headers = {"Accept": "application/json"} | ({"Content-Type": "application/x-www-form-urlencoded"} if body else {})
    req = urllib.request.Request(url, data=body, headers=headers)  # noqa: S310 (HTTPS only, checked above)
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (scheme checked above)
        return json.loads(r.read())


HTTP: Callable[..., dict] = _https_json          # tests replace this with a fake identity provider


def register(app, *, state: dict, oidc_auth, auth_settings, dev_tenant_id: Callable, now_fn: Callable) -> None:
    store: login.Store = state.setdefault("sessions", login.Store())

    def fetch(*a):
        return HTTP(*a)
    discovery: dict[str, dict] = {}

    def providers() -> dict[str, login.Provider]:
        return {p.id: p for p in login.providers_from_env()} if oidc_auth is not None else {}

    def endpoints(p: login.Provider) -> dict:
        if p.id not in discovery:
            d = fetch(p.issuer + "/.well-known/openid-configuration")
            if d.get("issuer") != p.issuer:
                raise ValueError("discovery issuer mismatch")
            discovery[p.id] = d
        return discovery[p.id]

    def cookie_kw(request: Request) -> dict:
        secure = request.url.scheme == "https" or (login.redirect_base() or "").startswith("https://")
        return {"httponly": True, "samesite": "lax", "secure": secure, "path": "/"}

    def fail(code: str) -> RedirectResponse:
        r = RedirectResponse(f"/#/signin?error={code}", status_code=303)
        r.delete_cookie(login.LOGIN_COOKIE, path="/")
        return r

    @app.get("/v1/auth/options")
    def options(request: Request) -> dict:
        """What this deployment can genuinely offer. Unconfigured providers are absent, not disabled buttons."""
        demo = (login.demo_enabled(auth_settings.mode) and login.is_loopback(getattr(request.client, "host", None))
                and login.local_host_header(request.headers.get("host")))
        return {"providers": [{"id": p.id, "label": p.label} for p in providers().values()],
                "demo": bool(demo), "developer_token": auth_settings.mode == "development_fixture",
                "sso_ready": bool(providers()) and login.redirect_base() is not None}

    @app.get("/v1/auth/login/{provider}")
    def begin(provider: str, request: Request, invite: str | None = None):
        p = providers().get(provider)
        base = login.redirect_base()
        if p is None or base is None:
            return fail("sso_not_configured")
        try:
            ep = endpoints(p)
        except Exception:  # noqa: BLE001  (discovery failure: say so, never guess endpoints)
            return fail("provider_unavailable")
        st, nonce, _, challenge = store.begin(p.id, invite)
        url = login.authorize_url(ep["authorization_endpoint"], p, f"{base}/v1/auth/callback/{p.id}", st, nonce,
                                  challenge)
        r = RedirectResponse(url, status_code=303)
        r.set_cookie(login.LOGIN_COOKIE, st, max_age=login.PENDING_TTL, **cookie_kw(request))
        return r

    @app.get("/v1/auth/callback/{provider}")
    def callback(provider: str, request: Request, code: str | None = None, error: str | None = None):
        st = request.query_params.get("state")
        p = providers().get(provider)
        if p is None:
            return fail("sso_not_configured")
        if error:
            return fail("provider_refused")
        bound = request.cookies.get(login.LOGIN_COOKIE)
        pending = store.take(st) if st and bound and st == bound else None      # login CSRF: same browser only
        if pending is None or pending["provider"] != p.id or not code:
            return fail("login_expired")
        try:
            tok = fetch(endpoints(p)["token_endpoint"], {
                "grant_type": "authorization_code", "code": code, "client_id": p.client_id,
                "redirect_uri": f"{login.redirect_base()}/v1/auth/callback/{p.id}",
                "code_verifier": pending["verifier"],
                **({"client_secret": p.client_secret} if p.client_secret else {})})
        except Exception:  # noqa: BLE001
            return fail("token_exchange_failed")
        id_token = tok.get("id_token") if isinstance(tok, dict) else None
        now = now_fn()
        with connect(state["dsn"]) as conn:
            v = oidc_auth.verify_subject(conn, id_token, now)
            if not hasattr(v, "subject"):
                return fail(f"rejected_{getattr(v, 'reason', 'unknown')}")
            claims = v.claims or {}
            if v.issuer != p.issuer:
                return fail("rejected_wrong_issuer")
            if claims.get("nonce") != pending["nonce"]:
                return fail("rejected_nonce")
            if login.mfa_required() and "mfa" not in (claims.get("amr") or []):
                return fail("mfa_required")
            if pending.get("invite"):
                from . import tenancy

                try:
                    tenancy.accept_invitation(conn, pending["invite"], v.issuer, v.subject)
                except Exception:  # noqa: BLE001  (expired, used, or another person's invitation)
                    return fail("invalid_invitation")
            signin = oidc_auth.authenticate(conn, id_token, now)
            if not hasattr(signin, "principal_id"):
                return fail(f"rejected_{getattr(signin, 'reason', 'unknown')}")
        sid = store.create("oidc", signin.principal_id, str(signin.tenant_id), p.id)
        r = RedirectResponse("/#/overview", status_code=303)
        r.set_cookie(login.SESSION_COOKIE, sid, max_age=login.ABSOLUTE_TTL, **cookie_kw(request))
        r.delete_cookie(login.LOGIN_COOKIE, path="/")
        return r

    @app.post("/v1/auth/demo")
    def demo(request: Request):
        """Local demonstration entry: the development fixture's principal in a SYNTHETIC Demo Lab tenant."""
        if not (login.demo_enabled(auth_settings.mode) and login.is_loopback(getattr(request.client, "host", None))
                and login.local_host_header(request.headers.get("host"))):
            return JSONResponse(status_code=404, content={"error": "not_found"})
        with connect(state["dsn"]) as conn:
            tid = dev_tenant_id(conn)
            row = None
            if tid is not None:
                with tenant_tx(conn, tid):
                    row = conn.execute("SELECT slug, synthetic FROM directory.tenant WHERE tenant_id = %s",
                                       (tid,)).fetchone()
        if row is None or not row["synthetic"] or row["slug"] in login.DEMO_REFUSED_SLUGS:
            return JSONResponse(status_code=403, content={
                "error": "demo_not_available", "detail": "the demo entry opens only a synthetic Demo Lab workspace"})
        sid = store.create("demo", auth_settings.principal_emp_id, str(tid))
        r = JSONResponse({"signed_in": True, "method": "demo"})
        r.set_cookie(login.SESSION_COOKIE, sid, max_age=login.ABSOLUTE_TTL, **cookie_kw(request))
        return r

    @app.get("/v1/auth/session")
    def session_info(request: Request):
        s = store.get(request.cookies.get(login.SESSION_COOKIE))
        if s is None:
            return JSONResponse(status_code=401, content={"error": "not_authenticated",
                                                          "detail": "no active session"})
        return {"method": s.kind, "provider": s.provider,
                "expires_at": datetime.fromtimestamp(store.expires_at(s), UTC).isoformat()}

    @app.post("/v1/auth/logout")
    def logout(request: Request):
        store.end(request.cookies.get(login.SESSION_COOKIE))
        r = JSONResponse({"signed_out": True})
        r.delete_cookie(login.SESSION_COOKIE, path="/")
        return r
