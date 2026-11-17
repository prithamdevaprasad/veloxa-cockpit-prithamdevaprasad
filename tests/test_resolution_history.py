"""C12 history, provenance, replay and audit (migration 0012). SYNTHETIC identities only."""

import uuid
from datetime import UTC, datetime

import psycopg
import pytest
from psycopg.rows import dict_row

from veloxa.db import connect, tenant_tx
from veloxa.resolution import rules
from veloxa.resolution import store as c12

pytestmark = pytest.mark.db


@pytest.fixture
def proj(db):
    with connect(db.dsn("projection")) as conn:
        yield conn


def syn(tag=""):
    return f"syn-{tag}{uuid.uuid4().hex[:6]}"


def person(proj, tenant):
    return c12.register_entity(proj, tenant, "person",
                               c12.register_identity(proj, tenant, "hr", "hr.emp_id", syn("hr"), "synthetic_test"))


def audit_kinds(db, tenant):
    with psycopg.connect(db.admin_dsn, autocommit=True, row_factory=dict_row) as admin:
        return admin.execute("SELECT kind, detail FROM audit.event WHERE tenant_id = %s AND kind LIKE 'identity.%%'"
                             " ORDER BY seq", (tenant,)).fetchall()


def test_identity_state_moves_through_explicit_classifications(proj, app_conn, tenant):
    ent = person(proj, tenant)
    seen = c12.register_identity(proj, tenant, "git", "git.author", syn(), "synthetic_test",
                                 observed_at=datetime(2099, 1, 5, 9, 0, tzinfo=UTC))
    assert c12.identity_state(app_conn, tenant, seen) == "unresolved"
    (cand,) = c12.propose(proj, tenant, seen, [rules.Candidate(ent, "exact_identifier", ("e",))])
    assert c12.identity_state(app_conn, tenant, seen) == "candidate"          # a possible match is not a fact
    c12.decide(app_conn, tenant, "person:SYN1", cand, "rejected", "different person", "SYN-H-1")
    assert c12.identity_state(app_conn, tenant, seen) == "rejected"
    c12.decide(app_conn, tenant, "person:SYN1", cand, "approved", "corrected after review", "SYN-H-2")
    assert c12.identity_state(app_conn, tenant, seen) == "approved"
    with tenant_tx(app_conn, tenant):
        row = app_conn.execute("SELECT observed_at, origin, system FROM judgement.identity_state"
                               " WHERE identity_id = %s", (seen,)).fetchone()
    assert row["observed_at"] == datetime(2099, 1, 5, 9, 0, tzinfo=UTC) and row["system"] == "git"
    unstated = c12.register_identity(proj, tenant, "rota", "rota.assignee", syn(), "synthetic_test")
    with tenant_tx(app_conn, tenant):
        assert app_conn.execute("SELECT observed_at FROM judgement.source_identity WHERE identity_id = %s",
                                (unstated,)).fetchone()["observed_at"] is None   # never defaulted to now
    two = c12.register_identity(proj, tenant, "jira", "jira.assignee", syn(), "synthetic_test")
    a, b = person(proj, tenant), person(proj, tenant)
    c12.propose(proj, tenant, two, [rules.Candidate(a, "ambiguous", ("2 fit",)),
                                    rules.Candidate(b, "ambiguous", ("2 fit",))])
    assert c12.identity_state(app_conn, tenant, two) == "conflicting"        # multiple candidates for one identity


def test_aliases_are_context_scoped_duplicates_detected_conflicts_surfaced_and_history_kept(proj, app_conn, tenant):
    x = c12.register_identity(proj, tenant, "jira", "jira.assignee", syn(), "synthetic_test")
    y = c12.register_identity(proj, tenant, "jira", "jira.assignee", syn(), "synthetic_test")
    first = c12.add_alias(proj, tenant, x, "jira.assignee", "SB")
    assert (first["duplicate"], first["conflicting"]) == (False, False)
    again = c12.add_alias(proj, tenant, x, "jira.assignee", "SB")
    assert again["duplicate"] and again["alias_id"] == first["alias_id"]
    clash = c12.add_alias(proj, tenant, y, "jira.assignee", "SB")
    assert clash["conflicting"]                                               # surfaced, never resolved silently
    other_ctx = c12.add_alias(proj, tenant, y, "payroll.processor", "SB")
    assert not other_ctx["conflicting"]                                       # the same text, another context
    assert c12.identity_state(app_conn, tenant, x) == "conflicting"
    assert c12.resolve(app_conn, tenant, "jira.assignee", "SB")["status"] == "unresolved"   # an alias is not a link
    c12.retire_alias(proj, tenant, first["alias_id"], "person:SYN1", "initials reassigned")
    with tenant_tx(app_conn, tenant):
        rows = {r["alias_id"]: r for r in app_conn.execute(
            "SELECT alias_id, active, conflicting, retired_at FROM judgement.alias_state WHERE context ="
            " 'jira.assignee' AND alias_text = 'SB'").fetchall()}
    assert rows[first["alias_id"]]["active"] is False and rows[first["alias_id"]]["retired_at"] is not None
    assert rows[clash["alias_id"]]["conflicting"] is False                    # history kept, conflict cleared
    with pytest.raises(psycopg.errors.UniqueViolation):
        c12.retire_alias(proj, tenant, first["alias_id"], "person:SYN1", "twice")
    with pytest.raises(c12.Invalid):
        c12.retire_alias(proj, tenant, clash["alias_id"], "person:SYN1", " ")


def test_only_an_hr_identity_anchors_a_person(proj, tenant):
    git = c12.register_identity(proj, tenant, "git", "git.author", syn(), "synthetic_test")
    with pytest.raises(psycopg.errors.CheckViolation, match="anchored only by an HR identity"):
        c12.register_entity(proj, tenant, "person", git)
    assert c12.register_entity(proj, tenant, "vendor", git)                   # other entity types are not people


def test_replay_is_idempotent_and_corrections_reference_the_previous_decision(db, proj, app_conn, tenant):
    ent = person(proj, tenant)
    seen = c12.register_identity(proj, tenant, "jira", "jira.assignee", syn(), "synthetic_test")
    (cand,) = c12.propose(proj, tenant, seen, [rules.Candidate(ent, "unique_initials_in_context", ("e",))])
    req = c12.request_decision(app_conn, tenant, "person:SYN2", cand, "please review")
    first = c12.decide(app_conn, tenant, "person:SYN1", cand, "approved", "same", "SYN-R-1", ["rec:1", "rec:2"],
                       request_key="syn-key-0001")
    replay = c12.decide(app_conn, tenant, "person:SYN1", cand, "approved", "same", "SYN-R-1", ["rec:1", "rec:2"],
                        request_key="syn-key-0001")
    assert replay["replayed"] and replay["decision_id"] == first["decision_id"] and replay["version"] == 1
    with pytest.raises(c12.Invalid, match="different decision"):
        c12.decide(app_conn, tenant, "person:SYN1", cand, "rejected", "x", "SYN-R-2", request_key="syn-key-0001")
    fix = c12.decide(app_conn, tenant, "person:SYN1", cand, "rejected", "evidence withdrawn", "SYN-R-3")
    with tenant_tx(app_conn, tenant):
        rows = app_conn.execute("SELECT version, decision_id, previous_decision_id, evidence_refs, decided_by"
                                " FROM judgement.resolution_decision WHERE candidate_id = %s ORDER BY version",
                                (cand,)).fetchall()
        assert app_conn.execute("SELECT requested_by FROM judgement.decision_request WHERE request_id = %s",
                                (req,)).fetchone()["requested_by"] == "person:SYN2"
    assert [r["version"] for r in rows] == [1, 2]                             # the replay wrote nothing
    assert rows[0]["previous_decision_id"] is None and str(rows[1]["previous_decision_id"]) == first["decision_id"]
    assert rows[0]["evidence_refs"] == ["rec:1", "rec:2"] and str(rows[1]["decision_id"]) == fix["decision_id"]
    with pytest.raises(psycopg.errors.CheckViolation, match="previous_decision_id"):
        with tenant_tx(app_conn, tenant):                                     # a stale reference is refused
            app_conn.execute("INSERT INTO judgement.resolution_decision (tenant_id, candidate_id, version,"
                             " decision_id, outcome, decided_by, rationale, decision_record, previous_decision_id)"
                             " VALUES (%s,%s,3,%s,'deferred','person:SYN1','x','SYN-R-4',%s)",
                             (tenant, cand, uuid.uuid4(), first["decision_id"]))
    kinds = [e["kind"] for e in audit_kinds(db, tenant)]
    assert kinds == ["identity.decision_requested", "identity.decided", "identity.decided"]
    assert all("syn-" not in str(e["detail"]) for e in audit_kinds(db, tenant))   # ids only, never values


def test_refused_approvals_are_audited_and_leave_no_decision(db, proj, app_conn, tenant):
    _ = person(proj, tenant)
    seen = c12.register_identity(proj, tenant, "jira", "jira.assignee", syn(), "synthetic_test")
    amb = person(proj, tenant)
    (cand,) = c12.propose(proj, tenant, seen, [rules.Candidate(amb, "conflicting", ("disagree",))])
    with pytest.raises(c12.NotPermitted):
        c12.decide(app_conn, tenant, "person:SYN1", cand, "approved", "x", "SYN-R-5")
    (event,) = audit_kinds(db, tenant)
    assert event["kind"] == "identity.decision_refused" and event["detail"]["outcome"] == "approved"
    with tenant_tx(app_conn, tenant):
        assert app_conn.execute("SELECT count(*) AS n FROM judgement.resolution_decision WHERE candidate_id = %s",
                                (cand,)).fetchone()["n"] == 0
    for table in ("judgement.alias_retirement", "judgement.decision_request"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with tenant_tx(app_conn, tenant):
                app_conn.execute(f"DELETE FROM {table}")
