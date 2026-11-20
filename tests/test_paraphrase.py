"""Paraphrase benchmark (Part 7.3), SYNTHETIC D1: each paraphrase must reach the same plan (tools) and the same
answer status as its base question. Pass rate is measured and must be at least 90%. The planner stays
deterministic: these are vocabulary and structure checks, never a model."""

import pytest
from conftest import D1_DAY

from veloxa import projector, tools
from veloxa.ask import pipeline
from veloxa.db import connect

pytestmark = pytest.mark.db
ALPHA = tools.Principal("person:T001", "executive", "alpha@example.test")

CASES = {
    "What is our headcount?": ["How many employees do we have?", "How big is the team?", "What is the staff count?"],
    "What is our headcount by department?": ["Headcount per department?", "How many people per department?",
                                             "Staff numbers by department"],
    "What is the ledger spend?": ["What are our expenses?", "How much have we spent?", "What are our outgoings?"],
    "What is the spend by vendor?": ["Spending per vendor?", "What are we paying vendors, by vendor?",
                                     "Expenditure by supplier"],
    "Who is Person Beta?": ["What is Person Beta's job title?", "Who's Person Beta?",
                            "What role does Person Beta work in?"],
    "Who does Person Beta report to?": ["Who is Person Beta's line manager?", "Who is the boss of Person Beta?",
                                        "Person Beta reports to whom?"],
    "What is the total payroll?": ["What is our wage bill?", "What are total salaries?", "What is our people cost?"],
    "Show me the timeline of events": ["What happened?", "What are the key dates?", "Give me the sequence of events"],
}


@pytest.fixture
def projected(db, ingest_conn, tenant, schedule, synthetic_root):
    from veloxa.ingest.pipeline import admit_pack

    admit_pack(ingest_conn, tenant, schedule, "D1", synthetic_root, D1_DAY)
    with connect(db.dsn("projection")) as conn:
        projector.build(conn, tenant)


def _shape(a):
    return a["status"], tuple(sorted({s["tool"] for s in (a.get("diagnostics") or {}).get("plan", [])}))


def test_paraphrases_reach_the_same_plan_and_status(app_conn, tenant, projected):
    from veloxa.releases import current_time

    ask = lambda q: pipeline.answer(app_conn, tenant, "Asia/Kolkata", ALPHA, q, current_time())  # noqa: E731
    total, passed, misses = 0, 0, []
    for base, paras in CASES.items():
        want = _shape(ask(base))
        for q in paras:
            total += 1
            got = _shape(ask(q))
            if got == want:
                passed += 1
            else:
                misses.append((q, got, want))
    rate = passed / total
    print(f"paraphrase pass rate: {passed}/{total} = {rate:.0%}; misses: {misses}")
    assert rate >= 0.9, misses
