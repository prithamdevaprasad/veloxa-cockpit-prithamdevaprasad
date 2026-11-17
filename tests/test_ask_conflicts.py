"""H-11 figure disagreements (src/veloxa/ask/conflicts.py), pure. SYNTHETIC figures only."""

from decimal import Decimal

from veloxa.ask import conflicts
from veloxa.signals.c4_integrity import Parsed, Reported


def formal(label="Revenue (recurring)", text="Rs. 3.7 Cr", period="Jan to Mar 2099"):
    return Reported("synthetic:board", label, text, period, None)


def spoken(label="Revenue", low=Decimal("20e7"), high=Decimal("21e7"), unit="INR", period=None, hedged=True):
    return Reported("synthetic:call", label, "twenty crore", period, None, None, Parsed(low, high, unit), hedged)


Q = "What is the revenue?"


def test_a_leading_token_match_with_disjoint_values_is_a_disagreement_citing_both():
    (d,) = conflicts.disagreements([formal()], [spoken()], [], Q)
    assert d["metric_match"] == "possibly_same_metric" and d["definition"] == "unconfirmed"
    assert d["period"] == "period_unconfirmed"
    assert [c["record_id"] for c in d["claims"]] == ["synthetic:board", "synthetic:call"]


def test_values_within_rounding_are_not_a_disagreement():
    assert conflicts.disagreements([formal()], [spoken(low=Decimal("3.65e7"), high=Decimal("3.75e7"))], [], Q) == []


def test_different_units_or_stated_periods_are_never_compared():
    assert conflicts.disagreements([formal()], [spoken(unit="percent")], [], Q) == []
    assert conflicts.disagreements([formal()], [spoken(period="Apr to Jun 2099")], [], Q) == []


def test_similar_labels_are_not_merged_and_only_asked_metrics_are_checked():
    assert conflicts.match("Rev", "Revenue (recurring)") is None
    assert conflicts.match("Revenue growth", "Revenue (recurring)") is None
    assert conflicts.match("revenue (recurring)", "Revenue (recurring)") == "same_label"
    assert conflicts.disagreements([formal()], [spoken()], [], "What is the burn?") == []


def test_values_without_a_unit_are_listed_as_not_comparable():
    unparsed = [{"record_id": "synthetic:cfo", "label": "Revenue", "value": "forty", "reason": "no unit stated"}]
    (d,) = conflicts.disagreements([formal()], [spoken()], unparsed, Q)
    assert d["not_comparable"] == unparsed
