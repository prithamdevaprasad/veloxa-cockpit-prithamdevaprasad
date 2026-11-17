"""D2 people contracts (hiring, leave, pulse). SYNTHETIC rows only; no source values."""

from datetime import date

from veloxa.ingest.people_contracts import (
    HIRING_COLUMNS,
    LEAVE_COLUMNS,
    PULSE_COLUMNS,
    PulseRow,
    hiring,
    leave,
    pulse,
)

TITLE = ("Synthetic export",)


def sheet(columns, *rows):
    return [TITLE, (None,), columns, *rows]


def req(rid="SYN-1", opened="2099-01-02", status="Open", status_date="2099-01-10", pipe=3, notes="syn note"):
    return (rid, "Synthetic role", "Syn team", "syn-m1", opened, status, status_date, pipe, notes)


def test_hiring_keeps_the_snapshot_status_date_and_restricts_notes():
    p = hiring(sheet(HIRING_COLUMNS, req(), req("SYN-2", pipe=None, notes=None)))
    assert p.errors == []
    a, b = p.rows
    assert (a.row, a.status_at_export, a.status_date, a.opened) == (4, "Open", date(2099, 1, 10), date(2099, 1, 2))
    assert a.notes_restricted == "syn note" and a.hiring_manager_source_id == "syn-m1"
    assert b.in_pipeline is None and b.notes_restricted is None           # not stated is not zero


def test_hiring_errors_are_reported_by_row_and_never_corrected():
    p = hiring(sheet(HIRING_COLUMNS, req(), req(), req("SYN-3", opened="soon"), req("SYN-4", status_date="2099-01-01"),
                     req("SYN-5", pipe=-1), req("SYN-6", status=None)))
    assert [r.req_id for r in p.rows] == ["SYN-1"]
    assert {(e.row, e.field) for e in p.errors} == {(5, "Req ID"), (6, "Opened"), (7, "Status Date"),
                                                    (8, "In pipeline"), (9, "Status")}
    assert hiring([TITLE, HIRING_COLUMNS[:-1]]).errors[0].field == "header"


def test_leave_is_kept_as_written_and_never_becomes_availability():
    p = leave(sheet(LEAVE_COLUMNS, ("S. Syn", "Casual", "2099-02-02", "2099-02-03", 2, "SA"),
                    ("S. Syn", "Personal (half day, PM)", "2099-02-05", "2099-02-05", 0.5, None),
                    ("T. Syn", "Sick", "2099-02-09", "2099-02-09", 3, "SA")))
    assert p.errors == [] and len(p.rows) == 3
    first = p.rows[0]
    assert (first.employee_source_id, first.start, first.end, first.days_as_written) == (
        "S. Syn", date(2099, 2, 2), date(2099, 2, 3), 2.0)
    assert not any(hasattr(first, a) for a in ("available", "availability", "absent"))
    assert {(f.row, f.field) for f in p.findings} == {(5, "Approved By"), (6, "Days")}


def test_leave_errors():
    p = leave(sheet(LEAVE_COLUMNS, ("S. Syn", "Casual", "2099-02-03", "2099-02-02", 1, "SA"),
                    ("S. Syn", "Casual", "Feb 3", "2099-02-03", 1, "SA"), ("S. Syn", "Casual", "2099-02-03",
                                                                         "2099-02-03", 0, "SA")))
    assert p.rows == [] and {(e.row, e.field) for e in p.errors} == {(4, "To"), (5, "From"), (6, "Days")}


def test_pulse_is_team_level_and_never_keeps_comment_text():
    p = pulse(sheet(PULSE_COLUMNS, ("Jul-Sep 2099", "Syn team", 6, 5, 3.4, "Not shown: below threshold"),
                    ("Oct-Dec 2098", "Syn team", 6, 6, 4, None),
                    ("Jan-Mar 2099", "Syn team", 6, 6, 4, "a free-text remark")))
    assert p.errors == []
    a, b, c = p.rows
    assert (a.quarter_start, a.quarter_end, a.engagement, a.comment_suppressed) == (
        date(2099, 7, 1), date(2099, 9, 30), 3.4, True)
    assert b.comment_suppressed is False and b.quarter_end == date(2098, 12, 31)
    assert "comment" not in {f for f in PulseRow.__dataclass_fields__ if f != "comment_suppressed"}
    assert all("remark" not in repr(r) for r in p.rows)                    # text is never retained
    assert [(f.row, f.field) for f in p.findings] == [(6, "Comments")]


def test_pulse_errors():
    p = pulse(sheet(PULSE_COLUMNS, ("Q3 2099", "Syn", 5, 5, 3, None), ("Jul-Sep 2099", "Syn", 5, 6, 3, None),
                    ("Jul-Sep 2099", "Syn", 5, 5, 6, None), ("Jul-Sep 2099", "Syn", 5, 5, 3, None),
                    ("Jul-Sep 2099", "Syn", 5, 5, 3, None), ("Jan-Jun 2099", "Syn", 5, 5, 3, None)))
    assert [r.row for r in p.rows] == [7]
    assert {(e.row, e.field) for e in p.errors} == {(4, "Quarter"), (5, "Responses"), (6, "Engagement (1-5)"),
                                                    (8, "row"), (9, "Quarter")}
