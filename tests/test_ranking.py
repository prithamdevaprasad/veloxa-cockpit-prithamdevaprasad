"""D-15 version 1 weighted severity ranking (veloxa.risk.ranking). Pure functions: no database."""

import copy
from pathlib import Path

import pytest

from veloxa.risk import ranking as r

FIXTURE = Path(__file__).parent / "fixtures" / "d15_test_policy.toml"
POLICY = r.load_policy(FIXTURE)
NA = r.Measure("not_applicable")
MISSING = r.Measure("missing", reason="no record")


def item(i, c1=NA, c2=NA, c3=NA, c4=NA, **kw):
    kw.setdefault("evidence", [f"rec:{i}"])
    return r.Item(i, f"Finding {i}", {"C1": c1, "C2": c2, "C3": c3, "C4": c4}, **kw)


def m(lo, hi=None, measure=None):
    return r.Measure("measured", lo, lo if hi is None else hi, measure)


def test_weights_sum_to_one_and_probabilities_are_disabled():
    assert sum(POLICY["weights"].values()) == pytest.approx(1.0)
    assert POLICY["probabilities"] == "disabled"
    bad = copy.deepcopy(POLICY)
    bad["weights"]["C1"] = 0.5
    with pytest.raises(ValueError):
        r.rank([item("a")], bad)


def test_anchors_are_linear_and_clamped():
    assert r.value(1.0, 1.0, 2.0) == 0 and r.value(2.0, 1.0, 2.0) == 1 and r.value(1.5, 1.0, 2.0) == 0.5
    assert r.value(0.5, 1.0, 2.0) == 0 and r.value(9, 1.0, 2.0) == 1


def test_missing_is_never_zero_it_widens_the_interval():
    known = r.score(POLICY, item("a", c1=m(2.0)))
    unknown = r.score(POLICY, item("b", c1=MISSING))
    assert known["low"] == known["high"] == pytest.approx(0.30)
    assert unknown["low"] == 0 and unknown["high"] == pytest.approx(0.30)        # [0, w1], not 0
    assert unknown["measured_weight"] == pytest.approx(0.70)


def test_unmapped_and_unquantified_are_treated_as_missing():
    for status in ("unmapped", "unquantified"):
        s = r.score(POLICY, item("a", c4=r.Measure(status)))
        assert (s["low"], s["high"]) == (0, pytest.approx(0.15))


def test_not_applicable_is_the_best_anchor():
    assert r.score(POLICY, item("a"))["high"] == 0


def test_fallback_c1_measure_uses_its_own_anchors():
    s = r.score(POLICY, item("a", c1=m(45, measure="C1.documented_slip_days@1")))
    assert s["criteria"]["C1"]["v_low"] == pytest.approx(0.5)


def test_eligibility_gate_keeps_weakly_measured_items_visible_but_unranked():
    out = r.rank([item("strong", c1=m(2.0)), item("weak", c1=MISSING, c2=MISSING, c3=MISSING)], POLICY)
    assert [x["item_id"] for x in out["ranked"]] == ["strong"]
    [u] = out["unrankable"]
    assert u["item_id"] == "weak" and "weight is measured" in u["reasons"][0]


def test_unsupported_or_unverified_findings_are_never_ranked():
    out = r.rank([item("ok", c1=m(2.0)), item("unverified", c1=m(2.0), verified=False),
                  item("no-evidence", c1=m(2.0), evidence=[])], POLICY)
    assert [x["item_id"] for x in out["ranked"]] == ["ok"]
    assert {u["item_id"] for u in out["unrankable"]} == {"unverified", "no-evidence"}


def test_equal_severity_items_share_a_rank_and_are_marked_tied():
    out = r.rank([item("a", c3=m(1.0)), item("b", c3=m(1.0)), item("c", c3=m(0.5))], POLICY)
    ranks = {x["item_id"]: (x["rank"], x["tied"]) for x in out["ranked"]}
    assert ranks["a"] == ranks["b"] == (1, True)
    assert ranks["c"] == (2, False)


def test_overlapping_intervals_share_a_band_no_arbitrary_tie_break():
    out = r.rank([item("a", c1=m(1.8), c4=MISSING), item("b", c1=m(1.9))], POLICY)      # [0.24, 0.39] vs 0.27
    assert {x["rank"] for x in out["ranked"]} == {1}
    assert all("overlap" in x["why"] for x in out["ranked"])


def test_confidence_never_changes_severity_or_order():
    hi = r.rank([item("a", c1=m(1.5), confidence="high"), item("b", c3=m(0.6), confidence="low")], POLICY)
    lo = r.rank([item("a", c1=m(1.5), confidence="low"), item("b", c3=m(0.6), confidence="high")], POLICY)
    assert [(x["item_id"], x["rank"], x["severity"]) for x in hi["ranked"]] == \
           [(x["item_id"], x["rank"], x["severity"]) for x in lo["ranked"]]


def test_each_criterion_counts_once_no_double_counting():
    s = r.score(POLICY, item("a", c1=m(2.0), c2=m(1000), c3=m(1.0), c4=m(5.0)))
    assert s["high"] == pytest.approx(1.0)                                      # max is exactly 1
    assert set(s["criteria"]) == {"C1", "C2", "C3", "C4"}


def test_different_snapshots_rank_on_their_own_evidence():
    early = r.rank([item("x", c1=m(1.2)), item("y", c3=m(1.0))], POLICY)
    later = r.rank([item("x", c1=m(2.0)), item("y", c3=m(0.6))], POLICY)
    assert early["ranked"][0]["item_id"] == "y" and later["ranked"][0]["item_id"] == "x"


def test_sensitivity_flags_a_close_call_and_not_a_clear_one():
    close = r.rank([item("a", c1=m(1.67)), item("b", c3=m(1.0))], POLICY)              # 0.201 vs 0.200
    assert all(x["sensitive"] for x in close["ranked"])
    clear = r.rank([item("a", c1=m(2.0), c2=m(1000)), item("b", c3=m(0.6))], POLICY)
    assert not any(x["sensitive"] for x in clear["ranked"])


def test_every_ranked_item_explains_its_position():
    out = r.rank([item("a", c1=m(2.0), c2=MISSING)], POLICY)
    x = out["ranked"][0]
    assert x["severity"]["criteria"]["C2"]["status"] == "missing"
    assert "not a probability" in x["why"] and "C1" in x["why"]


def test_without_a_configured_decision_file_ranking_is_unavailable(monkeypatch):
    monkeypatch.delenv(r.POLICY_ENV, raising=False)
    with pytest.raises(r.PolicyMissing):
        r.load_policy()
