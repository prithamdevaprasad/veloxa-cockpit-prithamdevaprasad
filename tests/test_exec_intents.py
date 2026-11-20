"""CEO briefing intents: each broad question reaches the briefing that answers it (hosted audit Q16, Q24, Q29)."""

import pytest

from veloxa.ask.routes import EXEC_INTENTS


def intent(q: str) -> str | None:
    return next((name for name, rx in EXEC_INTENTS if rx.search(q)), None)


@pytest.mark.parametrize(("q", "want"), [
    ("Who are the most important people in the organisation?", "leaders"),
    ("Who is our leadership team?", "leaders"),
    ("Where do we have key-person dependencies?", "people"),
    ("What people-related risks should I know about?", "people"),
    ("Which dependencies need escalation?", "escalate"),
    ("What is blocking delivery?", "delivery"),
    ("What financial information is missing?", "fin_missing"),
    ("What is our financial position based on the available evidence?", "finance"),
    ("What commercial risks should I know about?", "finance"),
])
def test_briefing_intent(q, want):
    assert intent(q) == want
