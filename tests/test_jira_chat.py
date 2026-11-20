"""Jira actions from Ask, against a mocked Jira (no network, no real site, invented values only). Every write is a
preview until confirmed; projects, types and assignees come only from Jira; ambiguity becomes a question."""

from test_jira_oauth import SITE, R, connect, make

from veloxa import jira_chat


def _projects(http, *projects):
    http.on("GET", "/rest/api/3/project/search", R(200, {"values": [
        {"key": k, "name": n, "id": str(i)} for i, (k, n) in enumerate(projects)]}))


def _types(http, project, *types):
    http.on("GET", f"/issue/createmeta/{project}/issuetypes", R(200, {"issueTypes": [
        {"id": str(10000 + i), "name": t} for i, t in enumerate(types)]}))
    http.on("GET", f"/issue/createmeta/{project}/issuetypes/10000", R(200, {"fields": [
        {"fieldId": "summary", "name": "Summary", "required": True},
        {"fieldId": "description", "name": "Description", "required": False}]}))


def wrote(http):
    return [(m, u) for m, u, _ in http.calls if m in ("PUT", "DELETE")
            or (m == "POST" and (u.endswith("/rest/api/3/issue") or u.endswith("/comment")))]


def test_not_connected_or_not_configured_is_said_plainly():
    assert "isn't configured" in jira_chat.handle(None, "t", "p", "Create a Jira task for X")["answer"]
    j, _, _ = make()
    assert "isn't connected" in jira_chat.handle(j, "tenant-a", "p", "Create a Jira task for X")["answer"]


def test_create_resolves_the_project_and_type_from_jira_and_only_previews():
    j, http, _ = make()
    connect(j)
    _projects(http, ("ENG", "Engineering"), ("MKT", "Marketing"))
    _types(http, "ENG", "Task", "Bug")
    http.on("POST", "/rest/api/3/search/jql", R(200, {"issues": []}))
    out = jira_chat.handle(j, "tenant-a", "admin-1", 'Create a Jira task in Engineering for "Approve backend hire"')
    assert out["status"] == "action_preview" and out["action"]["project"] == "ENG"
    assert out["action"]["issue_type"] == "Task" and out["action"]["fields"]["Summary"] == "Approve backend hire"
    assert "Nothing has been created yet" in out["answer"] and wrote(http) == []


def test_an_unclear_project_or_type_becomes_a_question_with_the_real_options():
    j, http, _ = make()
    connect(j)
    _projects(http, ("ENG", "Engineering"), ("MKT", "Marketing"))
    out = jira_chat.handle(j, "tenant-a", "admin-1", "Create a Jira task for the launch checklist")
    assert out["status"] == "clarify" and "ENG (Engineering)" in out["answer"] and "MKT (Marketing)" in out["answer"]
    _types(http, "ENG", "Story")
    out = jira_chat.handle(j, "tenant-a", "admin-1", "Create a Jira bug in ENG for a crash")
    assert out["status"] == "clarify" and "Story" in out["answer"] and wrote(http) == []


def test_assign_only_to_someone_jira_lists_and_ask_when_ambiguous():
    j, http, _ = make()
    connect(j)
    http.on("GET", "/rest/api/3/user/assignable/search", R(200, [
        {"accountId": "a1", "displayName": "Dev One"}, {"accountId": "a2", "displayName": "Dev Onely"}]))
    out = jira_chat.handle(j, "tenant-a", "admin-1", "Assign ENG-7 to Dev")
    assert out["status"] == "clarify" and "Dev One" in out["answer"] and "Dev Onely" in out["answer"]
    http.routes[("GET", "/rest/api/3/user/assignable/search")] = [R(200, [])]
    out = jira_chat.handle(j, "tenant-a", "admin-1", "Assign ENG-7 to Nobody Known")
    assert out["status"] == "abstained" and "won't assign it to anyone else" in out["answer"]
    assert wrote(http) == []


def test_update_without_a_stated_change_asks_and_with_a_comment_previews():
    j, http, _ = make()
    connect(j)
    out = jira_chat.handle(j, "tenant-a", "admin-1", "Update ENG-7")
    assert out["status"] == "clarify"
    http.on("GET", "/rest/api/3/issue/ENG-7/editmeta", R(200, {"fields": {"summary": {"name": "Summary"}}}))
    http.on("GET", "/rest/api/3/issue/ENG-7", R(200, {"key": "ENG-7", "fields": {"summary": "Old"}}))
    out = jira_chat.handle(j, "tenant-a", "admin-1", "Update ENG-7 and comment: please re-estimate")
    assert out["status"] == "action_preview" and out["action"]["comment"] == "please re-estimate"
    assert out["action"]["url"] == f"{SITE['url']}/browse/ENG-7" and wrote(http) == []


def test_the_overdue_report_is_a_read_with_links():
    j, http, _ = make()
    connect(j)
    _projects(http, ("ENG", "Engineering"))
    http.on("POST", "/rest/api/3/search/jql", R(200, {"issues": [{"key": "ENG-2", "fields": {
        "summary": "Late item", "duedate": "2099-01-01", "status": {"name": "To Do"}, "assignee": None}}]}))
    out = jira_chat.handle(j, "tenant-a", "admin-1", "Prepare an overdue-issues report")
    assert out["status"] == "answered" and "ENG-2: Late item" in out["answer"] and "/browse/ENG-2" in out["answer"]
    assert wrote(http) == []


def test_jira_requests_are_recognised_and_business_questions_are_not():
    assert jira_chat.asks("Create a Jira task for an Engineering blocker")
    assert jira_chat.asks("Assign ENG-12 to Dev One") and jira_chat.asks("Prepare an overdue-issues report")
    assert not jira_chat.asks("What is the status of Atlas GA?")
    assert not jira_chat.asks("Which Jira tasks does agent-eng-02 own?")
