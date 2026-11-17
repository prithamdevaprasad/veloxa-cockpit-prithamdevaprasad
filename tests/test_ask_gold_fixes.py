"""Regressions for defects the Sprint 5 gold expansion found. SYNTHETIC data only."""

import pytest
from test_ask import IST, _ask, projected  # noqa: F401  (fixture re-export)

from veloxa.ask import pipeline, planner, policy

PEOPLE = ["Person Alpha", "Person Beta"]


@pytest.mark.parametrize("question, policy_id", [
    ("Who commits the most code?", "POL-PROD-01"),
    ("Which engineer writes the most code?", "POL-PROD-01"),
    ("How much does the CTO earn?", "POL-PAY-01"),
    ("What is the head of sales paid?", "POL-PAY-01"),
])
def test_role_titles_and_code_volume_are_recognised(question, policy_id):
    r = policy.prescreen(question, PEOPLE)
    assert r is not None and r.policy.policy_id == policy_id


@pytest.mark.parametrize("question", ["What did the CTO say about the launch?", "How much did we spend on vendors?",
                                      "Which modules have the most code changes?"])
def test_work_level_questions_are_still_not_refused(question):
    r = policy.prescreen(question, PEOPLE)
    assert r is None or r.policy.policy_id != "POL-PAY-01"


def test_a_named_department_narrows_headcount_and_a_named_cost_centre_filters_spend():
    people = [{"emp_id": "T001", "full_name": "Person Alpha", "department": "Testing"},
              {"emp_id": "T002", "full_name": "Person Beta", "department": "People & Ops"}]
    (step,) = [s for s in planner.plan("How many employees are in Testing?", people, 2026, IST).steps
               if s.tool == "headcount"]
    assert step.args == {"by": "department"}
    (step,) = [s for s in planner.plan("How many people are in People & Ops?", people, 2026, IST).steps
               if s.tool == "headcount"]
    assert step.args == {"by": "department"}
    (spend,) = [s for s in planner.plan("What is the ledger spend for cost centre ENG-INF?", people, 2026, IST).steps
                if s.tool == "finance_totals"]
    assert spend.args == {"filters": {"cost_centre": "ENG-INF"}}
    (amp,) = [s for s in planner.plan("Spend in cost center G&A", people, 2026, IST).steps
              if s.tool == "finance_totals"]
    assert amp.args == {"filters": {"cost_centre": "G&A"}}


def test_select_discloses_what_it_cannot_show():
    facts = [(f"F{i}", type("F", (), {"display": f"fact {i}"})()) for i in range(pipeline.MAX_CLAIMS + 2)]
    chosen, omitted = pipeline._select(facts, [])
    assert len(chosen) == pipeline.MAX_CLAIMS and omitted == 2


@pytest.mark.db
def test_valid_time_and_recorded_time_reads_abstain_instead_of_erroring(app_conn, tenant, projected):  # noqa: F811
    for mode in ("VALID_AT", "RECORDED_BY"):
        a = _ask(app_conn, tenant, "What is the headcount?", mode=mode, as_of="2026-11-10")
        assert a["status"] == "abstained" and a["grade"]["value"] == "D"
        assert any("requires" in u for u in a["unknowns"])
