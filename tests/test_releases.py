from datetime import datetime, timedelta

import pytest

from veloxa.releases import AdmissionRefused, check_admissible, current_time, effective_now, load_schedule


def test_schedule_has_four_ordered_packs_with_ist_release_times():
    s = load_schedule()
    assert [p.id for p in s.packs] == ["D1", "D2", "D3", "D4"]
    assert [p.batch_seq for p in s.packs] == [1, 2, 3, 4]
    assert all(p.release_at.utcoffset() == timedelta(hours=5, minutes=30) for p in s.packs)
    assert all(p.release_at.hour == 10 and p.release_at.minute == 0 for p in s.packs)


def test_d1_admissible_exactly_at_release_but_not_one_second_before():
    s = load_schedule()
    d1 = s.pack("D1")
    assert check_admissible(s, "D1", d1.release_at, set()).id == "D1"
    with pytest.raises(AdmissionRefused) as exc:
        check_admissible(s, "D1", d1.release_at - timedelta(seconds=1), set())
    assert exc.value.reason == "not_yet_released"


@pytest.mark.parametrize("pack_id", ["D2", "D3", "D4"])
def test_later_packs_refused_on_day_one(pack_id):
    s = load_schedule()
    day_one_evening = datetime.fromisoformat("2026-11-16T23:59:59+05:30")
    with pytest.raises(AdmissionRefused) as exc:
        check_admissible(s, pack_id, day_one_evening, {1})
    assert exc.value.reason == "not_yet_released"


def test_out_of_order_admission_refused():
    s = load_schedule()
    after_d2 = datetime.fromisoformat("2026-11-17T11:00:00+05:30")
    with pytest.raises(AdmissionRefused) as exc:
        check_admissible(s, "D2", after_d2, set())
    assert exc.value.reason == "out_of_order"


def test_unknown_pack_refused():
    s = load_schedule()
    with pytest.raises(AdmissionRefused) as exc:
        check_admissible(s, "D9", datetime.fromisoformat("2030-01-01T00:00:00+00:00"), set())
    assert exc.value.reason == "unknown_pack"


def test_demo_clock_can_move_back_but_never_forward():
    real = datetime.fromisoformat("2026-11-16T12:00:00+05:30")
    assert effective_now(real, "2026-11-16T09:00:00+05:30") == datetime.fromisoformat("2026-11-16T09:00:00+05:30")
    assert effective_now(real, "2026-11-19T12:00:00+05:30") == real  # clamped: D4 cannot be unlocked early
    with pytest.raises(ValueError):
        effective_now(real, "2026-11-16T09:00:00")  # no offset


def test_clock_override_is_refused_outside_test_mode():
    with pytest.raises(RuntimeError, match="test mode"):
        current_time({"VELOXA_CLOCK": "2026-11-16T09:00:00+05:30"})


def test_clock_override_is_honoured_in_test_mode_and_still_clamped():
    t = current_time({"VELOXA_CLOCK": "2026-11-16T09:00:00+05:30", "VELOXA_TEST_MODE": "1"})
    assert t == datetime.fromisoformat("2026-11-16T09:00:00+05:30")
    future = current_time({"VELOXA_CLOCK": "2999-01-01T00:00:00+00:00", "VELOXA_TEST_MODE": "1"})
    assert future < datetime.fromisoformat("2999-01-01T00:00:00+00:00")  # clamped to the real time


def test_no_override_means_the_real_clock():
    from datetime import UTC

    before = datetime.now(UTC)
    assert before <= current_time({}) <= datetime.now(UTC)
