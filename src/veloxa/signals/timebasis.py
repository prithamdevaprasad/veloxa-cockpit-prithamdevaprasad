"""Per-fact recorded and valid time for detector inputs (ARCHITECTURE.md §8.1, §8.4 T2-T6).

The database enforces K (sealed batches, latest version at K). Recorded time R and valid time V belong to each
FACT, so detectors apply them here, from what the source itself establishes, and never invent them:

R (when the source recorded it):
- the source states its own date (e.g. a formal document's "Sent <date>"): R is that day, [00:00, next 00:00) in
  the tenant time zone (T6), basis SOURCE_DATE;
- otherwise R is only bounded above by the extraction time E (T4: undated material has recorded <= its extraction
  upper bound), basis EXTRACTION_UPPER_BOUND. The invariant R <= E is checked; a source date after E is not
  trusted and falls back to the extraction bound (flagged).

V (when the fact held):
- a figure for a stated period holds over that period (STATED_PERIOD);
- a statement of status or intent holds from the day it was made (STATEMENT_DATE), until a later statement
  supersedes it (handled by the detector's status rules);
- otherwise V is UNKNOWN, and the fact is never used to answer VALID_AT (a snapshot or undated fact is not
  silently treated as historically valid, T3).

Mode rules (mirroring the SQL predicate, §8.3): CURRENT and KNOWN_AT filter by K only; RECORDED_BY(X) keeps a fact
only if upper(R) <= X; VALID_AT(X) keeps a fact only if X lies in its known V.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from ..temporal import Mode, ReadBasis


class RecordedBasis(StrEnum):
    SOURCE_DATE = "source_date"
    EXTRACTION_UPPER_BOUND = "extraction_upper_bound"


class ValidBasis(StrEnum):
    STATED_PERIOD = "stated_period"
    STATEMENT_DATE = "statement_date"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FactTime:
    extracted_at: datetime               # E
    recorded_hi: datetime                # upper(R), exclusive
    recorded_basis: RecordedBasis
    recorded_day: date | None            # the stated day, when the source states one
    valid_from: date | None              # V = [valid_from, valid_to); valid_to None = open
    valid_to: date | None
    valid_basis: ValidBasis
    note: str | None = None              # e.g. an R <= E inconsistency


def _start(d: date, tz: str) -> datetime:
    return datetime.combine(d, time.min, tzinfo=ZoneInfo(tz))


def recorded(source_day: date | None, extracted_at: datetime, tz: str) -> tuple[datetime, RecordedBasis,
                                                                                 date | None, str | None]:
    if source_day is not None:
        if _start(source_day, tz) > extracted_at:
            return (extracted_at, RecordedBasis.EXTRACTION_UPPER_BOUND, None,
                    f"the source's own date ({source_day.isoformat()}) is after its extraction; not trusted "
                    "(R <= E)")
        return _start(source_day + timedelta(days=1), tz), RecordedBasis.SOURCE_DATE, source_day, None
    return extracted_at, RecordedBasis.EXTRACTION_UPPER_BOUND, None, None


def statement_time(source_day: date | None, extracted_at: datetime, tz: str) -> FactTime:
    hi, basis, day, note = recorded(source_day, extracted_at, tz)
    if day is None:
        return FactTime(extracted_at, hi, basis, None, None, None, ValidBasis.UNKNOWN, note)
    return FactTime(extracted_at, hi, basis, day, day, None, ValidBasis.STATEMENT_DATE, note)


_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                       "dec"), start=1)}
_PERIOD = re.compile(r"^\s*(?P<a>[A-Za-z]{3})[a-z]*\s+to\s+(?P<b>[A-Za-z]{3})[a-z]*\s+(?P<y>\d{4})\s*$")


def stated_period(text: str | None) -> tuple[date, date] | None:
    """'Apr to Jun 2026' -> [2026-04-01, 2026-07-01). Anything else -> None (unknown, never guessed)."""
    m = _PERIOD.match(text or "")
    if not m or m["a"].lower() not in _MONTHS or m["b"].lower() not in _MONTHS:
        return None
    y, a, b = int(m["y"]), _MONTHS[m["a"].lower()], _MONTHS[m["b"].lower()]
    if b < a:
        return None  # a period spanning a year end states no single year; not guessed
    end = date(y + 1, 1, 1) if b == 12 else date(y, b + 1, 1)
    return date(y, a, 1), end


def figure_time(issued_on: date | None, period: str | None, extracted_at: datetime, tz: str) -> FactTime:
    hi, basis, day, note = recorded(issued_on, extracted_at, tz)
    span = stated_period(period)
    if span is None:
        return FactTime(extracted_at, hi, basis, day, None, None, ValidBasis.UNKNOWN, note)
    return FactTime(extracted_at, hi, basis, day, span[0], span[1], ValidBasis.STATED_PERIOD, note)


def excluded(ft: FactTime, basis: ReadBasis, tz: str) -> str | None:
    """None if the fact may be used at this basis, else the reason it is withheld."""
    if basis.mode == Mode.RECORDED_BY:
        if ft.recorded_hi > basis.recorded_cutoff:
            return "recorded after the RECORDED_BY cutoff" if ft.recorded_basis == RecordedBasis.SOURCE_DATE \
                else "its recorded time is bounded only by its extraction, which is after the RECORDED_BY cutoff"
    if basis.mode == Mode.VALID_AT:
        if ft.valid_basis == ValidBasis.UNKNOWN:
            return "its valid time is unknown, so it cannot answer VALID_AT"
        v = basis.valid_at.astimezone(ZoneInfo(tz)).date()
        if v < ft.valid_from or (ft.valid_to is not None and v >= ft.valid_to):
            return "it does not hold at the VALID_AT instant"
    return None
