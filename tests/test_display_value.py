"""Structured claim values are always rendered as readable text (never a dict repr or [object Object])."""

from veloxa.knowledge import display_value


def test_reported_figure_spoken_figure_and_partial_values():
    assert display_value({"period": "Jan to Mar 2099", "issued_on": "2099-04-06", "value_text": "123 widgets"}) == (
        "123 widgets (Jan to Mar 2099; issued 6 April 2099), as reported")
    spoken = display_value({"low": "1.0", "high": "2.0", "span": "forty-two crore", "hedged": True, "period": None})
    assert spoken.startswith("“forty-two crore”, as spoken (approximate)") and "None" not in spoken
    assert display_value({"value_text": "61", "issued_on": "not a date"}) == "61, as reported"
    assert display_value(None) == "" and display_value({"x": None}) == "no value recorded"
    assert display_value({"customers": 61, "unit": "count"}) == "customers: 61"
    for v in (None, {}, {"a": {"b": 1}}, "plain", 3):
        assert "object Object" not in display_value(v)
