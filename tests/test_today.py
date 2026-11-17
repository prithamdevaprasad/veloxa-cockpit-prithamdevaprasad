"""Voice note T6a: what the cockpit shows when it opens, without a question. At most three evidence-backed items,
never ranked while D-15 is pending, never padded. SYNTHETIC bodies and data only."""

import pytest
from conftest import DEV_TOKEN, api_client, dev_fixture, ensure_configured_d1

from veloxa.risk.briefing import TODAY_LIMIT, today_section


def sig(subject, stype="delivery_delay", status="active", category="delay_recorded", flags=()):
    return {"subject": subject, "signal_type": stype, "status": status, "value": {"category": category},
            "flags": list(flags), "observed": [f"{subject} statement"], "missing": ["a new date is not stated"],
            "evidence_records": [{"record_id": f"synthetic:{subject}"}], "recorded_on": "2026-07-09",
            "event_date": None}


def body(signals, watchlist=()):
    by = {c: {"measurability": f"{c} note", "signals": []} for c in ("C1", "C2", "C3", "C4", "C5")}
    by["C1"]["signals"] = signals
    return {"watchlist": list(watchlist), "evidence_signals": {"by_criterion": by}}


def test_one_qualifying_item_is_shown_with_evidence_and_nothing_is_padded():
    t = today_section(body([sig("Orion"), sig("Orion", "commitment_status",
                                              category="delayed_new_date_not_stated")]))
    (item,) = t["items"]                                     # two signals, one business object
    assert item["business_object"] == "Orion" and item["evidence"] == ["synthetic:Orion"]
    assert "delayed_new_date_not_stated" in item["title"] and item["unknowns"] == ["a new date is not stated"]
    assert t["ranked"] is False and "nothing is added to fill the list" in t["explanation"]
    assert t["not_shown_because_unmeasurable"] == ["C2: C2 note", "C3: C3 note", "C4: C4 note", "C5: C5 note"]


def test_provisional_or_non_adverse_signals_never_become_worries():
    t = today_section(body([sig("Vega", status="provisional"), sig("Lyra", "commitment", category="dated")]))
    assert t["items"] == [] and "No item qualifies" in t["explanation"]


def test_more_than_three_candidates_are_never_cut_down_by_an_invented_order():
    t = today_section(body([sig(n) for n in ("A", "B", "C", "D")]))
    assert t["items"] == [] and t["candidate_count"] == 4 and t["candidates"] == ["A", "B", "C", "D"]
    assert "D-15" in t["explanation"]
    three = today_section(body([sig(n) for n in ("A", "B", "C")]))
    assert len(three["items"]) == TODAY_LIMIT == 3


def test_undominated_risks_qualify_and_dominated_ones_do_not():
    risk = {"risk_id": "R-1", "business_object": "reported figure", "description": "differs",
            "comparison": {"group": "not_dominated"}, "evidence": [{"record_id": "synthetic:r"}], "unknowns": [],
            "time_context": {}}
    t = today_section(body([], [risk, {**risk, "risk_id": "R-2", "comparison": {"group": "dominated"}}]))
    assert [i["source"] for i in t["items"]] == ["risk_watchlist"]


@pytest.mark.db
def test_the_today_endpoint_needs_no_question_fails_closed_and_never_ranks(db, monkeypatch, tmp_path):
    ensure_configured_d1(db, tmp_path)
    with api_client(db, monkeypatch) as anonymous:
        assert anonymous.get("/v1/today").status_code == 401  # fails closed without authentication
    with api_client(db, monkeypatch, **dev_fixture()) as client:
        r = client.get("/v1/today", headers={"Authorization": f"Bearer {DEV_TOKEN}"})
        assert r.status_code == 200
        j = r.json()
        assert j["top_three"] is None and j["ranking_mode"] == "unweighted_partial_order"
        assert len(j["today"]["items"]) <= 3 and j["today"]["ranked"] is False
        snap = client.get(j["full_briefing"], headers={"Authorization": f"Bearer {DEV_TOKEN}"}).json()
        assert snap["today"] == j["today"]                  # recorded in the immutable briefing snapshot
