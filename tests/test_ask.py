"""Ask Veloxa v0 (ARCHITECTURE.md §13, §19.4): refusal pre-screen, temporal parse, bounded planner, deterministic
verifier, grading/status, recording and the API. SYNTHETIC data only."""

from datetime import date, datetime, timedelta

import psycopg
import pytest
from conftest import D1_DAY

from veloxa import config, projector, tools
from veloxa.ask import pipeline, planner, policy, timeparse
from veloxa.ask.verifier import Claim, verify
from veloxa.db import connect, tenant_tx
from veloxa.temporal import Mode

IST = "Asia/Kolkata"
NOW = datetime.fromisoformat("2026-11-16T15:00:00+05:30")
PEOPLE = ["Person Alpha", "Person Beta"]

# ---------------------------------------------------------------------------------------------------------------
# Refusal pre-screen (§13.6): one case per policy, plus questions that must NOT be refused
# ---------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("question, policy_id", [
    ("What is Person Alpha's salary?", "POL-PAY-01"),
    ("Who are the top earners?", "POL-PAY-01"),
    ("Predict which employees will leave the company", "POL-PEOPLE-01"),
    ("Rank the engineers by performance rating", "POL-PEOPLE-01"),
    ("Will Person Beta quit?", "POL-PEOPLE-01"),
    ("Summarise Beta's emails", "POL-COMMS-01"),
    ("Monitor the team's chat messages", "POL-COMMS-01"),
    ("Why did Person Beta take leave?", "POL-SENS-01"),
    ("Who has the most commits?", "POL-PROD-01"),
    ("Send an email to Person Beta", "POL-ACT-01"),
    ("Can you approve the pending invoice?", "POL-ACT-01"),
])
def test_prescreen_refuses_each_catalogue_policy_with_a_reason_and_an_alternative(question, policy_id):
    r = policy.prescreen(question, PEOPLE)
    assert r is not None and r.policy.policy_id == policy_id
    assert r.policy.reason and r.policy.alternative


@pytest.mark.parametrize("question", [
    "What is our headcount by department?", "Which vendor did we pay the most?", "Has anyone quit so far?",
    "Who owns performance testing?", "What is the total payroll?", "What did the board update report as revenue?",
    "When did Person Beta join?",
])
def test_prescreen_does_not_refuse_work_level_questions(question):
    assert policy.prescreen(question, PEOPLE) is None


# ---------------------------------------------------------------------------------------------------------------
# Temporal parse (§8.3): cues only; parameters win; dates are end-of-day; a bare date is content
# ---------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("question, mode, as_of", [
    ("What did we know on 16 Nov at noon about spend?", Mode.KNOWN_AT,
     datetime.fromisoformat("2026-11-16T12:00:00+05:30")),
    ("Headcount as of 2026-11-16", Mode.KNOWN_AT, date(2026, 11, 16)),
    ("What had been recorded by Nov 15, 2026?", Mode.RECORDED_BY, date(2026, 11, 15)),
    ("What was true on 17/11/2026?", Mode.VALID_AT, date(2026, 11, 17)),
    ("Who joined on 2026-01-05?", Mode.CURRENT, None),
    ("What is the spend?", Mode.CURRENT, None),
])
def test_time_cues_are_parsed_deterministically(question, mode, as_of):
    spec = timeparse.parse(question, NOW, IST)
    assert (spec.mode, spec.as_of) == (mode, as_of)


def test_explicit_time_parameters_win_and_invalid_ones_are_typed_errors():
    spec = timeparse.parse("What did we know on 15 Nov?", NOW, IST, as_known_at="2026-11-16")
    assert (spec.mode, spec.as_of, spec.source) == (Mode.KNOWN_AT, date(2026, 11, 16), "parameters")
    for kwargs in ({"as_known_at": "yesterday-ish"}, {"mode": "VALID_AT"}, {"mode": "CURRENT", "as_of": "2026-11-16"},
                   {"as_known_at": "2026-11-16", "as_of": "2026-11-15"}, {"mode": "SOMETIME"}):
        with pytest.raises(ValueError):
            timeparse.parse("q", NOW, IST, **kwargs)


# ---------------------------------------------------------------------------------------------------------------
# Planner (§13.2): declared operations only, validated arguments, bounded steps, no guessed identities
# ---------------------------------------------------------------------------------------------------------------

_PEOPLE_ROWS = [{"full_name": "Person Alpha", "emp_id": "T001"}, {"full_name": "Person Beta", "emp_id": "T002"},
                {"full_name": "Other Beta", "emp_id": "T003"}]


@pytest.mark.parametrize("question, tools_expected", [
    ("What is our headcount by department?", [("headcount", {"by": "department"})]),
    ("Ledger spend by vendor for CC-A", [("finance_totals", {"dims": ["vendor"], "filters": {"cost_centre": "CC-A"}})]),
    ("What is the total payroll?", [("payroll_total", {})]),
    ("Who is Person Alpha?", [("person", {"name": "Person Alpha"})]),
    ("Do the sources disagree?", [("conflicts", {})]),
    ("What happened between 2099-01-01 and 2099-01-31?",
     [("timeline", {"start": date(2099, 1, 1), "end": date(2099, 1, 31)})]),
    ("synthetic launch plan", [("text_search", {"query": "synthetic OR launch OR plan"})]),
])
def test_plans_use_declared_operations_only(question, tools_expected):
    p = planner.plan(question, _PEOPLE_ROWS, 2026, IST)
    assert [(s.tool, s.args) for s in p.steps] == tools_expected
    assert p.interpretation.startswith("I read this as:")


def test_plans_are_bounded_and_never_resolve_an_ambiguous_first_name():
    p = planner.plan("Board revenue, spend, headcount, payroll and conflicts?", _PEOPLE_ROWS, 2026, IST)
    assert len(p.steps) == planner.MAX_STEPS
    p = planner.plan("Who is Person?", _PEOPLE_ROWS, 2026, IST)  # two people share this first name
    assert [(s.tool, s.args) for s in p.steps] == [("person", {"name": "Person"})]  # passed on as written


@pytest.mark.parametrize("step", [
    planner.Step("run_sql", {"query": "SELECT 1"}),
    planner.Step("finance_totals", {"dims": ["narration"]}),
    planner.Step("finance_totals", {"filters": {"amount_inr": "1"}}),
    planner.Step("headcount", {"by": "salary_band"}),
    planner.Step("person", {"name": ""}),
    planner.Step("timeline", {"start": "2099-01-01"}),
])
def test_plan_validation_rejects_undeclared_operations_and_arguments(step):
    with pytest.raises(planner.PlanError):
        planner.validate(step)


# ---------------------------------------------------------------------------------------------------------------
# Verifier (§13.4): drop or downgrade only, each claim against its own evidence
# ---------------------------------------------------------------------------------------------------------------

_REC = {"R1": {"record_id": "R1", "kind": "export", "data_class": "confidential", "excerpt": "GL row 4",
               "source_path": "gl.xlsx", "locator": {"row": 4}},
        "R2": {"record_id": "R2", "kind": "transcript", "data_class": "internal", "excerpt": "We spent about 40.",
               "source_path": "t.docx", "locator": {}},
        "R3": {"record_id": "R3", "kind": "export", "data_class": "payroll_aggregate", "excerpt": "row 9",
               "source_path": "gl.xlsx", "locator": {"row": 9}}}
_FACTS = {"F1": {"display": "Ledger spend (all): INR 30", "value": 30, "unit": "INR", "inputs": ["R1"],
                 "qualifiers": []}}
_VISIBLE = {"R1", "R2", "R3"}


def _verify(claim, visible=_VISIBLE):
    return verify([claim], _FACTS, _REC, visible, PEOPLE)


def test_a_verbatim_fact_claim_is_supported():
    out = _verify(Claim("Ledger spend (all): INR 30", "derived", ["F1", "R1"]))
    assert [c.verification for c in out.kept] == ["supported"]


@pytest.mark.parametrize("claim, why", [
    (Claim("Ledger spend (all): INR 31", "derived", ["F1"]), "number"),
    (Claim("Person Alpha approved spend of INR 30", "derived", ["F1"]), "names a person"),
    (Claim("Ledger spend: INR 30", "derived", ["F9"]), "not in the evidence pack"),
    (Claim("Ledger spend: INR 30", "derived", []), "not in the evidence pack"),
])
def test_unsupported_claims_are_dropped(claim, why):
    out = _verify(claim)
    assert not out.kept and why in out.dropped[0].notes[0]


def test_a_claim_citing_a_record_invisible_at_the_basis_is_dropped():
    out = _verify(Claim("Ledger spend (all): INR 30", "derived", ["F1"]), visible={"R2"})
    assert not out.kept and "not visible" in out.dropped[0].notes[0]


def test_policy_scan_drops_protected_classes_and_leave_types_and_raises_policy_events():
    out = verify([Claim("row 9", "record", ["R3"]), Claim("GL row 4 mentions sick leave", "record", ["R1"])],
                 _FACTS, _REC, _VISIBLE, PEOPLE)
    assert not out.kept
    assert {e["check"] for e in out.policy_events} == {"protected_class_cited", "leave_type_mentioned"}


def test_a_claim_stronger_than_its_weakest_source_is_downgraded_not_upgraded():
    out = _verify(Claim("We spent about 40.", "derived", ["R2"]))
    assert out.kept[0].kind == "statement" and out.kept[0].verification == "downgraded"


@pytest.mark.parametrize("refused, grade, qualifiers, conflict, status", [
    (True, "A", [], True, "refused"), (False, "D", [], True, "abstained"), (False, "A", [], True, "conflict"),
    (False, "A", ["as at extraction"], False, "qualified"), (False, "B", [], False, "qualified"),
    (False, "A", [], False, "answered"),
])
def test_status_precedence(refused, grade, qualifiers, conflict, status):
    assert pipeline._status(refused, grade, qualifiers, conflict) == status


# ---------------------------------------------------------------------------------------------------------------
# Database: the whole pipeline over a synthetic sealed batch
# ---------------------------------------------------------------------------------------------------------------

ALPHA = tools.Principal("person:T001", "executive", "alpha@example.test")
OUTSIDER = tools.Principal("person:T999", "executive", "nobody@example.test")


@pytest.fixture
def projected(db, ingest_conn, tenant, schedule, synthetic_root):
    from veloxa.ingest.pipeline import admit_pack

    admit_pack(ingest_conn, tenant, schedule, "D1", synthetic_root, D1_DAY)
    with connect(db.dsn("projection")) as conn:
        projector.build(conn, tenant)


def _ask(app_conn, tenant, question, principal=ALPHA, **kw):
    from veloxa.releases import current_time

    return pipeline.answer(app_conn, tenant, IST, principal, question, current_time(), **kw)


def _cited(answer):
    return {e["record_id"] for e in answer["evidence"]}


@pytest.mark.db
def test_a_headcount_answer_cites_governed_facts_and_is_qualified_in_facts_only_mode(app_conn, tenant, projected):
    a = _ask(app_conn, tenant, "How many people work here?")
    assert a["status"] == "qualified" and a["grade"]["value"] == "A"
    assert pipeline.FACTS_ONLY in a["qualifiers"]
    head = a["claims"][0]
    assert head["text"] == "People in the HR export: 2" and head["verification"] == "supported"
    fact = next(f for f in a["facts"] if f["fact_id"] == head["evidence"][0])
    assert fact["metric"] == "headcount@1" and fact["value"] == 2
    assert set(fact["inputs"]) <= _cited(a) and all(e["data_class"] == "personal" for e in a["evidence"])
    assert a["read_basis"]["batch_seq"] == 1 and a["read_basis"]["mode"] == "CURRENT"
    assert a["interpretation"].startswith("I read this as: headcount")


@pytest.mark.db
def test_spend_answers_exclude_payroll_and_say_so(app_conn, tenant, projected):
    a = _ask(app_conn, tenant, "What is the ledger spend?")
    assert a["claims"][0]["text"].endswith("INR 30")
    assert "payroll lines excluded (disclosure-controlled)" in a["qualifiers"]
    assert all(e["data_class"] not in ("payroll_aggregate", "sensitive_personal") for e in a["evidence"])


@pytest.mark.db
@pytest.mark.parametrize("question, policy_id", [
    ("What is Person Beta's salary?", "POL-PAY-01"),
    ("What is the total payroll?", "POL-PAY-02"),
    ("Rank people by attrition risk", "POL-PEOPLE-01"),
])
def test_refusals_are_successful_answers_with_policy_reason_and_alternative(app_conn, tenant, projected, question,
                                                                            policy_id):
    a = _ask(app_conn, tenant, question)
    assert a["status"] == "refused" and a["refusal"]["policy"] == policy_id
    assert a["refusal"]["alternative"] and a["claims"] == [] and a["evidence"] == [] and a["facts"] == []


@pytest.mark.db
@pytest.mark.parametrize("question, unknown", [("Who is Person?", "ambiguous"),
                                                ("Who is Persn Alpha?", "no person in the HR export matches")])
def test_unresolved_or_ambiguous_identities_abstain_instead_of_guessing(app_conn, tenant, projected, question,
                                                                        unknown):
    a = _ask(app_conn, tenant, question)
    assert a["status"] == "abstained" and a["grade"]["value"] == "D" and a["claims"] == []
    assert any(unknown in u for u in a["unknowns"])


@pytest.mark.db
def test_person_answers_are_pronoun_free_record_claims(app_conn, tenant, projected):
    a = _ask(app_conn, tenant, "Who is Person Beta and who do they report to?")
    texts = [c["text"] for c in a["claims"]]
    assert "Person Beta reports to Person Alpha (Lead Tester)" in texts
    assert all(c["kind"] == "record" and c["grade"] == "A" for c in a["claims"])


@pytest.mark.db
def test_a_basis_before_any_admission_abstains_and_explains(app_conn, tenant, projected):
    a = _ask(app_conn, tenant, "What is the headcount?", as_known_at="2026-11-15")
    assert a["status"] == "abstained" and a["read_basis"]["batch_seq"] == 0
    assert any("RECORDED_BY" in u for u in a["unknowns"])


@pytest.mark.db
def test_an_invalid_time_is_a_typed_error(app_conn, tenant, projected):
    with pytest.raises(pipeline.InvalidTime):
        _ask(app_conn, tenant, "headcount", as_known_at="2999-01-01")


@pytest.mark.db
def test_private_emails_are_cited_only_to_their_parties(app_conn, tenant, projected):
    email = "email:data_room/email/01.eml#document"
    assert email in _cited(_ask(app_conn, tenant, "synthetic"))
    outsider = _ask(app_conn, tenant, "synthetic", principal=OUTSIDER)
    assert email not in _cited(outsider)


@pytest.mark.db
def test_every_answer_is_recorded_with_citations_and_a_chained_audit_event(db, app_conn, tenant, projected):
    from veloxa import audit

    a = _ask(app_conn, tenant, "What is the ledger spend?")
    r = _ask(app_conn, tenant, "What is Person Beta's salary?")
    with tenant_tx(app_conn, tenant):
        rows = {x["answer_id"].hex: x for x in app_conn.execute("SELECT * FROM runtime.answer")}
        cites = {x["record_id"] for x in app_conn.execute(
            "SELECT record_id FROM runtime.answer_citation WHERE answer_id = %s", (a["audit_id"],))}
    ra = rows[a["audit_id"].replace("-", "")]
    assert ra["status"] == "qualified" and ra["answer"]["answer"] == a["answer"] and cites == _cited(a)
    assert rows[r["audit_id"].replace("-", "")]["policy_id"] == "POL-PAY-01"
    with connect(db.admin_dsn) as admin, tenant_tx(admin, tenant):
        # the administrator bypasses RLS, so the tenant is filtered explicitly here
        kinds = [e["kind"] for e in admin.execute("SELECT kind FROM audit.event WHERE tenant_id = %s ORDER BY seq",
                                                  (tenant,))]
        details = [e["detail"] for e in admin.execute(
            "SELECT detail FROM audit.event WHERE tenant_id = %s AND kind = 'ask.answered'", (tenant,))]
        assert audit.verify_chain(admin, tenant)
    assert kinds.count("ask.answered") == 2
    assert all("question" not in d and "answer" not in d for d in details)  # structure and counts only


@pytest.mark.db
def test_the_app_role_cannot_rewrite_answers_or_read_audit_details(app_conn, tenant, projected):
    a = _ask(app_conn, tenant, "What is the ledger spend?")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with tenant_tx(app_conn, tenant):
            app_conn.execute("UPDATE runtime.answer SET status = 'answered' WHERE answer_id = %s", (a["audit_id"],))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with tenant_tx(app_conn, tenant):
            app_conn.execute("SELECT detail FROM audit.event")


@pytest.mark.db
def test_answers_do_not_leak_later_material(db, app_conn, tenant, tmp_path):
    """Leakage canaries for /v1/ask across a real two-batch boundary (§8.5)."""
    import canary
    from synthetic_pack import make_d1
    from test_temporal import D2_CANARIES, _make_synthetic_d2

    from veloxa import ledger
    from veloxa.ingest.pipeline import admit_pack
    from veloxa.releases import Pack, Schedule, current_time
    from veloxa.temporal import resolve_basis

    d1_at = datetime.fromisoformat("2026-11-16T08:00:00+05:30")
    d2_at = datetime.fromisoformat("2026-11-16T09:00:00+05:30")
    sched = Schedule("canary", IST, (Pack("D1", 1, "Day1_Mon_16Nov", d1_at), Pack("D2", 2, "Day2_Tue_17Nov", d2_at)))
    make_d1(tmp_path)
    _make_synthetic_d2(tmp_path)
    with connect(db.dsn("ingest")) as ing:
        for p in ("D1", "D2"):
            admit_pack(ing, tenant, sched, p, tmp_path, current_time())
    with connect(db.dsn("projection")) as prj:
        projector.build(prj, tenant)
    releases = ledger.sealed_releases(app_conn, tenant)
    now = current_time()
    bases = {1: resolve_basis(tenant, releases, Mode.KNOWN_AT, d2_at - timedelta(seconds=1), IST, now),
             2: resolve_basis(tenant, releases, Mode.CURRENT, None, IST, now)}

    def ask_surface(question):
        def surface(conn, basis):
            kw = {} if basis.mode == Mode.CURRENT else {"as_known_at": basis.as_of}
            return _cited(_ask(conn, tenant, question, **kw))
        return surface

    surfaces = {"ask:text": ask_surface("synthetic OR canary"), "ask:timeline": ask_surface("What happened?")}
    universe_d1 = {"email:data_room/email/01.eml#document"}
    report = canary.run(surfaces, app_conn, bases, {1: universe_d1, 2: D2_CANARIES})
    assert not report.leaks, report.leaks


# ---------------------------------------------------------------------------------------------------------------
# API: POST /v1/ask
# ---------------------------------------------------------------------------------------------------------------


@pytest.mark.db
def test_ask_endpoint_answers_as_the_configured_principal_and_returns_typed_errors(db, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from synthetic_pack import make_d1

    from veloxa.api import PRIVILEGED_ENV, create_app
    from veloxa.ingest.pipeline import admit_pack
    from veloxa.releases import load_schedule

    schedule = load_schedule()
    tid = config.tenant_uuid(schedule.tenant_slug)
    with connect(db.dsn("app")) as conn, tenant_tx(conn, tid):
        admitted = conn.execute("SELECT count(*) AS n FROM ledger.admission_batch").fetchone()["n"]
    if not admitted:  # the configured tenant may already hold the session's synthetic D1 (test_api)
        make_d1(tmp_path)
        with connect(db.dsn("ingest")) as conn:
            admit_pack(conn, tid, schedule, "D1", tmp_path, D1_DAY)
    with connect(db.dsn("projection")) as conn:
        projector.build(conn, tid)
    for name in PRIVILEGED_ENV:
        monkeypatch.delenv(name, raising=False)
    clock = datetime.fromisoformat("2026-11-16T15:00:00+05:30")
    monkeypatch.setenv("VELOXA_PRINCIPAL_TITLE", "Lead Tester")
    with TestClient(create_app(dsn=db.dsn("app"), clock=lambda: clock)) as client:
        ok = client.post("/v1/ask", json={"question": "What is the ledger spend?"})
        assert ok.status_code == 200 and ok.json()["status"] == "qualified" and ok.json()["audit_id"]
        refused = client.post("/v1/ask", json={"question": "What is Person Beta's salary?"}).json()
        assert refused["status"] == "refused" and refused["refusal"]["policy"] == "POL-PAY-01"
        bad = client.post("/v1/ask", json={"question": "headcount", "as_known_at": "2999-01-01"})
        assert bad.status_code == 422 and bad.json()["error"] == "invalid_time"
        assert client.post("/v1/ask", json={"question": "q", "tenant_id": str(tid)}).status_code == 422
    monkeypatch.setenv("VELOXA_PRINCIPAL_TITLE", "Tester-in-chief")
    with TestClient(create_app(dsn=db.dsn("app"), clock=lambda: clock)) as client:
        denied = client.post("/v1/ask", json={"question": "What is the ledger spend?"})
        assert denied.status_code == 403 and denied.json()["error"] == "not_authorised"
