"""Integration fixes: relevance stop-words, conflict grouping, unnamed commitments, scenario parsing, safe hiring
counts. No database."""

from veloxa import api_insights
from veloxa.ask import conflicts, pipeline
from veloxa.risk import graph, scenario_engine


def test_generic_verbs_and_question_filler_never_make_a_passage_relevant():
    for w in ("say", "says", "saying", "said", "records", "still", "unknown", "what", "about"):
        assert w in pipeline._GENERIC
    assert "commitment" not in pipeline._GENERIC and "unresolved" not in pipeline._GENERIC


def test_one_conflict_per_subject_lists_every_source_value_once():
    a = {"subject": "ARR", "claims": [{"record_id": "board:q2", "value": "5.8"}, {"record_id": "t1", "value": "42"}]}
    b = {"subject": "ARR", "claims": [{"record_id": "board:q3", "value": "6.1"}, {"record_id": "t1", "value": "42"}]}
    c = {"subject": "Customers", "claims": [{"record_id": "x", "value": "1"}, {"record_id": "y", "value": "2"}]}
    out = {g["subject"]: g for g in conflicts._grouped([a, b, c])}
    assert len(out) == 2 and [x["value"] for x in out["ARR"]["claims"]] == ["5.8", "42", "6.1"]


def test_the_detector_placeholder_is_never_a_commitment():
    assert "unresolved commitment" in graph.UNNAMED and "unresolved" in graph.UNNAMED


def test_free_text_scenarios_name_only_known_subjects_and_signed_shifts():
    names = ["Orbit Core 3.0", "Pulse 1.5", "Vega 3.0"]
    assert scenario_engine.parse("Move Orbit Core 3.0 by 3 weeks and Pulse 1.5 earlier by 5 days", names) == [
        {"subject": "Orbit Core 3.0", "shift_days": 21}, {"subject": "Pulse 1.5", "shift_days": -5}]
    assert scenario_engine.parse("What if Vega 3.0 slips a month?", names) == [{"subject": "Vega 3.0",
                                                                               "shift_days": 30}]
    assert scenario_engine.parse("Move Project Lynx by 2 weeks", names) == []          # never invented


def test_personal_sources_aggregate_only_by_non_identifying_columns():
    allow = api_insights.PERSONAL_COUNT_COLUMNS["hiring_tracker"]
    assert allow == {"Status", "Team"}
    assert not allow & {"Hiring Manager", "Notes", "In pipeline", "Req ID"}


def test_owner_reported_conversation_fixes():
    from veloxa import assistant, jira_chat
    from veloxa.agents import conversation
    from veloxa.ask import routes

    assert jira_chat._due('Create a Jira task in ENG for "X" due 30 November 2026') == "2026-11-30"
    assert jira_chat._due("create a task in ENG for X by 2026-12-01") == "2026-12-01"
    assert jira_chat._due("create a task in ENG for X") is None                       # never invented
    assert routes.ISSUE_KEY.findall("What is the status of ENG-12?") == ["ENG-12"]
    assert routes.COMMITMENTS.search("Which commitments are unresolved?")
    assert scenario_engine.parse("move atlas by 3 weeks", ["Atlas GA"]) == [{"subject": "Atlas GA", "shift_days": 21}]
    assert scenario_engine.parse("move atlas by 3 weeks", ["Atlas GA", "Atlas Mobile"]) == []   # ambiguous: no guess
    assert conversation.intent("What are the current issues in your area? Summarize the evidence.") == "updates"
    for yes in ("yes, create it", "Confirm", "go ahead", "yes please"):
        assert assistant.CONFIRM.match(yes)
    assert not assistant.CONFIRM.match("yes, but what is the ARR?")
