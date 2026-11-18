"""Connected company model (Sprint 5 phase 3): admitted evidence -> C12 identities and candidates, admission
separation for parsed-but-unadmitted sources. SYNTHETIC D1 pack only."""

from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from conftest import ensure_configured_d1

from veloxa import catalogue
from veloxa.db import connect, tenant_tx
from veloxa.ingest import people_contracts
from veloxa.releases import load_authorisations
from veloxa.resolution import integrate
from veloxa.resolution import store as c12

pytestmark = pytest.mark.db


@pytest.fixture
def d1(db, tmp_path):
    tenant = ensure_configured_d1(db, tmp_path)
    with connect(db.dsn("projection")) as proj:
        first = integrate.sync_batch(proj, tenant, 1)
        again = integrate.sync_batch(proj, tenant, 1)
    return tenant, first, again


def rows(conn, tenant, sql, params=()):
    with tenant_tx(conn, tenant):
        return conn.execute(sql, params).fetchall()


def test_admitted_hr_and_email_records_become_cited_identities_and_candidates(d1, app_conn):
    tenant, first, again = d1
    assert first == again                                       # idempotent: K reproduces the same identities
    assert first["hr_identities"] == 2 and first["email_identities"] == 2 and first["candidates"] == 2
    ids = rows(app_conn, tenant, "SELECT si.system, si.context, si.source_value, si.origin, si.batch_seq,"
               " si.source_record_id, si.observed_at, r.record_id FROM judgement.source_identity si"
               " JOIN ledger.source_record r ON r.tenant_id = si.tenant_id AND r.record_id = si.source_record_id"
               " AND r.version = si.source_version WHERE si.context IN ('hr.emp_id', 'email.address')")
    assert {(r["system"], r["source_value"]) for r in ids} == {("hr", "T001"), ("hr", "T002"),
                                                               ("email", "alpha@example.test"),
                                                               ("email", "beta@example.test")}
    assert all(r["origin"] == "release_pack" and r["batch_seq"] == 1 for r in ids)   # every one cites its record
    hr = [r for r in ids if r["system"] == "hr"]
    mail = [r for r in ids if r["system"] == "email"]
    assert all(r["observed_at"] is None for r in hr)           # the HR export states no observation time
    assert {r["observed_at"] for r in mail} == {datetime(2099, 1, 5, 10, 0, tzinfo=timezone(timedelta(hours=5,
                                                                                                      minutes=30)))}
    links = rows(app_conn, tenant,
                 "SELECT si.source_value AS addr, a.source_value AS emp, mc.classification FROM"
                 " judgement.match_candidate mc"
                 " JOIN judgement.source_identity si ON si.tenant_id = mc.tenant_id AND si.identity_id = mc.identity_id"
                 " JOIN judgement.entity e ON e.tenant_id = mc.tenant_id AND e.entity_id = mc.entity_id"
                 " JOIN judgement.source_identity a ON a.tenant_id = e.tenant_id"
                 " AND a.identity_id = e.anchor_identity_id"
                 " WHERE si.context = 'email.address'")
    assert {(r["addr"], r["emp"], r["classification"]) for r in links} == {
        ("alpha@example.test", "T001", "exact_identifier"), ("beta@example.test", "T002", "exact_identifier")}


def test_real_candidates_stay_unresolved_and_grant_nothing(d1, app_conn):
    tenant, _, _ = d1
    cands = rows(app_conn, tenant, "SELECT mc.candidate_id, mc.identity_id FROM judgement.match_candidate mc"
                 " JOIN judgement.source_identity si ON si.tenant_id = mc.tenant_id AND si.identity_id = mc.identity_id"
                 " WHERE si.context = 'email.address'")
    for c in cands:
        assert c12.identity_state(app_conn, tenant, c["identity_id"]) == "candidate"
        with pytest.raises(c12.NotPermitted, match="only synthetic test identities"):
            c12.decide(app_conn, tenant, "person:T001", c["candidate_id"], "approved", "same address", "SYN-CM-1")
    assert c12.resolve(app_conn, tenant, "email.address", "alpha@example.test")["status"] == "unresolved"
    assert rows(app_conn, tenant, "SELECT count(*) AS n FROM policy.relationship_tuple WHERE subject_id LIKE"
                " '%%@%%' OR resource_id LIKE '%%@%%'")[0]["n"] == 0          # participation is never a permission


def test_projections_and_the_ledger_are_unchanged_by_integration(db, tmp_path, app_conn):
    tenant = ensure_configured_d1(db, tmp_path)
    count = ("SELECT (SELECT count(*) FROM ledger.source_record) AS ledger, (SELECT count(*) FROM projection.person)"
             " AS people, (SELECT count(*) FROM projection.document_text) AS docs")
    before = rows(app_conn, tenant, count)[0]
    with connect(db.dsn("projection")) as proj:
        integrate.sync_batch(proj, tenant, 1)
    assert rows(app_conn, tenant, count)[0] == before


def test_only_the_projection_role_integrates_and_tenants_stay_isolated(db, d1, tenant, app_conn):
    t1, _, _ = d1
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        integrate.sync_batch(app_conn, t1, 1)
    assert rows(app_conn, tenant, "SELECT count(*) AS n FROM judgement.source_identity")[0]["n"] == 0
    with connect(db.dsn("projection")) as proj:
        empty = integrate.sync_batch(proj, tenant, 1)              # another tenant has no batch 1: nothing invented
    assert empty == {"hr_identities": 0, "email_identities": 0, "candidates": 0, "unmatched_email_identities": 0}


def test_a_parsed_source_is_not_an_admitted_source():
    """Parsing never admits. A file needs a catalogue rule AND a source-specific authorisation (K-13, owner decision
    18 Nov 2026 for D2/D3); an unknown file stays unclassified, and D4 is not authorised at all."""
    title, cols = ("Synthetic",), people_contracts.PULSE_COLUMNS
    parsed = people_contracts.pulse([title, (None,), cols, ("Jul-Sep 2099", "Syn", 5, 5, 3, None)])
    assert parsed.errors == [] and len(parsed.rows) == 1
    rules = catalogue.load_catalogue()
    with pytest.raises(catalogue.UnclassifiedSource):
        catalogue.classify(rules, "data_room/engineering/Synthetic_Tracker_Export.xlsx")
    auth = load_authorisations()
    assert "D4" not in auth and all(auth[p].sources for p in ("D2", "D3"))
    assert "data_room/hr/Pulse_Survey.xlsx" in auth["D2"].sources
