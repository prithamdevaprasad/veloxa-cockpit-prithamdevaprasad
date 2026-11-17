"""Typed contracts for the D2 people sources: hiring requisitions, the leave register and the team pulse survey.

A successful parse is NOT admission: these sources have no approved catalogue data class yet, so they cannot be
admitted (config/admission_authorisations.toml and the catalogue decide that, never a parser).
Rows come from openpyxl `values_only` iteration: a title row, a blank row, then the header (worksheet row 3).

Semantics kept, nothing invented:
- Hiring: `status_at_export` is the export-time snapshot, with the source's own `status_date` (its valid time).
  `notes_restricted` is recruiter free text: never a public employee fact, never a claim source on its own.
  The hiring manager is a SOURCE identifier (C12 decides any link).
- Leave: `start`/`end` and `days_as_written` exactly as stated (end inclusive as written). A leave type is not
  availability, and no availability is derived. Employee and approver are source identifiers (C12 contexts
  `leave.employee`, `leave.approver`).
- Pulse: team-level only. Comment TEXT is never retained: only whether the source suppressed it. Engagement is the
  source's stated team score (1-5); no per-person value exists or is derived.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

HIRING_COLUMNS = ("Req ID", "Role", "Team", "Hiring Manager", "Opened", "Status", "Status Date", "In pipeline",
                  "Notes")
LEAVE_COLUMNS = ("Employee", "Leave Type", "From", "To", "Days", "Approved By")
PULSE_COLUMNS = ("Quarter", "Team", "Team size", "Responses", "Engagement (1-5)", "Comments")
_QUARTER = re.compile(r"^(Jan|Apr|Jul|Oct)-(Mar|Jun|Sep|Dec) (\d{4})$")
_QUARTER_END = {"Jan": ("Mar", 3, 31), "Apr": ("Jun", 6, 30), "Jul": ("Sep", 9, 30), "Oct": ("Dec", 12, 31)}
_MONTH = {"Jan": 1, "Apr": 4, "Jul": 7, "Oct": 10}
SUPPRESSED_MARKER = "Not shown"


@dataclass(frozen=True)
class Issue:
    row: int
    field: str
    reason: str


@dataclass(frozen=True)
class Requisition:
    row: int
    req_id: str
    role: str
    team: str
    hiring_manager_source_id: str | None
    opened: date
    status_at_export: str
    status_date: date
    in_pipeline: int | None          # None = not stated, never zero
    notes_restricted: str | None


@dataclass(frozen=True)
class LeaveEntry:
    row: int
    employee_source_id: str
    leave_type: str
    start: date
    end: date
    days_as_written: float
    approver_source_id: str | None


@dataclass(frozen=True)
class PulseRow:
    row: int
    quarter_label: str
    quarter_start: date
    quarter_end: date
    team: str
    team_size: int
    responses: int
    engagement: float
    comment_suppressed: bool         # the text itself is never kept


@dataclass
class Parsed:
    rows: list = field(default_factory=list)
    errors: list[Issue] = field(default_factory=list)
    findings: list[Issue] = field(default_factory=list)


def _text(v) -> str | None:
    return None if v is None or not str(v).strip() else str(v).strip()


def _date(v) -> date:
    if isinstance(v, date):
        return v if type(v) is date else v.date()
    s = _text(v)
    if s is None or len(s) != 10:
        raise ValueError("not an ISO date (YYYY-MM-DD)")
    return date.fromisoformat(s)


def _body(rows: list[tuple], columns: tuple[str, ...], out: Parsed):
    rows = list(rows)
    hi = next((i for i, r in enumerate(rows) if r and r[0] == columns[0]), None)
    if hi is None or tuple(rows[hi][:len(columns)]) != columns or any(c is not None for c in rows[hi][len(columns):]):
        out.errors.append(Issue(0, "header", f"expected {list(columns)}"))
        return []
    return [(i, dict(zip(columns, r, strict=False))) for i, r in enumerate(rows[hi + 1:], start=hi + 2)
            if any(c is not None for c in r)]


def hiring(rows: list[tuple]) -> Parsed:
    out = Parsed()
    seen: dict[str, int] = {}
    for i, c in _body(rows, HIRING_COLUMNS, out):
        errs = []
        req = _text(c["Req ID"])
        if not req:
            errs.append(("Req ID", "missing"))
        elif req in seen:
            errs.append(("Req ID", f"duplicate of row {seen[req]}"))
        for f in ("Role", "Team", "Status"):
            if not _text(c[f]):
                errs.append((f, "missing"))
        dates = {}
        for f in ("Opened", "Status Date"):
            try:
                dates[f] = _date(c[f])
            except (ValueError, TypeError):
                errs.append((f, "not an ISO date (YYYY-MM-DD)"))
        pipe = c["In pipeline"]
        if pipe is not None and (isinstance(pipe, bool) or not isinstance(pipe, int) or pipe < 0):
            errs.append(("In pipeline", "not a non-negative whole number"))
        if len(dates) == 2 and dates["Status Date"] < dates["Opened"]:
            errs.append(("Status Date", "before Opened"))
        if errs:
            out.errors += [Issue(i, f, why) for f, why in errs]
            continue
        seen[req] = i
        out.rows.append(Requisition(i, req, _text(c["Role"]), _text(c["Team"]), _text(c["Hiring Manager"]),
                                    dates["Opened"], _text(c["Status"]), dates["Status Date"], pipe,
                                    _text(c["Notes"])))
    return out


def leave(rows: list[tuple]) -> Parsed:
    out = Parsed()
    for i, c in _body(rows, LEAVE_COLUMNS, out):
        errs = []
        emp, kind = _text(c["Employee"]), _text(c["Leave Type"])
        if not emp:
            errs.append(("Employee", "missing"))
        if not kind:
            errs.append(("Leave Type", "missing"))
        span = {}
        for f in ("From", "To"):
            try:
                span[f] = _date(c[f])
            except (ValueError, TypeError):
                errs.append((f, "not an ISO date (YYYY-MM-DD)"))
        days = c["Days"]
        if isinstance(days, bool) or not isinstance(days, (int, float)) or days <= 0:
            errs.append(("Days", "not a positive number"))
        if len(span) == 2 and span["To"] < span["From"]:
            errs.append(("To", "before From"))
        if errs:
            out.errors += [Issue(i, f, why) for f, why in errs]
            continue
        entry = LeaveEntry(i, emp, kind, span["From"], span["To"], float(days), _text(c["Approved By"]))
        calendar = (entry.end - entry.start).days + 1
        if entry.days_as_written > calendar:
            out.findings.append(Issue(i, "Days", f"more days than the {calendar} calendar day(s) stated; kept as "
                                                 "written"))
        if entry.approver_source_id is None:
            out.findings.append(Issue(i, "Approved By", "approver not recorded (unknown, not unapproved)"))
        out.rows.append(entry)
    return out


def pulse(rows: list[tuple]) -> Parsed:
    out = Parsed()
    seen: dict[tuple[str, str], int] = {}
    for i, c in _body(rows, PULSE_COLUMNS, out):
        errs = []
        label, team = _text(c["Quarter"]), _text(c["Team"])
        m = _QUARTER.match(label or "")
        if not m or _QUARTER_END[m.group(1)][0] != m.group(2):
            errs.append(("Quarter", "not a calendar quarter label (e.g. Jan-Mar 2026)"))
        if not team:
            errs.append(("Team", "missing"))
        size, resp, eng = c["Team size"], c["Responses"], c["Engagement (1-5)"]
        for f, v in (("Team size", size), ("Responses", resp)):
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                errs.append((f, "not a non-negative whole number"))
        if isinstance(eng, bool) or not isinstance(eng, (int, float)) or not 1 <= eng <= 5:
            errs.append(("Engagement (1-5)", "not a number from 1 to 5"))
        if not errs and resp > size:
            errs.append(("Responses", "more responses than team members"))
        if not errs and (label, team) in seen:
            errs.append(("row", f"duplicate quarter and team (row {seen[(label, team)]})"))
        if errs:
            out.errors += [Issue(i, f, why) for f, why in errs]
            continue
        seen[(label, team)] = i
        year = int(m.group(3))
        _, end_month, end_day = _QUARTER_END[m.group(1)]
        comment = _text(c["Comments"])
        suppressed = comment is not None and comment.startswith(SUPPRESSED_MARKER)
        if comment is not None and not suppressed:
            out.findings.append(Issue(i, "Comments", "unsuppressed comment text present: not retained"))
        out.rows.append(PulseRow(i, label, date(year, _MONTH[m.group(1)], 1), date(year, end_month, end_day), team,
                                 size, resp, float(eng), suppressed))
    return out
