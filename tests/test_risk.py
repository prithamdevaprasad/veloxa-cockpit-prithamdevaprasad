"""Risk engine v0 and the interim briefing (ARCHITECTURE.md §16, §19.3): unweighted partial-order comparison,
the D-15 governance gate, immutable briefing snapshots, release and tenant boundaries, and requester
authentication. SYNTHETIC data only."""

from datetime import date, datetime, timedelta

import psycopg
import pytest
from conftest import DEV_TOKEN, api_client, dev_fixture, ensure_configured_d1

from veloxa import auth, ledger, projector, tools
from veloxa.db import connect, tenant_tx
from veloxa.risk import briefing, detectors, governance
from veloxa.risk.compare import compare, partial_order
from veloxa.risk.model import (
    MEASURED,
    MISSING,
    NOT_APPLICABLE,
    UNCERTAIN,
    MeasureDefinition,
    MeasureValue,
    Risk,
)
from veloxa.temporal import Mode, resolve_basis

IST = "Asia/Kolkata"
PEOPLE = MeasureDefinition("C4", "C4.absolute_discrepancy", 1, "people", "d")
LINES = MeasureDefinition("C4", "C4.absolute_discrepancy", 1, "lines", "d")
PEOPLE_V2 = MeasureDefinition("C4", "C4.absolute_discrepancy", 2, "people", "d")
INR = MeasureDefinition("C2", "C2.amount_at_stake", 1, "INR", "d")
DAYS = MeasureDefinition("C5", "C5.days_to_commitment", 1, "days", "d")


def m(definition, low, high=None):
    return MeasureValue(MEASURED, definition, low, low if high is None else high, ("REC",))


def risk(rid, **measures):
    full = {c: MeasureValue(NOT_APPLICABLE) for c in ("C1", "C2", "C3", "C4", "C5")} | measures
    return Risk(rid, "test@1", "object", "synthetic risk", full, ["REC"], {})


# ---------------------------------------------------------------------------------------------------------------
# Unweighted comparison (pure)
# ---------------------------------------------------------------------------------------------------------------


def test_a_risk_that_is_at_least_as_bad_everywhere_and_worse_somewhere_dominates():
    a = risk("R-a", C2=m(INR, 100), C5=m(DAYS, 10))
    b = risk("R-b", C2=m(INR, 50), C5=m(DAYS, 10))   # equal on C5, less exposure on C2
    o = compare(a, b)
    assert o.relation == "dominates" and "worse on C2" in o.explanation
    assert compare(b, a).relation == "dominated_by"


def test_lower_is_worse_for_time_proximity():
    sooner, later = risk("R-a", C5=m(DAYS, 5)), risk("R-b", C5=m(DAYS, 40))
    assert compare(sooner, later).relation == "dominates"


def test_risks_that_trade_off_across_criteria_are_incomparable():
    a = risk("R-a", C2=m(INR, 100), C5=m(DAYS, 30))   # more money at stake
    b = risk("R-b", C2=m(INR, 50), C5=m(DAYS, 10))    # sooner
    o = compare(a, b)
    assert o.relation == "trade_off" and "D-15" in o.explanation


@pytest.mark.parametrize("state", [MISSING, UNCERTAIN])
def test_missing_or_uncertain_measures_block_comparison_and_are_never_read_as_zero(state):
    a = risk("R-a", C4=m(PEOPLE, 9), C2=MeasureValue(state, note="no amount"))
    b = risk("R-b", C4=m(PEOPLE, 1), C2=m(INR, 10))
    # Reading the missing C2 as zero would make neither dominate; reading it as "worst" would make R-a dominate.
    o = compare(a, b)
    assert o.relation == "not_comparable" and f"C2 is {state} for R-a" in o.explanation


def test_not_applicable_on_one_side_is_not_zero():
    o = compare(risk("R-a", C4=m(PEOPLE, 5)), risk("R-b", C4=m(PEOPLE, 1), C2=m(INR, 10)))
    assert o.relation == "not_comparable" and "not applicable for R-a" in o.explanation


@pytest.mark.parametrize("other", [LINES, PEOPLE_V2])
def test_different_measure_definitions_or_units_are_not_compared(other):
    o = compare(risk("R-a", C4=m(PEOPLE, 5)), risk("R-b", C4=m(other, 1)))
    assert o.relation == "not_comparable" and "different measure definitions" in o.explanation


@pytest.mark.parametrize("a, b", [((5, 5), (5, 5)), ((3, 8), (5, 6))])
def test_equal_or_overlapping_values_are_indistinguishable_with_no_tie_break(a, b):
    o = compare(risk("R-a", C4=m(PEOPLE, *a)), risk("R-b", C4=m(PEOPLE, *b)))
    assert o.relation == "indistinguishable" and "no tie-break" in o.explanation


def test_interval_dominance_needs_the_whole_interval_to_be_worse():
    assert compare(risk("R-a", C4=m(PEOPLE, 6, 9)), risk("R-b", C4=m(PEOPLE, 2, 5))).relation == "dominates"
    assert compare(risk("R-a", C4=m(PEOPLE, 5, 9)), risk("R-b", C4=m(PEOPLE, 2, 6))).relation == "indistinguishable"


def test_the_partial_order_is_never_completed_into_a_ranking():
    a, b = risk("R-a", C4=m(PEOPLE, 9)), risk("R-b", C4=m(PEOPLE, 1))
    c = risk("R-c", C4=m(LINES, 4))                               # incomparable with both
    starved = risk("R-d", C4=MeasureValue(MISSING, note="x"))     # insufficient: nothing measured
    per = partial_order([a, b, c, starved])["per_risk"]
    assert "R-d" not in per
    assert per["R-a"]["dominates"] == ["R-b"] and per["R-b"]["dominated_by"] == ["R-a"]
    assert per["R-c"]["dominated_by"] == [] and set(per["R-c"]["not_comparable_with"]) == {"R-a", "R-b"}
    assert starved.quantification_state == "insufficient" and a.quantification_state == "quantified"


def test_values_cannot_hide_numbers_in_missing_states_or_skip_criteria():
    with pytest.raises(ValueError):
        MeasureValue(MISSING, low=0, high=0)
    with pytest.raises(ValueError):
        MeasureValue(MEASURED, PEOPLE, 5, 1)
    with pytest.raises(ValueError):
        Risk("R-x", "t@1", "o", "d", {"C4": m(PEOPLE, 1)}, [], {})


# ---------------------------------------------------------------------------------------------------------------
# D-15 governance gate (pure)
# ---------------------------------------------------------------------------------------------------------------


def test_weighted_ranking_is_blocked_while_d15_is_pending_and_uses_only_recorded_content_after_approval():
    from pathlib import Path

    from veloxa.risk import ranking

    assert governance.ranking_mode(governance.PENDING) == "unweighted_partial_order"
    with pytest.raises(governance.RankingBlocked, match="pending an owner decision"):
        governance.weighted_ranking(governance.PENDING, [])
    approved = governance.PolicyState("approved", 1, "0" * 64, "owner", date(2026, 11, 17), "DECISIONS.md D-15")
    assert governance.ranking_mode(approved) == "unweighted_partial_order"   # the briefing's top_three stays null
    with pytest.raises(governance.RankingBlocked):                           # no recorded content: never ranks
        governance.weighted_ranking(approved, [])
    content = ranking.load_policy(Path(__file__).parent / "fixtures" / "d15_test_policy.toml")
    assert governance.weighted_ranking(approved, [], content) == {
        "policy": {"weights": content["weights"], "method": content["method"], "probabilities": "disabled"},
        "ranked": [], "unrankable": []}


def _decision_file(tmp_path, **overrides):
    d = {"decision_ref": '"D-15"', "status": '"approved"', "approved_by": '"Owner"', "approved_on": "2026-11-17",
         "decision_record": '"DECISIONS.md D-15 (synthetic test)"'} | overrides
    body = "[decision]\n" + "".join(f"{k} = {v}\n" for k, v in d.items() if v is not None)
    body += "[content]\nweights = {}\nanchors = {}\nguardrail_tiers = []\ngate_thresholds = {}\n"
    p = tmp_path / "d15.toml"
    p.write_text(body, encoding="utf-8")
    return p


@pytest.mark.parametrize("overrides", [{"status": '"pending"'}, {"approved_by": '""'}, {"decision_ref": '"D-14"'},
                                       {"approved_on": None}, {"decision_record": None}])
def test_only_a_complete_owner_approved_d15_decision_file_is_accepted(tmp_path, overrides):
    with pytest.raises(ValueError):
        governance.load_decision_file(_decision_file(tmp_path, **overrides))


def test_figure_pairings_need_owner_approval_and_none_are_approved_yet(tmp_path):
    assert detectors.load_mappings() == []
    p = tmp_path / "m.toml"
    p.write_text('[[mapping]]\nreported_figure = "Headcount"\nmetric = "headcount"\nunit = "people"\n',
                 encoding="utf-8")
    with pytest.raises(ValueError, match="owner approval"):
        detectors.load_mappings(p)


# ---------------------------------------------------------------------------------------------------------------
# Requester authentication (pure)
# ---------------------------------------------------------------------------------------------------------------


def test_authentication_fails_closed_by_default_and_the_fixture_needs_every_explicit_setting():
    assert auth.settings_from_env({}).mode == "none"
    assert auth.settings_from_env({"VELOXA_PRINCIPAL_TITLE": "CEO"}).authenticate(f"Bearer {DEV_TOKEN}") is None
    for env in ({"VELOXA_DEV_AUTH": "1"}, {"VELOXA_DEV_AUTH": "1", "VELOXA_DEV_TOKEN": "short",
                                           "VELOXA_DEV_PRINCIPAL": "T001"},
                {"VELOXA_DEV_AUTH": "1", "VELOXA_DEV_TOKEN": DEV_TOKEN}, {"VELOXA_DEV_AUTH": "yes"}):
        with pytest.raises(auth.AuthConfigError):
            auth.settings_from_env(env)
    s = auth.settings_from_env(dev_fixture("T001"))
    assert s.authenticate(f"Bearer {DEV_TOKEN}") == "T001"
    for bad in (None, "", DEV_TOKEN, f"Basic {DEV_TOKEN}", "Bearer wrong", f"Bearer {DEV_TOKEN}x"):
        assert s.authenticate(bad) is None


# ---------------------------------------------------------------------------------------------------------------
# Database: detector, briefing snapshots, release and tenant boundaries
# ---------------------------------------------------------------------------------------------------------------

D1_AT = datetime.fromisoformat("2026-11-16T08:00:00+05:30")
D2_AT = datetime.fromisoformat("2026-11-16T09:00:00+05:30")
MAPPINGS = [detectors.FigureMapping("Headcount", "headcount", "people", "Owner", date(2026, 11, 16), "test"),
            detectors.FigureMapping("Ledger lines", "ledger_line_count", "lines", "Owner", date(2026, 11, 16), "test")]
ALPHA = tools.Principal("person:T001", "authenticated", "alpha@example.test")


@pytest.fixture
def two_batches(db, ingest_conn, tenant, tmp_path):
    """D1 (2 people, 2 visible ledger lines); D2 adds two board updates that report figures differently."""
    from synthetic_pack import make_board_update, make_d1

    from veloxa.ingest.pipeline import admit_pack
    from veloxa.releases import Pack, Schedule, current_time

    sched = Schedule("risk", IST, (Pack("D1", 1, "Day1_Mon_16Nov", D1_AT), Pack("D2", 2, "Day2_Tue_17Nov", D2_AT)))
    make_d1(tmp_path)
    d2 = tmp_path / "Day2_Tue_17Nov"
    make_board_update(d2, [("Headcount", "3"), ("Ledger lines", "5")], "Board_Q1.pdf")
    make_board_update(d2, [("Headcount", "7"), ("Ledger lines", "9 approx")], "Board_Q2.pdf", "Apr to Jun 2099",
                      "9 Jul 2099")
    for p in ("D1", "D2"):
        admit_pack(ingest_conn, tenant, sched, p, tmp_path, current_time())
    with connect(db.dsn("projection")) as prj:
        projector.build(prj, tenant)
    return tenant


def _basis(app_conn, tenant, mode=Mode.CURRENT, at=None):
    from veloxa.releases import current_time

    return resolve_basis(tenant, ledger.sealed_releases(app_conn, tenant), mode, at, IST, current_time())


def _serve(app_conn, tenant, basis, mappings=MAPPINGS):
    from veloxa.releases import current_time

    return briefing.serve(app_conn, tenant, ALPHA, basis, current_time(), mappings)


def _keys(obj):
    if isinstance(obj, dict):
        return set(obj) | {k for v in obj.values() for k in _keys(v)}
    if isinstance(obj, list):
        return {k for v in obj for k in _keys(v)}
    return set()


@pytest.mark.db
def test_the_briefing_is_an_unranked_watchlist_with_evidence_and_a_partial_order(app_conn, two_batches):
    b = _serve(app_conn, two_batches, _basis(app_conn, two_batches))
    assert b["ranking_mode"] == "unweighted_partial_order" and b["owner_decision_status"] == "D-15 pending"
    assert b["top_three"] is None and "D-15" in b["top_three_explanation"]
    assert "NO priority" in b["display_order"]
    ids = [r["risk_id"] for r in b["watchlist"]]
    assert ids == sorted(ids) and len(ids) == 3  # Q1 headcount (1), Q2 headcount (5), Q1 ledger lines (3)
    by_obj = {r["business_object"]: r for r in b["watchlist"]}
    q1 = by_obj["reported figure 'Headcount' (Jan to Mar 2099)"]
    q2 = by_obj["reported figure 'Headcount' (Apr to Jun 2099)"]
    lines = by_obj["reported figure 'Ledger lines' (Jan to Mar 2099)"]
    assert q2["measures"]["C4"]["interval"] == [5, 5] and q1["measures"]["C4"]["interval"] == [1, 1]
    assert q2["comparison"]["dominates"] == [q1["risk_id"]] and q1["comparison"]["group"] == "dominated"
    assert q2["comparison"]["group"] == "not_dominated" and "not a priority tier" in q2["comparison"]["explanation"]
    assert set(lines["comparison"]["not_comparable_with"]) == {q1["risk_id"], q2["risk_id"]}
    for r in b["watchlist"]:
        kinds = {e["kind"] for e in r["evidence"]}
        assert kinds == {"document", "export"} and all(e["data_class"] != "payroll_aggregate" for e in r["evidence"])
        assert r["time_context"]["reported_period"] and "does not say which source is correct" in r["description"]
    assert any("not a plain number" in n for n in b["signals_needing_data"])  # "about 40" is not compared
    assert any(n.startswith("C3") for n in b["signals_needing_data"])
    assert not {"score", "rank", "priority", "weight", "weights", "severity"} & _keys(b)


@pytest.mark.db
def test_the_briefing_respects_release_and_tenant_boundaries(db, app_conn, two_batches):
    import uuid

    before_d2 = _serve(app_conn, two_batches, _basis(app_conn, two_batches, Mode.KNOWN_AT,
                                                     D2_AT - timedelta(seconds=1)))
    assert before_d2["read_basis"]["batch_seq"] == 1 and before_d2["watchlist"] == []
    current = _serve(app_conn, two_batches, _basis(app_conn, two_batches))
    assert all(e["batch_seq"] <= 2 for r in current["watchlist"] for e in r["evidence"])
    other = uuid.uuid4()
    with psycopg.connect(db.admin_dsn, autocommit=True) as admin:
        admin.execute("INSERT INTO directory.tenant (tenant_id, slug, synthetic, time_zone) VALUES (%s, %s, true,"
                      " 'UTC')", (other, f"t-{other.hex[:12]}"))
    empty = _serve(app_conn, other, _basis(app_conn, other))
    assert empty["watchlist"] == [] and empty["read_basis"]["batch_seq"] == 0
    with tenant_tx(app_conn, other):
        assert app_conn.execute("SELECT count(*) AS n FROM runtime.briefing_snapshot WHERE tenant_id = %s",
                                (two_batches,)).fetchone()["n"] == 0  # RLS: no cross-tenant snapshot visible


@pytest.mark.db
def test_snapshots_are_reused_immutable_and_keep_the_policy_version_used(db, app_conn, two_batches, tmp_path):
    basis = _basis(app_conn, two_batches)
    first = _serve(app_conn, two_batches, basis)
    assert _serve(app_conn, two_batches, basis)["snapshot_id"] == first["snapshot_id"]  # identical: reused
    assert first["policy"]["status"] == "pending" and first["policy"]["version"] is None

    with connect(db.admin_dsn) as admin:  # the owner records D-15 version 1 through the privileged command
        assert governance.record_decision(admin, two_batches, _decision_file(tmp_path)) == 1
    later = _serve(app_conn, two_batches, basis)
    assert later["snapshot_id"] != first["snapshot_id"] and later["policy"]["version"] == 1
    assert later["ranking_mode"] == "unweighted_partial_order" and later["top_three"] is None
    assert "approved (version 1)" in later["owner_decision_status"]

    kept = briefing.fetch(app_conn, two_batches, ALPHA, first["snapshot_id"])
    assert kept == first and kept["policy"]["status"] == "pending"   # history is not rewritten
    with pytest.raises(psycopg.errors.InsufficientPrivilege), tenant_tx(app_conn, two_batches):
        app_conn.execute("UPDATE runtime.briefing_snapshot SET payload = '{}'")
    with pytest.raises(psycopg.errors.InsufficientPrivilege), tenant_tx(app_conn, two_batches):
        app_conn.execute("INSERT INTO policy.ranking_decision (tenant_id, version, decision_ref, status, approved_by,"
                         " approved_on, decision_record, content, content_sha256) VALUES (%s, 2, 'D-15', 'approved',"
                         " 'x', '2026-11-17', 'x', '{}', %s)", (two_batches, "0" * 64))
    with pytest.raises(psycopg.errors.CheckViolation), connect(db.admin_dsn) as admin, tenant_tx(admin, two_batches):
        admin.execute("INSERT INTO policy.ranking_decision (tenant_id, version, decision_ref, status, approved_by,"
                      " approved_on, decision_record, content, content_sha256) VALUES (%s, 5, 'D-15', 'approved',"
                      " 'x', '2026-11-17', 'x', '{}', %s)", (two_batches, "0" * 64))  # versions cannot skip
    with pytest.raises(psycopg.errors.CheckViolation), connect(db.admin_dsn) as admin, tenant_tx(admin, two_batches):
        admin.execute("INSERT INTO runtime.briefing_snapshot (tenant_id, snapshot_id, created_at, principal_id,"
                      " batch_seq, mode, engine_version, ranking_mode, policy_status, input_hash, payload) VALUES"
                      " (%s, gen_random_uuid(), now(), 'p', 1, 'CURRENT', 'x', 'weighted', 'pending', %s, '{}')",
                      (two_batches, "0" * 64))  # weighted ranking can never be recorded without approval


@pytest.mark.db
def test_with_no_owner_approved_pairing_nothing_is_detected(app_conn, two_batches):
    b = _serve(app_conn, two_batches, _basis(app_conn, two_batches), mappings=[])
    assert b["watchlist"] == [] and any("no owner-approved pairing" in n for n in b["signals_needing_data"])


# ---------------------------------------------------------------------------------------------------------------
# API: authentication and the briefing endpoint
# ---------------------------------------------------------------------------------------------------------------

SPOOFED = [{}, {"X-Principal-Title": "CEO"}, {"X-User-Id": "T001", "X-Role": "CEO"},
           {"Authorization": "Bearer CEO"}, {"Authorization": f"Basic {DEV_TOKEN}"}]


@pytest.mark.db
@pytest.mark.parametrize("headers", SPOOFED)
def test_unauthenticated_requesters_get_nothing_by_default(db, monkeypatch, tmp_path, headers):
    ensure_configured_d1(db, tmp_path)
    monkeypatch.setenv("VELOXA_PRINCIPAL_TITLE", "CEO")  # the old title-based setting no longer grants anything
    with api_client(db, monkeypatch, VELOXA_PRINCIPAL_TITLE="CEO") as client:
        assert client.get("/health").json()["auth"] == "none"
        for r in (client.post("/v1/ask", json={"question": "What is the ledger spend?"}, headers=headers),
                  client.get("/v1/briefing", headers=headers)):
            assert r.status_code == 401 and r.json()["error"] == "not_authenticated"
            assert "claims" not in r.json() and "watchlist" not in r.json()


@pytest.mark.db
@pytest.mark.parametrize("headers", SPOOFED)
def test_with_the_fixture_enabled_only_the_token_authenticates(db, monkeypatch, tmp_path, headers):
    ensure_configured_d1(db, tmp_path)
    with api_client(db, monkeypatch, **dev_fixture("T001")) as client:
        assert client.get("/health").json()["auth"] == "development_fixture"
        assert client.get("/v1/briefing", headers=headers).status_code == 401
        r = client.post("/v1/ask", json={"question": "q", "principal": "T001"},
                        headers={"Authorization": f"Bearer {DEV_TOKEN}"})
        assert r.status_code == 422  # the body cannot carry an identity


@pytest.mark.db
def test_a_half_configured_fixture_stops_the_api(db, monkeypatch):
    with pytest.raises(auth.AuthConfigError):
        api_client(db, monkeypatch, VELOXA_DEV_AUTH="1", VELOXA_DEV_PRINCIPAL="T001")


@pytest.mark.db
def test_briefing_endpoint_returns_and_replays_snapshots_and_ignores_ranking_parameters(db, monkeypatch, tmp_path):
    import uuid

    ensure_configured_d1(db, tmp_path)
    auth_header = {"Authorization": f"Bearer {DEV_TOKEN}"}
    with api_client(db, monkeypatch, **dev_fixture("T001")) as client:
        b = client.get("/v1/briefing", headers=auth_header).json()
        assert b["top_three"] is None and b["ranking_mode"] == "unweighted_partial_order"
        assert b["owner_decision_status"] == "D-15 pending" and b["watchlist"] == []
        tampered = client.get("/v1/briefing?weights=C1:9&top_three=3&tiers=x&ranking_mode=weighted",
                              headers=auth_header).json()
        assert tampered["snapshot_id"] == b["snapshot_id"] and tampered["top_three"] is None
        assert client.get(f"/v1/briefing/{b['snapshot_id']}", headers=auth_header).json() == b
        assert client.get(f"/v1/briefing/{uuid.uuid4()}", headers=auth_header).status_code == 404
        bad = client.get("/v1/briefing?as_known_at=2999-01-01", headers=auth_header)
        assert bad.status_code == 422 and bad.json()["error"] == "invalid_time"
    with api_client(db, monkeypatch, **dev_fixture("T002")) as client:  # another principal cannot replay it
        assert client.get(f"/v1/briefing/{b['snapshot_id']}", headers=auth_header).status_code == 404
