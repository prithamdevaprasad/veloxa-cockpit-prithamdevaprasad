"""Agent interview replies (veloxa.agents.conversation): only verified claims, the session's own history and policy."""

import pytest

from veloxa.agents import conversation as cv

CLAIMS = [
    {
        "fact_id": "self.identity",
        "text": "I'm the AI representation of Person A, built from their HR record",
        "evidence": ["hr#1"],
    },
    {"fact_id": "self.title", "text": "My job title is Staff Engineer", "evidence": ["hr#1"]},
    {"fact_id": "self.department", "text": "I'm part of the Engineering department", "evidence": ["hr#1"]},
    {"fact_id": "self.manager", "text": "I report to Person B, the VP Engineering", "evidence": ["hr#1", "hr#2"]},
    {"fact_id": "self.join_date", "text": "I joined on 14 March 2022", "evidence": ["hr#1"]},
]


@pytest.mark.parametrize(
    "q, kind",
    [
        ("Tell me a little about yourself.", "about"),
        ("What's your role in the company?", "role"),
        ("Who do you report to?", "manager"),
        ("When did you join?", "joined"),
        ("Can you explain that in simpler terms?", "simpler"),
        ("What evidence supports what you just told me?", "evidence"),
        ("What did I ask you earlier?", "memory"),
        ("What do you not know?", "unknowns"),
        ("Do you know what your workload is?", "workload"),
        ("What is your biggest challenge right now?", "challenge"),
        ("What are your hobbies?", "personal"),
    ],
)
def test_intents(q, kind):
    assert cv.intent(q) == kind


def test_a_role_question_gets_only_title_and_department_in_plain_sentences():
    chosen = cv.select("role", CLAIMS)
    assert [c["fact_id"] for c in chosen] == ["self.title", "self.department"]
    assert (
        cv.reply("role", chosen, "answered")
        == "My job title is Staff Engineer. I'm part of the Engineering department."
    )


def test_the_hr_record_never_answers_workload_or_challenges():
    assert cv.select("workload", CLAIMS) == [] and cv.select("challenge", CLAIMS) == []
    text = cv.reply("workload", [], "abstained")
    assert "don't have a record" in text and "workload" in text and "Staff Engineer" not in text


def test_memory_evidence_and_simplify_use_only_the_real_session():
    assert cv.history_reply("memory", [])[0].startswith("This is the start of our conversation")
    hist = [
        {
            "message": "Who do you report to?",
            "reply": "I report to Person B, the VP Engineering.",
            "evidence": [{"record_id": "hr#1", "system": "keka_hrms"}, {"record_id": "hr#2", "system": "keka_hrms"}],
        },
        {"message": "What do you not know?", "reply": "My records cover ...", "evidence": []},
    ]
    text, ids = cv.history_reply("memory", hist)
    assert "2 questions" in text and "Who do you report to?" in text and ids == []
    text, ids = cv.history_reply("evidence", hist)
    assert "2 records in the HR export (Keka)" in text and ids == ["hr#1", "hr#2"]
    text, _ = cv.history_reply("simpler", hist[:1])
    assert text == "In short: I report to Person B, the VP Engineering."


def test_persona_details_are_always_labelled_and_absent_persona_is_not_invented():
    text, ids = cv.history_reply("personal", [], [{"category": "hobbies", "value": "weekend treks"}])
    assert "weekend treks" in text and "fictional assumption, not a fact" in text and ids == []
    assert "won't make any up" in cv.history_reply("personal", [], [])[0]


def test_refusals_are_plain_and_offer_the_policy_alternative():
    text = cv.refusal_reply(
        {
            "policy": "POL-PAY-01",
            "reason": "Individual pay is protected. Pay values ...",
            "alternative": "Ask about ledger spend excluding payroll lines.",
        }
    )
    assert text == (
        "I can't help with that. Individual pay is confidential, so I can't share anyone's salary or pay "
        "details. Instead, you could ask about ledger spend excluding payroll lines."
    )


def test_a_combined_question_gets_every_fact_it_asks_for_once():
    chosen = cv.select("manager", CLAIMS, "What is your role and who do you report to?")
    assert [c["fact_id"] for c in chosen] == ["self.title", "self.department", "self.manager"]
