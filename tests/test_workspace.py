"""Multi-company workspace foundation (migration 0017). SYNTHETIC tenants and people only."""

import uuid
from datetime import timedelta

import psycopg
import pytest

from veloxa import tenancy
from veloxa.db import connect, tenant_tx

pytestmark = pytest.mark.db
ISS = "https://idp.example.test"


@pytest.fixture
def admin(db):
    with connect(db.admin_dsn) as c:
        yield c


@pytest.fixture
def app(db):
    with connect(db.dsn("app")) as c:
        yield c


def _slug():
    return f"t-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def ws(admin):
    """A synthetic workspace with an active synthetic administrator."""
    tid = tenancy.create_tenant(admin, _slug(), "Europe/London", "Synthetic Co", synthetic=True)
    tenancy.bootstrap_synthetic_admin(admin, tid, "person:syn-admin", "Synthetic Admin")
    return tid


def test_tenant_creation_is_data_with_fail_closed_defaults(admin, app, ws):
    s = tenancy.settings(app, ws)
    assert (s.admission_mode, s.evidence_access, s.status, s.version, s.synthetic) == (
        "submission", "rebac", "active", 1, True)
    with pytest.raises(tenancy.TenancyError):
        tenancy.create_tenant(admin, "Bad Slug!", "UTC", "x")
    with pytest.raises(tenancy.TenancyError):
        tenancy.create_tenant(admin, _slug(), "Mars/Base", "x")
    v = tenancy.record_settings(admin, ws, status="suspended")
    assert v == 2 and tenancy.settings(app, ws).status == "suspended"


def test_existing_tenants_keep_their_pre_0017_behaviour(app, db):
    from veloxa import config

    s = tenancy.settings(app, config.tenant_uuid("veloxa"))
    assert (s.admission_mode, s.evidence_access, s.version) == ("release_schedule", "tenant_wide", 0)


def test_the_synthetic_bootstrap_is_refused_for_a_real_tenant(admin):
    tid = tenancy.create_tenant(admin, _slug(), "UTC", "Real Co", synthetic=False)
    with pytest.raises(tenancy.TenancyError, match="synthetic"):
        tenancy.bootstrap_synthetic_admin(admin, tid, "person:anyone", "Anyone")


def test_only_an_active_admin_grants_and_nobody_escalates_themselves(app, ws):
    tenancy.create_department(app, ws, "person:syn-admin", "engineering", "Engineering", "hr_export")
    pid, _ = tenancy.invite(app, ws, "person:syn-admin", "eng.head@example.test", "Eng Head",
                            [{"resource_type": "department", "resource_id": "engineering", "relation": "head"}])
    tuple_id = tenancy.grant(app, ws, "person:syn-admin", "department", "engineering", "member", pid, "onboarding")
    assert tuple_id
    with pytest.raises(tenancy.NotPermitted, match="self-escalation"):
        tenancy.grant(app, ws, "person:syn-admin", "tenant", str(ws), "company_reader", "person:syn-admin", "x")
    with pytest.raises(tenancy.NotPermitted, match="administrator"):          # an invited non-admin cannot grant
        tenancy.grant(app, ws, pid, "tenant", str(ws), "admin", pid, "x")
    with pytest.raises(tenancy.TenancyError, match="not grantable"):
        tenancy.grant(app, ws, "person:syn-admin", "tenant", str(ws), "owner", pid, "x")
    with pytest.raises(tenancy.TenancyError, match="reason"):
        tenancy.grant(app, ws, "person:syn-admin", "department", "engineering", "member", pid, " ")
    with pytest.raises(tenancy.NotPermitted):
        tenancy.revoke(app, ws, "person:syn-admin",
                       next(r["tuple_id"] for r in tenancy.relations(app, ws, "person:syn-admin")
                            if r["relation"] == "admin"))


def test_the_api_role_cannot_write_tuples_or_directory_rows_directly(app, ws):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with tenant_tx(app, ws, "person:syn-admin"):
            app.execute("INSERT INTO policy.relationship_tuple (tenant_id, tuple_id, resource_type, resource_id,"
                        " relation, subject_type, subject_id, source) VALUES (%s, %s, 'tenant', %s, 'admin',"
                        " 'person', 'x', 'x')", (ws, uuid.uuid4(), str(ws)))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with tenant_tx(app, ws, "person:syn-admin"):
            app.execute("SELECT * FROM directory.tenant_issuer")


def test_an_invitation_is_one_time_bound_to_a_trusted_issuer_and_grants_its_roles(admin, app, ws):
    tenancy.add_issuer(admin, ws, f"{ISS}/{ws}", "veloxa", f"{ISS}/{ws}/jwks")
    tenancy.create_department(app, ws, "person:syn-admin", "finance", "Finance")
    pid, token = tenancy.invite(app, ws, "person:syn-admin", "Fin.Reviewer@Example.test", "Fin Reviewer",
                                [{"resource_type": "department", "resource_id": "finance", "relation": "reviewer"}])
    assert tenancy.member(app, ws, pid)["status"] == "invited"
    with pytest.raises(tenancy.NotPermitted, match="not trusted"):
        tenancy.accept_invitation(app, token, "https://untrusted.example.test", "sub-1")
    t, p = tenancy.accept_invitation(app, token, f"{ISS}/{ws}", "sub-1")
    assert (t, p) == (ws, pid) and tenancy.member(app, ws, pid)["status"] == "active"
    assert {(r["resource_id"], r["relation"]) for r in tenancy.relations(app, ws, pid)} == {("finance", "reviewer")}
    with pytest.raises(tenancy.NotPermitted, match="already used"):
        tenancy.accept_invitation(app, token, f"{ISS}/{ws}", "sub-2")
    with pytest.raises(tenancy.NotPermitted):
        tenancy.accept_invitation(app, "not-a-real-token", f"{ISS}/{ws}", "sub-3")
    with app.transaction():
        assert app.execute("SELECT * FROM directory.principal_for_subject(%s, %s)",
                           (f"{ISS}/{ws}", "sub-1")).fetchone()["principal_id"] == pid


def test_invitation_validity_is_bounded(app, ws):
    with pytest.raises(tenancy.TenancyError, match="validity"):
        tenancy.invite(app, ws, "person:syn-admin", "x@example.test", "X", [], valid_for=timedelta(days=30))


def test_suspension_is_immediate_and_an_admin_cannot_suspend_themselves(admin, app, ws):
    tenancy.bootstrap_synthetic_admin(admin, ws, "person:syn-admin-2", "Second Admin")
    tenancy.set_status(app, ws, "person:syn-admin", "person:syn-admin-2", "suspended", "left the pilot")
    assert tenancy.member(app, ws, "person:syn-admin-2")["status"] == "suspended"
    with pytest.raises(tenancy.NotPermitted, match="active"):
        tenancy.create_department(app, ws, "person:syn-admin-2", "ops", "Operations")
    with pytest.raises(tenancy.NotPermitted, match="own status"):
        tenancy.set_status(app, ws, "person:syn-admin", "person:syn-admin", "suspended", "x")


def test_an_admin_of_one_tenant_has_no_power_in_another(admin, app, ws):
    other = tenancy.create_tenant(admin, _slug(), "UTC", "Other Co", synthetic=True)
    tenancy.bootstrap_synthetic_admin(admin, other, "person:other-admin", "Other Admin")
    with pytest.raises(tenancy.NotPermitted):
        tenancy.create_department(app, other, "person:syn-admin", "engineering", "Engineering")
    with tenant_tx(app, ws, "person:syn-admin"):
        seen = {r["tenant_id"] for r in app.execute("SELECT tenant_id FROM directory.principal").fetchall()}
    assert seen == {ws}


def test_workspace_tables_are_append_only(admin, ws):
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="append-only"):
        with tenant_tx(admin, ws, "operator"):
            admin.execute("UPDATE directory.principal SET display_name = 'x' WHERE tenant_id = %s", (ws,))


def test_the_operator_cli_creates_and_shows_a_tenant(db, monkeypatch, capsys):
    from urllib.parse import urlsplit

    from veloxa.cli import main

    p = urlsplit(db.admin_dsn)
    for k, v in {"VELOXA_DB_HOST": p.hostname, "VELOXA_DB_PORT": str(p.port), "VELOXA_DB_NAME": db.name,
                 "VELOXA_DB_ADMIN_USER": p.username, "VELOXA_DB_ADMIN_PASSWORD": p.password}.items():
        monkeypatch.setenv(k, v)
    slug = _slug()
    assert main(["tenant", "create", "--slug", slug, "--tz", "Asia/Kolkata", "--name", "CLI Co", "--synthetic"]) == 0
    assert main(["tenant", "show", "--slug", slug]) == 0
    assert "evidence access rebac" in capsys.readouterr().out
    assert main(["tenant", "create", "--slug", slug, "--tz", "UTC"]) == 2           # duplicate refused
    assert main(["tenant", "invite-admin", "--slug", slug, "--email", "a@example.test", "--name", "A"]) == 0
    assert "One-time token" in capsys.readouterr().out


def test_a_member_without_an_email_takes_it_from_their_accepted_invitation_only(admin, app, ws):
    """A bootstrapped member (no stored e-mail) later invited to sign in: their address comes from the invitation
    once it is ACCEPTED through a trusted sign-in, never before, so mail addressed to them becomes visible to them."""
    tenancy.add_issuer(admin, ws, f"{ISS}/{ws}", "veloxa", f"{ISS}/{ws}/jwks")
    tenancy.add_synthetic_member(admin, ws, "person:boss", "Boss")
    assert tenancy.member(app, ws, "person:boss")["email"] is None
    token = "boss-invitation-token-for-tests-only-0123456789"
    tenancy._guarded(app, ws, "person:syn-admin", "SELECT directory.admin_invite(%s,%s,%s,%s,%s,%s::jsonb) AS id",
                     ("person:boss", "Boss", "Boss@Example.test", tenancy.token_hash(token), timedelta(days=1), "[]"),
                     "workspace.invited", {"principal": "person:boss", "roles": []})
    assert tenancy.member(app, ws, "person:boss")["email"] is None          # invited, not accepted: no address
    tenancy.accept_invitation(app, token, f"{ISS}/{ws}", "sub-boss")
    assert tenancy.member(app, ws, "person:boss")["email"] == "boss@example.test"
