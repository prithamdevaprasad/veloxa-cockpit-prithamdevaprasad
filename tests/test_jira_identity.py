"""Jira assignee -> employee identity resolution (migration 0022). Real (release-pack) identities may be linked ONLY
by a verified exact identifier or an administrator's explicit mapping, only in the Jira contexts, never by a name
rule; ambiguous candidates are never approvable; e-mail identities keep the synthetic-only guard; every change is a
new versioned decision. Uses the SYNTHETIC D1 test pack's records as ledger citations."""

import uuid

import psycopg
import pytest
from conftest import ensure_configured_d1

from veloxa.db import connect, tenant_tx
from veloxa.resolution import jira_identity, rules
from veloxa.resolution import store as c12

pytestmark = pytest.mark.db


@pytest.fixture
def proj(db):
    with connect(db.dsn("projection")) as conn:
        yield conn


def _rec(db, tenant):
    with psycopg.connect(db.admin_dsn, autocommit=True, row_factory=psycopg.rows.dict_row) as admin:
        r = admin.execute("SELECT record_id, version, batch_seq FROM ledger.source_record WHERE tenant_id = %s"
                          " ORDER BY record_id LIMIT 1", (tenant,)).fetchone()
    return (r["record_id"], r["version"], r["batch_seq"])


def _person(proj, tenant, ref):
    hr = c12.register_identity(proj, tenant, "hr", "hr.emp_id", f"T{uuid.uuid4().hex[:6]}", "release_pack", ref)
    return c12.register_entity(proj, tenant, "person", hr)


def _jira(proj, tenant, ref, context="jira.assignee_code"):
    return c12.register_identity(proj, tenant, "jira", context, f"X{uuid.uuid4().hex[:5]}", "release_pack", ref)


@pytest.fixture
def setup(db, tmp_path):
    tenant = ensure_configured_d1(db, tmp_path)
    return tenant, _rec(db, tenant)


def test_a_name_rule_never_links_a_real_jira_identity(setup, proj, app_conn):
    tenant, ref = setup
    ent, ident = _person(proj, tenant, ref), _jira(proj, tenant, ref)
    (cand,) = c12.propose(proj, tenant, ident, [rules.Candidate(ent, "unique_initials_in_context", ("initials",))])
    with pytest.raises(c12.NotPermitted, match="only synthetic"):
        c12.decide(app_conn, tenant, "person:ADMIN", cand, "approved", "initials look right", "TEST")


def test_an_administrator_mapping_links_keeps_history_and_can_be_withdrawn(setup, proj, app_conn):
    tenant, ref = setup
    a, b, ident = _person(proj, tenant, ref), _person(proj, tenant, ref), _jira(proj, tenant, ref)
    with tenant_tx(app_conn, tenant, "person:ADMIN"):
        ca = jira_identity._candidate_for(app_conn, tenant, ident, a, "administrator_mapping", ["admin"])
    c12.decide(app_conn, tenant, "person:ADMIN", ca, "approved", "confirmed with the team lead", "admin mapping")
    with tenant_tx(app_conn, tenant):
        links = jira_identity.resolved(app_conn, tenant)
    assert len([v for (ctx, _), v in links.items() if ctx == "jira.assignee_code"]) >= 1
    with tenant_tx(app_conn, tenant, "person:ADMIN"):           # a second approval for another person is refused
        cb = jira_identity._candidate_for(app_conn, tenant, ident, b, "administrator_mapping", ["admin"])
    with pytest.raises(c12.NotPermitted, match="already approved"):
        c12.decide(app_conn, tenant, "person:ADMIN", cb, "approved", "other person", "admin mapping")
    jira_identity.unmap_identity(app_conn, tenant, "person:ADMIN", ident, "was wrong")
    c12.decide(app_conn, tenant, "person:ADMIN", cb, "approved", "the right person", "admin mapping")
    with tenant_tx(app_conn, tenant):
        hist = app_conn.execute(
            "SELECT d.outcome FROM judgement.resolution_decision d JOIN judgement.match_candidate mc"
            " USING (tenant_id, candidate_id) WHERE mc.identity_id = %s ORDER BY d.decided_at", (ident,)).fetchall()
        raw = app_conn.execute("SELECT source_value FROM judgement.source_identity WHERE identity_id = %s",
                               (ident,)).fetchone()["source_value"]
    assert [h["outcome"] for h in hist] == ["approved", "rejected", "approved"]       # nothing overwritten
    assert raw.startswith("X")                                                        # original value kept


def test_ambiguous_is_never_approvable_and_other_contexts_stay_locked(setup, proj, app_conn):
    tenant, ref = setup
    ent, ident = _person(proj, tenant, ref), _jira(proj, tenant, ref)
    (amb,) = c12.propose(proj, tenant, ident, [rules.Candidate(ent, "ambiguous", ("two people fit",))])
    with pytest.raises(c12.NotPermitted, match="ambiguous"):
        c12.decide(app_conn, tenant, "person:ADMIN", amb, "approved", "pick one", "TEST")
    mail = c12.register_identity(proj, tenant, "email", "email.address", f"x{uuid.uuid4().hex[:5]}@example.test",
                                 "release_pack", ref)
    with pytest.raises(psycopg.errors.InsufficientPrivilege), tenant_tx(app_conn, tenant, "person:ADMIN"):
        jira_identity._candidate_for(app_conn, tenant, mail, ent, "administrator_mapping", ["admin"])
    with pytest.raises(psycopg.errors.InsufficientPrivilege), tenant_tx(app_conn, tenant, "person:ADMIN"):
        jira_identity._candidate_for(app_conn, tenant, ident, ent, "unique_initials_in_context", ["app"])


def test_verified_email_links_only_an_exact_unique_match(setup, proj, app_conn, monkeypatch):
    tenant, ref = setup
    one, two, three = (_person(proj, tenant, ref) for _ in range(3))
    acct = {n: _jira(proj, tenant, ref, "jira.account_id") for n in ("ok", "dup", "none", "noemail")}
    with tenant_tx(app_conn, tenant):
        value = {n: app_conn.execute("SELECT source_value FROM judgement.source_identity WHERE identity_id = %s",
                                     (i,)).fetchone()["source_value"] for n, i in acct.items()}
    people = {"E1": {"entity_id": one, "full_name": "A", "title": "t", "department": "d", "work_email": "a@example.test"},
              "E2": {"entity_id": two, "full_name": "B", "title": "t", "department": "d", "work_email": "dup@example.test"},
              "E3": {"entity_id": three, "full_name": "C", "title": "t", "department": "d", "work_email": "dup@example.test"}}
    monkeypatch.setattr(jira_identity, "_people", lambda conn, t, k: people)
    users = [{"account_id": value["ok"], "email": "A@example.test"}, {"account_id": value["dup"], "email": "dup@example.test"},
             {"account_id": value["none"], "email": "nobody@example.test"}, {"account_id": value["noemail"], "email": None},
             {"account_id": "not-an-assignee", "email": "a@example.test"}]
    out = jira_identity.verify_with_jira(app_conn, tenant, "person:ADMIN", users, 1)
    assert (out["matched"], out["ambiguous"], out["no_hr_match"], out["not_assignees"]) == (1, 1, 1, 1)
    with tenant_tx(app_conn, tenant):
        links = jira_identity.resolved(app_conn, tenant)
    assert links.get(("jira.account_id", value["ok"])) is not None
    assert ("jira.account_id", value["dup"]) not in links                     # ambiguous stays unresolved
    again = jira_identity.verify_with_jira(app_conn, tenant, "person:ADMIN", users, 1)
    assert (again["matched"], again["already"]) == (0, 1)                     # idempotent
