"""C12 registries, decision queue and the approval guard (migration 0011). SYNTHETIC identities only; the one
release-pack identity cites a record of the SYNTHETIC D1 test pack, to prove release-pack candidates stay unresolved."""

import uuid
from datetime import datetime

import psycopg
import pytest
from conftest import DEV_TOKEN, api_client, dev_fixture, ensure_configured_d1

from veloxa.db import connect, tenant_tx
from veloxa.resolution import rules
from veloxa.resolution import store as c12

pytestmark = pytest.mark.db
AUTH = {"Authorization": f"Bearer {DEV_TOKEN}"}


@pytest.fixture
def proj(db):
    with connect(db.dsn("projection")) as conn:
        yield conn


def ctx():
    return f"jira.assignee_{uuid.uuid4().hex[:8]}"


def synthetic_pair(proj, tenant, classification="exact_identifier", value=None):
    """An HR-anchored synthetic entity and an observed synthetic identity, with one recorded candidate."""
    hr = c12.register_identity(proj, tenant, "hr", "hr.emp_id", f"syn-{uuid.uuid4().hex[:6]}", "synthetic_test")
    entity = c12.register_entity(proj, tenant, "person", hr)
    seen = c12.register_identity(proj, tenant, "jira", ctx(), value or f"syn-{uuid.uuid4().hex[:6]}",
                                 "synthetic_test")
    (cand,) = c12.propose(proj, tenant, seen, [rules.Candidate(entity, classification, ("synthetic evidence",))])
    return hr, entity, seen, cand


def d1_record(db, tenant):
    with psycopg.connect(db.admin_dsn, autocommit=True, row_factory=psycopg.rows.dict_row) as admin:
        return admin.execute("SELECT record_id, version, batch_seq FROM ledger.source_record WHERE tenant_id = %s"
                             " ORDER BY record_id LIMIT 1", (tenant,)).fetchone()


def test_synthetic_candidate_is_queued_then_decided_by_versions_and_read_basis_aware(proj, app_conn, tenant):
    _, entity, seen, cand = synthetic_pair(proj, tenant)
    q = c12.queue(app_conn, tenant)
    assert [(r["candidate_id"], r["classification"], r["latest_outcome"]) for r in q] == [
        (cand, "exact_identifier", None)]
    assert "score" not in q[0] and "confidence" not in q[0]
    with tenant_tx(app_conn, tenant):
        value, context = app_conn.execute("SELECT source_value, context FROM judgement.source_identity WHERE"
                                          " identity_id = %s", (seen,)).fetchone().values()
    assert c12.resolve(app_conn, tenant, context, value)["status"] == "unresolved"     # nothing is auto-accepted

    d1 = c12.decide(app_conn, tenant, "person:SYN1", cand, "deferred", "need a second source", "SYN-DEC-1")
    assert d1["version"] == 1 and c12.queue(app_conn, tenant)[0]["latest_outcome"] == "deferred"
    d2 = c12.decide(app_conn, tenant, "person:SYN1", cand, "approved", "same synthetic person", "SYN-DEC-2")
    assert d2["version"] == 2 and c12.queue(app_conn, tenant) == []
    r = c12.resolve(app_conn, tenant, context, value)
    assert (r["status"], r["entity_id"], r["raw"]) == ("resolved", str(entity), value)
    before = datetime.fromisoformat(d2["decided_at"]).replace(microsecond=0)
    assert c12.resolve(app_conn, tenant, context, value, known_at=before.replace(year=2000))["status"] == "unresolved"
    c12.decide(app_conn, tenant, "person:SYN1", cand, "rejected", "turned out different", "SYN-DEC-3")
    assert c12.resolve(app_conn, tenant, context, value)["status"] == "unresolved"
    assert c12.resolve(app_conn, tenant, context, value,
                       known_at=datetime.fromisoformat(d2["decided_at"]))["status"] == "resolved"   # as shown then


def test_a_release_pack_candidate_can_never_be_approved(db, proj, app_conn, tmp_path):
    tenant = ensure_configured_d1(db, tmp_path)
    rec = d1_record(db, tenant)
    real = c12.register_identity(proj, tenant, "hr", "hr.record", f"real-{uuid.uuid4().hex[:6]}", "release_pack",
                                 (rec["record_id"], rec["version"], rec["batch_seq"]))
    real_entity = c12.register_entity(proj, tenant, "person", real)
    seen = c12.register_identity(proj, tenant, "jira", ctx(), f"obs-{uuid.uuid4().hex[:6]}", "release_pack",
                                 (rec["record_id"], rec["version"], rec["batch_seq"]))
    (cand,) = c12.propose(proj, tenant, seen, [rules.Candidate(real_entity, "exact_identifier", ("e",))])
    with pytest.raises(c12.NotPermitted, match="only synthetic test identities"):
        c12.decide(app_conn, tenant, "person:SYN1", cand, "approved", "looks the same", "SYN-DEC-4")
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="only synthetic test identities"):
        with tenant_tx(app_conn, tenant):                                  # the database refuses on its own
            app_conn.execute("INSERT INTO judgement.resolution_decision (tenant_id, candidate_id, version,"
                             " decision_id, outcome, decided_by, rationale, decision_record) VALUES"
                             " (%s,%s,1,%s,'approved','person:SYN1','bypass','SYN-DEC-5')",
                             (tenant, cand, uuid.uuid4()))
    assert c12.decide(app_conn, tenant, "person:SYN1", cand, "deferred", "owner decision needed", "SYN-DEC-6")
    # a synthetic observation linked to a real anchor is refused too
    syn = c12.register_identity(proj, tenant, "jira", ctx(), f"syn-{uuid.uuid4().hex[:6]}", "synthetic_test")
    (mixed,) = c12.propose(proj, tenant, syn, [rules.Candidate(real_entity, "surname_initial", ("e",))])
    with pytest.raises(c12.NotPermitted):
        c12.decide(app_conn, tenant, "person:SYN1", mixed, "approved", "x", "SYN-DEC-7")


def test_registration_rules_hold_in_code_and_in_the_database(db, proj, tenant):
    with pytest.raises(c12.Invalid, match="syn-"):
        c12.register_identity(proj, tenant, "jira", ctx(), "looks-real", "synthetic_test")
    with pytest.raises(c12.Invalid, match="cite"):
        c12.register_identity(proj, tenant, "jira", ctx(), "x", "release_pack")
    with pytest.raises(psycopg.errors.CheckViolation):
        with tenant_tx(proj, tenant):
            proj.execute("INSERT INTO judgement.source_identity (tenant_id, identity_id, system, context, source_value,"
                         " origin) VALUES (%s,%s,'jira','jira.assignee','looks-real','synthetic_test')",
                         (tenant, uuid.uuid4()))
    a = c12.register_identity(proj, tenant, "jira", "jira.assignee", "syn-same", "synthetic_test")
    assert c12.register_identity(proj, tenant, "jira", "jira.assignee", "syn-same", "synthetic_test") == a
    c12.add_alias(proj, tenant, a, "payroll.processor", "SS")
    with tenant_tx(proj, tenant):
        assert proj.execute("SELECT context FROM judgement.alias WHERE identity_id = %s", (a,)).fetchone()[
            "context"] == "payroll.processor"


def test_ambiguous_and_double_approvals_are_refused(proj, app_conn, tenant):
    _, _, _, amb = synthetic_pair(proj, tenant, "ambiguous")
    with pytest.raises(c12.NotPermitted, match="ambiguous"):
        c12.decide(app_conn, tenant, "person:SYN1", amb, "approved", "x", "SYN-DEC-8")
    hr2 = c12.register_identity(proj, tenant, "hr", "hr.emp_id", "syn-second", "synthetic_test")
    other = c12.register_entity(proj, tenant, "person", hr2)
    _, _, seen, first = synthetic_pair(proj, tenant)
    (second,) = c12.propose(proj, tenant, seen, [rules.Candidate(other, "surname_initial", ("e",))])
    c12.decide(app_conn, tenant, "person:SYN1", first, "approved", "x", "SYN-DEC-9")
    with pytest.raises(c12.NotPermitted, match="already approved"):
        c12.decide(app_conn, tenant, "person:SYN1", second, "approved", "x", "SYN-DEC-10")
    with pytest.raises(c12.Invalid):
        c12.decide(app_conn, tenant, "person:SYN1", second, "approved", "  ", "SYN-DEC-11")
    with pytest.raises(c12.Invalid):
        c12.decide(app_conn, tenant, "person:SYN1", second, "merged", "x", "SYN-DEC-12")


def test_a_protected_class_identity_is_invisible_to_the_app_and_fails_closed(proj, app_conn, tenant):
    hr = c12.register_identity(proj, tenant, "hr", "hr.emp_id", "syn-pay-anchor", "synthetic_test")
    entity = c12.register_entity(proj, tenant, "person", hr)
    pay = c12.register_identity(proj, tenant, "payroll", "payroll.processor", "syn-pay", "synthetic_test",
                                data_class="payroll_aggregate")
    (cand,) = c12.propose(proj, tenant, pay, [rules.Candidate(entity, "exact_identifier", ("e",))])
    assert c12.queue(app_conn, tenant) == []
    with pytest.raises(c12.Invalid, match="unknown candidate"):
        c12.decide(app_conn, tenant, "person:SYN1", cand, "approved", "x", "SYN-DEC-16")


def test_append_only_least_privilege_and_tenant_isolation(db, proj, app_conn, tenant):
    _, entity, seen, cand = synthetic_pair(proj, tenant)
    c12.decide(app_conn, tenant, "person:SYN1", cand, "rejected", "x", "SYN-DEC-13")
    for sql in ("UPDATE judgement.resolution_decision SET outcome = 'approved'",
                "DELETE FROM judgement.resolution_decision"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with tenant_tx(app_conn, tenant):
                app_conn.execute(sql + " WHERE candidate_id = %s", (cand,))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):                 # the app role only decides
        with tenant_tx(app_conn, tenant):
            app_conn.execute("INSERT INTO judgement.match_candidate (tenant_id, candidate_id, identity_id, entity_id,"
                             " classification, rule_version, evidence) VALUES (%s,%s,%s,%s,'exact_identifier',"
                             "'x','[]')", (tenant, uuid.uuid4(), seen, entity))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):                 # projection proposes, never decides
        c12.decide(proj, tenant, "person:SYN1", cand, "deferred", "x", "SYN-DEC-14")
    with pytest.raises(psycopg.errors.UniqueViolation, match="not consecutive"):
        with tenant_tx(app_conn, tenant):
            app_conn.execute("INSERT INTO judgement.resolution_decision (tenant_id, candidate_id, version,"
                             " decision_id, outcome, decided_by, rationale, decision_record) VALUES"
                             " (%s,%s,5,%s,'deferred','person:SYN1','x','SYN-DEC-15')", (tenant, cand, uuid.uuid4()))
    other = uuid.uuid4()
    with psycopg.connect(db.admin_dsn, autocommit=True) as admin:
        admin.execute("INSERT INTO directory.tenant (tenant_id, slug, synthetic, time_zone) VALUES"
                      " (%s, %s, true, 'UTC')", (other, f"t-{other.hex[:12]}"))
    assert c12.queue(app_conn, other) == []
    with tenant_tx(app_conn, other):
        assert app_conn.execute("SELECT count(*) AS n FROM judgement.match_candidate").fetchone()["n"] == 0


def test_the_api_needs_a_resolver_relation_and_refuses_real_approvals(db, monkeypatch, tmp_path, proj):
    tenant = ensure_configured_d1(db, tmp_path)
    _, _, _, cand = synthetic_pair(proj, tenant)
    rec = d1_record(db, tenant)
    real = c12.register_identity(proj, tenant, "hr", "hr.record", f"real-{uuid.uuid4().hex[:6]}", "release_pack",
                                 (rec["record_id"], rec["version"], rec["batch_seq"]))
    real_entity = c12.register_entity(proj, tenant, "person", real)
    obs = c12.register_identity(proj, tenant, "jira", ctx(), f"obs-{uuid.uuid4().hex[:6]}", "release_pack",
                                (rec["record_id"], rec["version"], rec["batch_seq"]))
    (real_cand,) = c12.propose(proj, tenant, obs, [rules.Candidate(real_entity, "exact_identifier", ("e",))])
    body = {"candidate_id": str(cand), "outcome": "approved", "rationale": "same", "decision_record": "SYN-API-1"}
    with api_client(db, monkeypatch) as anonymous:
        assert anonymous.post("/v1/identity/decisions", json=body).status_code == 401
    with api_client(db, monkeypatch, **dev_fixture()) as client:
        r = client.post("/v1/identity/decisions", json=body, headers=AUTH)
        assert r.status_code == 403 and r.json()["error"] == "not_authorised"        # no resolver relation yet
        with psycopg.connect(db.admin_dsn, autocommit=True) as admin:
            admin.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant),))
            admin.execute("INSERT INTO policy.relationship_tuple (tenant_id, tuple_id, resource_type, resource_id,"
                          " relation, subject_type, subject_id, source) VALUES (%s,%s,'identity_queue','default',"
                          "'resolve','person','T001','synthetic test') ON CONFLICT DO NOTHING",
                          (tenant, uuid.uuid4()))
        queued = client.get("/v1/identity/queue", headers=AUTH).json()["candidates"]
        assert str(cand) in {c["candidate_id"] for c in queued} and all("score" not in c for c in queued)
        assert client.post("/v1/identity/decisions", json=body | {"decided_by": "someone"},
                           headers=AUTH).status_code == 422                   # the decider is never a field
        ok = client.post("/v1/identity/decisions", json=body, headers=AUTH)
        assert ok.status_code == 200 and ok.json()["version"] == 1
        bad = client.post("/v1/identity/decisions", json=body | {"candidate_id": str(real_cand)}, headers=AUTH)
        assert bad.status_code == 403 and bad.json()["error"] == "decision_not_permitted"
    with connect(db.dsn("app")) as conn, tenant_tx(conn, tenant):
        assert conn.execute("SELECT decided_by FROM judgement.resolution_decision WHERE candidate_id = %s",
                            (cand,)).fetchone()["decided_by"] == "person:T001"
        assert conn.execute("SELECT count(*) AS n FROM judgement.resolution_decision WHERE candidate_id = %s",
                            (real_cand,)).fetchone()["n"] == 0
