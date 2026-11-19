"""Conversational agents: greetings and help need no retrieval; updates and blockers retrieve; follow-ups use the
session; hedges are stated once and never claim the agent is the person. No database."""

import pytest

from veloxa.agents import conversation as c
from veloxa.agents import tools as agent_tools


@pytest.mark.parametrize("msg,kind", [
    ("Hi", "greeting"), ("hello there!", "greeting"), ("Good morning", "greeting"), ("Thanks!", "thanks"),
    ("What can you do?", "help"), ("Any updates?", "updates"), ("How is the project going?", "updates"),
    ("What's blocking the work?", "blockers"), ("Tell me more", "more"), ("Show evidence", "evidence"),
    ("What is your role?", "role"), ("Who do you report to?", "manager"), ("What did I ask earlier?", "memory"),
])
def test_intents(msg, kind):
    assert c.intent(msg) == kind


def test_a_greeting_with_a_question_is_not_small_talk():
    assert c.intent("Hi, any updates on Atlas?") == "updates"


def test_small_talk_says_it_is_an_ai_representation_and_states_no_facts():
    who = {"display_name": "Test Person", "title": "Engineer", "department": "Engineering"}
    hello = c.small_talk("greeting", who)
    assert "Test Person's AI assistant" in hello and "not the person" in hello
    assert "Any updates?" in c.small_talk("help", who)
    assert c.small_talk("greeting", {}).startswith("Hello. I'm an AI assistant")


def test_tell_me_more_continues_the_last_answer_and_then_says_it_is_complete():
    claims = [{"text": f"Fact {i}.", "evidence": [f"r{i}"]} for i in range(6)]
    hist = [{"message": "Any updates?", "claims": claims, "evidence": [{"record_id": f"r{i}"} for i in range(6)]}]
    text, ids = c.history_reply("more", hist)
    assert text == "Fact 4. Fact 5." and ids == ["r4", "r5"]
    text, ids = c.history_reply("more", [{"message": "x", "claims": claims[:2], "evidence": [{"record_id": "r0"}]}])
    assert text.startswith("That's everything my records hold") and ids == ["r0"]
    assert c.history_reply("more", [])[0].startswith("There's nothing to expand on yet")


def test_a_shared_hedge_is_said_once():
    lead = "From my department's records (not something I can confirm I saw): "
    out = c.reply("updates", [{"text": lead + "a is due", "evidence": ["x"]},
                              {"text": lead + "b is open", "evidence": ["y"]}], "qualified")
    assert out.count("department's records") == 1 and out.endswith("A is due. B is open.")


def test_blockers_without_records_are_not_guessed():
    assert "nothing is marked as a blocker" in c.reply("blockers", [], "abstained")


def test_work_updates_is_a_read_only_briefing_tool_with_dates_kept_apart():
    spec = agent_tools.REGISTRY["work.updates"]
    assert spec.read_only and spec.capability == "briefing" and spec.audited
    from veloxa.agents import updates
    assert {"jira", "git"} <= updates.DEPT_SYSTEMS["engineering"]
    assert "payroll_register" not in set().union(*updates.DEPT_SYSTEMS.values())
    assert "leave_register" not in set().union(*updates.DEPT_SYSTEMS.values())
