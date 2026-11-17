"""Deterministic scenarios (Sprint 8): observed / derived / assumption / unknown, never a probability.
SYNTHETIC packs only (Orion: a dated GA in D1, postponed without a new date in D2)."""

from datetime import datetime

import pytest
from test_signals_db import IST, _basis, packs  # noqa: F401  (fixture re-export)

from veloxa.risk import scenarios
from veloxa.temporal import Mode

pytestmark = pytest.mark.db
BETWEEN = datetime.fromisoformat("2026-11-16T08:30:00+05:30")      # after D1's release, before D2's


def test_a_dated_commitment_can_be_shifted_with_its_calculation_shown(app_conn, packs):  # noqa: F811
    b = _basis(app_conn, packs, Mode.KNOWN_AT, BETWEEN)
    assert b.batch_seq == 1 and "Orion" in scenarios.commitments(app_conn, b, IST)
    s = scenarios.commitment_shift(app_conn, b, IST, "Orion", 30)
    assert s["derived"][0]["statement"] == "shifted date 2026-07-15"
    assert s["derived"][0]["calculation"] == "2026-06-15 + 30 day(s)" and s["derived"][0]["evidence"]
    assert any(o["stated_date"] == "2026-06-15" for o in s["observed"])
    assert s["assumptions"] == ["Orion moves by 30 day(s): a scenario input, not evidence"]
    assert s["probability"] is None and s["ranked"] is False
    assert any("required rate" in u for u in s["unknowns"])


def test_a_commitment_moved_without_a_new_date_has_nothing_to_shift(app_conn, packs):  # noqa: F811
    s = scenarios.commitment_shift(app_conn, _basis(app_conn, packs), IST, "Orion", 30)
    assert s["derived"] == []
    assert any("moved without a replacement date" in u for u in s["unknowns"])
    assert any(o["signal_type"] == "delivery_delay" for o in s["observed"])


def test_inputs_are_validated_and_unknown_objects_are_refused(app_conn, packs):  # noqa: F811
    b = _basis(app_conn, packs)
    for bad in (10_000, 1.5, True):
        with pytest.raises(scenarios.ScenarioError):
            scenarios.commitment_shift(app_conn, b, IST, "Orion", bad)
    with pytest.raises(scenarios.ScenarioError, match="no admitted evidence"):
        scenarios.commitment_shift(app_conn, b, IST, "Nonexistent", 5)


def test_the_scenario_api_fails_closed_and_validates(db, monkeypatch, tmp_path):
    from conftest import DEV_TOKEN, api_client, dev_fixture, ensure_configured_d1

    ensure_configured_d1(db, tmp_path)
    h = {"Authorization": f"Bearer {DEV_TOKEN}"}
    with api_client(db, monkeypatch) as anon:
        assert anon.get("/v1/scenarios/commitments").status_code == 401
    with api_client(db, monkeypatch, **dev_fixture()) as c:
        assert c.get("/v1/scenarios/commitments", headers=h).json()["commitments"] == []   # synthetic D1: none
        r = c.post("/v1/scenarios/commitment-shift", json={"business_object": "Nothing", "shift_days": 3}, headers=h)
        assert r.status_code == 422 and r.json()["error"] == "invalid_scenario"
        assert c.post("/v1/scenarios/commitment-shift", json={"business_object": "X", "shift_days": 9999},
                      headers=h).status_code == 422
        assert c.post("/v1/scenarios/commitment-shift", json={"business_object": "X", "shift_days": 3,
                                                              "probability": 0.9}, headers=h).status_code == 422
