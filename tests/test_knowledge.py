"""Claim extraction and the Conflict Registry rules (veloxa.knowledge). Pure functions: no database."""

from veloxa import knowledge as k


def rec(system, payload, cls="internal", rid=None, sheet="S", row=5):
    return {"record_id": rid or f"{system}:x#{row}", "version": 1, "system": system, "data_class": cls,
            "locator": {"sheet": sheet, "row": row}, "payload": payload}


def claim(subject, pred, obj, rid, valid=None, stype="team"):
    return {"claim_id": k.Claim(stype, subject, pred, obj, valid, "unstated", rid, 1, {}, "m", "internal").claim_id,
            "subject_type": stype, "subject": subject, "predicate": pred, "object": obj, "valid_at": valid,
            "record_id": rid}


def test_precision_is_kept_as_written():
    assert k.precision("2026-11-17") == "day"
    assert k.precision("2026-11-17T09:00:00") == "instant"
    assert k.precision("Jul-Sep 2026") == "quarter"
    assert k.precision("Oct-2026") == "month"
    assert k.precision(None) == "unstated"


def test_protected_classes_and_sources_never_become_claims():
    assert k.row_claims("zoho_books", rec("zoho_books", {"Voucher": "V1", "Amount (Rs.)": 5},
                                          cls="payroll_aggregate")) == []
    assert k.row_claims("payroll_register", rec("payroll_register", {"Emp ID": "E1"})) == []
    assert {"payroll_register", "leave_register", "git", "email"} <= k.NEVER_CLAIMED


def test_row_claims_keep_locator_valid_time_and_skip_empty_values():
    cs = k.row_claims("hiring_tracker", rec("hiring_tracker", {
        "Req ID": "R-1", "Role": "Engineer", "Team": "Eng", "Hiring Manager": "M", "Opened": "2026-08-01",
        "Status": "Open", "Status Date": "2026-10-01", "In pipeline": 3, "Notes": None}, cls="personal"))
    by = {c.predicate: c for c in cs}
    assert by["status"].valid_at == "2026-10-01" and by["status"].valid_precision == "day"
    assert by["opened"].valid_at == "2026-08-01"
    assert by["status"].locator == {"sheet": "S", "row": 5} and by["status"].data_class == "personal"
    assert "notes" not in by                                  # recruiter notes are never a claim
    assert all(c.subject == "requisition:R-1" for c in cs)


def test_values_for_different_quarters_are_a_series_not_a_conflict():
    cs = [claim("team:Eng", "engagement_score", 3.9, "pulse:a#4", "Apr-Jun 2026"),
          claim("team:Eng", "engagement_score", 3.4, "pulse:a#9", "Jul-Sep 2026")]
    assert k.detect_conflicts(cs) == []


def test_same_fact_same_time_different_values_needs_review():
    cs = [claim("requisition:R-1", "status", "Open", "hiring:a#4", "2026-10-01", "requisition"),
          claim("requisition:R-1", "status", "Filled", "other:b#2", "2026-10-01", "requisition")]
    [c] = k.detect_conflicts(cs)
    assert c["resolution"] == "needs_review" and c["preferred_claim_id"] is None
    assert "No approved precedence rule" in c["rationale"]


def test_employment_fact_uses_the_hr_system_of_record_rule():
    cs = [claim("person:E1", "title", "Lead", "keka_hrms:key:Emp ID=E1", None, "person"),
          claim("person:E1", "title", "Manager", "documents:x#doc", None, "person")]
    [c] = k.detect_conflicts(cs)
    assert c["resolution"] == "rule" and c["rule"] == "R-HR-SOR-01"
    assert c["preferred_claim_id"] == cs[0]["claim_id"]
    assert set(c["claim_ids"]) == {cs[0]["claim_id"], cs[1]["claim_id"]}   # both kept and cited


def _board(rid, value, period):
    return claim("metric:arr (recurring)", "reported_value", {"value_text": value, "period": period}, rid, period,
                 "metric")


def _spoken(rid, low, high, unit="percent", period=None):
    return claim("metric:arr", "stated_value", {"low": str(low), "high": str(high), "unit": unit, "period": period},
                 rid, period, "metric")


def test_board_figures_for_different_periods_do_not_conflict():
    assert k.detect_conflicts([_board("doc:q2", "41%", "Apr-Jun 2026"),
                               _board("doc:q3", "46%", "Jul-Sep 2026")]) == []


def test_spoken_figure_matching_one_reported_period_is_not_a_conflict():
    cs = [_board("doc:q2", "41%", "Apr-Jun 2026"), _board("doc:q3", "46%", "Jul-Sep 2026"),
          _spoken("t1:doc", 45.5, 46.5)]
    assert k.detect_conflicts(cs) == []


def test_spoken_figure_matching_no_reported_period_is_a_conflict_needing_review():
    cs = [_board("doc:q2", "41%", "Apr-Jun 2026"), _board("doc:q3", "46%", "Jul-Sep 2026"),
          _spoken("t1:doc", 70, 72)]
    [c] = k.detect_conflicts(cs)
    assert c["resolution"] == "needs_review" and c["subject"] == "metric:arr"
    assert "label only" in c["rationale"]


def test_a_unitless_spoken_figure_is_not_compared():
    cs = [_board("doc:q3", "46%", "Jul-Sep 2026"), _spoken("t1:doc", 40, 42, unit=None)]
    assert k.detect_conflicts(cs) == []


def test_board_numbers_parse_currency_scale_percent_and_months():
    assert k._board_number("Rs. 4 Cr") == ("INR", 40000000)
    assert k._board_number("Rs. 2.5 L")[0] == "INR"
    assert k._board_number("12%") == ("percent", 12)
    assert k._board_number("13.5 months")[0] == "months"
    assert k._board_number("not a number") == (None, None)
