"""Admin > Integrations > Jira over HTTP in a SYNTHETIC workspace: administration required, secrets never returned,
connect/read are audited, a forged callback is refused, and no delete route exists. No network: Atlassian is never
contacted (the authorize URL is only built; the callback is exercised with a forged state)."""

import pytest
from conftest import DEV_TOKEN, api_client
from test_workspace_api import _fixture, synth  # noqa: F401  (pytest fixture)

from veloxa.db import connect

pytestmark = pytest.mark.db
H = {"Authorization": f"Bearer {DEV_TOKEN}"}
SECRET = "placeholder-secret-never-echoed"


def _env(monkeypatch):
    monkeypatch.setenv("ATLASSIAN_CLIENT_ID", "placeholder-client-id")
    monkeypatch.setenv("ATLASSIAN_CLIENT_SECRET", SECRET)
    monkeypatch.setenv("VELOXA_PUBLIC_URL", "http://localhost:4175")
    for k in ("VELOXA_ENV_FILE", "VELOXA_TOKEN_KEY", "VELOXA_TOKEN_DIR"):
        monkeypatch.delenv(k, raising=False)


def test_jira_integration_is_admin_only_audited_and_never_returns_secrets(db, monkeypatch, synth):  # noqa: F811
    _env(monkeypatch)
    with api_client(db, monkeypatch, **_fixture(synth["slug"], "person:syn-admin")) as c:
        s = c.get("/v1/integrations/jira/status", headers=H)
        assert s.status_code == 200
        body = s.json()
        assert body["callback_url"] == "http://localhost:4175/v1/integrations/jira/callback"
        assert body["env"] == {"ATLASSIAN_CLIENT_ID": True, "ATLASSIAN_CLIENT_SECRET": True, "VELOXA_PUBLIC_URL": True,
                               "VELOXA_TOKEN_KEY": False, "VELOXA_TOKEN_DIR": False}
        assert body["connected"] is False and SECRET not in s.text
        r = c.post("/v1/integrations/jira/connect", headers={**H, "X-Veloxa-Request": "1"})
        assert r.status_code == 200 and r.json()["authorize_url"].startswith("https://auth.atlassian.com/authorize?")
        assert SECRET not in r.text
        bad = c.get("/v1/integrations/jira/callback?code=x&state=forged", follow_redirects=False)
        assert bad.status_code == 303 and bad.headers["location"] == "/#/admin?jira=bad_state"
        nc = c.get("/v1/integrations/jira/search?jql=project%20%3D%20X", headers=H)
        assert nc.status_code == 409 and nc.json()["error"] == "not_connected"
        routes = [(m, rt.path) for rt in c.app.routes for m in getattr(rt, "methods", []) or []
                  if "/integrations/jira" in getattr(rt, "path", "")]
        assert routes and not any(m == "DELETE" for m, _ in routes)
        assert not any("transition" in p or "delete" in p for _, p in routes)
    with connect(db.admin_dsn) as a:
        kinds = [r["kind"] for r in a.execute("SELECT kind FROM audit.event WHERE tenant_id = %s ORDER BY seq",
                                               (synth["tenant"],)).fetchall()]
        details = " ".join(str(r["detail"]) for r in a.execute(
            "SELECT detail FROM audit.event WHERE tenant_id = %s", (synth["tenant"],)).fetchall())
    assert "jira.connect_started" in kinds and "jira.read" in kinds and SECRET not in details


def test_a_non_admin_cannot_see_or_use_the_jira_integration(db, monkeypatch, synth):  # noqa: F811
    _env(monkeypatch)
    with api_client(db, monkeypatch, **_fixture(synth["slug"], "person:syn-user")) as c:
        assert c.get("/v1/integrations/jira/status", headers=H).status_code == 403
        assert c.post("/v1/integrations/jira/connect", headers=H).status_code == 403
        assert c.post("/v1/integrations/jira/commit", headers=H,
                      json={"preview_id": "x" * 20, "confirm": True}).status_code == 403


def test_jira_requests_in_ask_follow_the_integration_permission(db, monkeypatch, synth):  # noqa: F811
    _env(monkeypatch)
    q = {"question": "Create a Jira task for an Engineering blocker"}
    with api_client(db, monkeypatch, **_fixture(synth["slug"], "person:syn-admin")) as c:
        a = c.post("/v1/ask", headers=H, json=q).json()
        assert a["status"] == "abstained" and "isn't connected" in a["answer"] and a["action"] is None
    with api_client(db, monkeypatch, **_fixture(synth["slug"], "person:syn-user")) as c:
        a = c.post("/v1/ask", headers=H, json=q).json()
        assert a["status"] == "refused" and "company-reader grant" in a["answer"]
    with connect(db.admin_dsn) as conn:
        kinds = [r["kind"] for r in conn.execute("SELECT kind FROM audit.event WHERE tenant_id = %s",
                                                  (synth["tenant"],)).fetchall()]
    assert kinds.count("jira.chat") == 2
