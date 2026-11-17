"""C19 disclosure auditor: the exact decision core (ARCHITECTURE.md §15.2-§15.6). SYNTHETIC variables only:
there are no pay values anywhere in these tests, because the auditor never takes values as input."""

import inspect
from fractions import Fraction as F
from pathlib import Path

import pytest

from veloxa.disclosure import core
from veloxa.disclosure.core import AuditorError, Query, decide, row, segment_sum_row

PEOPLE = {"A", "B", "C", "D", "E", "F", "G"}


def q(*rows, sizes=None, stat="SUM"):
    return Query(stat, tuple(rows), tuple(sizes or [len(r) for r in rows]))


def test_a_classic_differencing_attack_is_denied():
    total = row({p: 1 for p in PEOPLE})
    first = decide([], q(total), PEOPLE, set())
    assert first.allowed
    minus_a = row({p: 1 for p in PEOPLE - {"A"}})
    second = decide(list(first.append), q(minus_a), PEOPLE, set())
    assert (second.allowed, second.reason) == (False, "would_disclose")


def test_m2_stable_pay_detects_a_joiner_that_a_per_period_model_misses():
    jan = segment_sum_row({p: 1 for p in ("A", "B", "C", "D", "E")})
    feb = segment_sum_row({p: 1 for p in ("A", "B", "C", "D", "E", "F")})     # F joined in February
    assert decide([jan], q(feb), PEOPLE, set()).reason == "would_disclose"   # stable pay: Feb - Jan = F
    per_period = row({f"{p}@feb": 1 for p in ("A", "B", "C", "D", "E", "F")})  # one variable per person-month
    jan_pp = row({f"{p}@jan": 1 for p in ("A", "B", "C", "D", "E")})
    months = {f"{p}@{m}" for p in PEOPLE for m in ("jan", "feb")}
    assert decide([jan_pp], q(per_period), months, set()).allowed             # why M2 is required (§15.2)


def test_pro_rata_coefficients_are_exact():
    half = segment_sum_row({"A": 1, "B": 1, "C": 1, "D": 1, "E": F(1, 3)})    # E present for a third of the period
    full = segment_sum_row({"A": 1, "B": 1, "C": 1, "D": 1, "E": 1})
    assert decide([half], q(full), PEOPLE, set()).reason == "would_disclose"  # difference = 2/3 E
    with pytest.raises(AuditorError, match="exact arithmetic"):
        row({"A": 0.5})                                                       # floats are refused


def test_small_group_totals_are_denied_even_without_an_individual_disclosure():
    five = row({p: 1 for p in ("A", "B", "C", "D", "E")})
    three = row({p: 1 for p in ("C", "D", "E")})
    d = decide([five], q(three, sizes=[3], stat="SUM"), PEOPLE, set(), k=3)
    assert (d.allowed, d.reason) == (False, "small_group")    # A + B (2 people < k = 3) would be determined


def test_policy_gates_come_first():
    total = row({p: 1 for p in PEOPLE})
    for stat in ("MAX", "MIN", "MEDIAN", "PERCENTILE", "VARIANCE", "MODE"):
        assert decide([], q(total, stat=stat), PEOPLE, set()).reason == "statistic_not_released"
    assert decide([], q(row({"A": 1, "B": 1}), sizes=[2]), PEOPLE, set()).reason == "cohort_below_k"
    hidden = Query("SUM", (total,), (7,), cohort_membership_known=False)
    assert decide([], hidden, PEOPLE, set()).reason == "cohort_membership_not_known"


def test_background_rows_enable_inference_and_are_respected():
    own = row({"A": 1})                                       # the principal's own value (a background unit row)
    five = row({p: 1 for p in ("A", "B", "C", "D", "E")})
    d = decide([own], q(five), PEOPLE, {"A"})
    assert (d.allowed, d.reason) == (False, "small_group")    # knowing A, the sum reveals B+C+D+E: 4 people < k
    everyone = row({p: 1 for p in PEOPLE})
    assert decide([own], q(everyone), PEOPLE, {"A"}).allowed  # the other six stay a group of at least k
    assert decide([], q(five), PEOPLE, set()).allowed         # without the background row the same sum is safe


def test_pre_existing_exposure_is_reported_not_blamed_on_the_new_query():
    doc = row({"B": 1})                                       # e.g. a document the principal can already read
    d = decide([doc], q(row({p: 1 for p in ("C", "D", "E", "F", "G")})), PEOPLE, set())
    assert d.allowed and d.pre_existing_exposure == frozenset({"B"})


def test_decisions_take_structure_only_never_values():
    params = inspect.signature(decide).parameters
    assert set(params) == {"ledger", "query", "protected", "allowed_vars", "k"}
    assert core.DEFAULT_K == 5                                # D-14a default, unchanged


def test_the_core_is_reached_only_through_the_disclosure_gate():
    src = Path(core.__file__).resolve().parents[1]
    users = sorted(p.name for p in src.rglob("*.py") if "disclosure" not in p.parts
                   and ("disclosure.gate" in p.read_text(encoding="utf-8")
                        or "disclosure import core" in p.read_text(encoding="utf-8")))
    assert users == ["tools.py"]                              # Ask's payroll tool, via the gate only
