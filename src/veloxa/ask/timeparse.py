"""Temporal parse (ARCHITECTURE.md §13.1 stage 3, §8.3): question wording or request parameters -> mode and as-of.

Deterministic. A time phrase is recognised only with an explicit cue ("as known on", "as of", "recorded by",
"true on" ...). A bare date elsewhere in a question is content (e.g. "who joined on 2026-01-05"), not a read
basis. Explicit request parameters always win over wording, and the result is always echoed in the answer.

Dates: ISO (2026-11-17, 2026-11-17T12:00+05:30), "17 Nov 2026", "17 November", "Nov 17", "17/11/2026",
optionally followed by a time ("at noon", "10:00", "10am"). A date without a year takes the clock's year.
A date-only value means the END of that day in the tenant time zone (resolved later by temporal.resolve_basis).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from ..temporal import Mode

_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                       "dec"), start=1)}
_MON = r"(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|" \
       r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_TIME = r"(?:\s*(?:,|at)?\s*(?P<time>noon|midnight|\d{1,2}(?::\d{2})?\s*(?:am|pm)|\d{1,2}:\d{2})(?:\s*ist)?)?"
_DATE = (
    r"(?P<iso>\d{4}-\d{2}-\d{2}(?:[t ]\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|z)?)?)"
    r"|(?P<dmy>\d{1,2})(?:st|nd|rd|th)?\s+" + _MON + r"\.?(?:,?\s+(?P<y1>\d{4}))?" + _TIME
    + r"|" + _MON.replace("mon>", "mon2>") + r"\.?\s+(?P<md>\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(?P<y2>\d{4}))?"
    + _TIME.replace("time>", "time2>")
    + r"|(?P<sd>\d{1,2})/(?P<sm>\d{1,2})/(?P<sy>\d{4})"
)

# Cue phrases, most specific first. Each maps to a mode.
_CUES: list[tuple[Mode, str]] = [
    (Mode.RECORDED_BY,
     r"(?:had been |was |were )?(?:recorded|documented|written down|on record)\s+(?:by|before|as of)"),
    (Mode.VALID_AT, r"(?:actually |really )?(?:true|valid|the case|in effect|effective)\s+(?:on|at|as of)"),
    (Mode.KNOWN_AT, r"(?:as (?:we )?(?:knew|know|known)(?: it)?|what (?:did )?(?:we|you|i) (?:know|knew)|"
                    r"(?:known|knew)|as (?:of|at)|according to what we knew)\s+(?:on|at|by|as of)?"),
]


@dataclass(frozen=True)
class TimeSpec:
    mode: Mode
    as_of: date | datetime | None
    source: str               # "default" | "wording" | "parameters"
    phrase: str | None        # the matched wording, echoed in the interpretation
    remainder: str            # the question with the time phrase removed (for planning)


def _clock_time(text: str | None) -> time | None:
    if not text:
        return None
    t = text.strip().lower().replace(" ", "")
    if t == "noon":
        return time(12, 0)
    if t == "midnight":
        return time(0, 0)
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(am|pm)?", t)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    if h > 23 or mi > 59:
        raise ValueError(f"invalid time {text!r}")
    return time(h, mi)


def parse_date(text: str, default_year: int, tz: str) -> date | datetime | None:
    """Parse one date or date-time expression (whole string). Returns None if it is not a date."""
    m = re.fullmatch(_DATE, text.strip(), re.IGNORECASE)
    return _from_match(m, default_year, tz) if m else None


def _from_match(m: re.Match, default_year: int, tz: str) -> date | datetime:
    g = m.groupdict()
    if g["iso"]:
        s = g["iso"].upper().replace("Z", "+00:00")
        if len(s) == 10:
            return date.fromisoformat(s)
        dt = datetime.fromisoformat(s.replace(" ", "T"))
        return dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo(tz))
    if g["sd"]:
        d = date(int(g["sy"]), int(g["sm"]), int(g["sd"]))
        clock = None
    elif g["dmy"]:
        d = date(int(g["y1"] or default_year), _MONTHS[g["mon"][:3].lower()], int(g["dmy"]))
        clock = _clock_time(g["time"])
    else:
        d = date(int(g["y2"] or default_year), _MONTHS[g["mon2"][:3].lower()], int(g["md"]))
        clock = _clock_time(g["time2"])
    return datetime.combine(d, clock, tzinfo=ZoneInfo(tz)) if clock else d


def find_dates(text: str, default_year: int, tz: str) -> list[tuple[date | datetime, tuple[int, int]]]:
    """Every date expression in free text, with its span."""
    out = []
    for m in re.finditer(rf"(?<![\w-])(?:{_DATE})(?![\w-])", text, re.IGNORECASE):
        try:
            out.append((_from_match(m, default_year, tz), m.span()))
        except ValueError:
            continue
    return out


def parse(question: str, now: datetime, tz: str, mode: str | None = None, as_of: str | None = None,
          as_known_at: str | None = None) -> TimeSpec:
    """Resolve mode and as-of from explicit parameters, else from cue wording, else CURRENT.

    Raises ValueError for invalid parameters (the API turns this into the typed error `invalid_time`)."""
    year = now.astimezone(ZoneInfo(tz)).year
    if mode or as_of or as_known_at:
        if as_known_at and (as_of or (mode and mode.upper() != "KNOWN_AT")):
            raise ValueError("use either as_known_at, or mode with as_of")
        m = Mode.KNOWN_AT if as_known_at else Mode((mode or "CURRENT").upper())
        raw = as_known_at or as_of
        value = parse_date(raw, year, tz) if raw else None
        if raw and value is None:
            raise ValueError(f"cannot read {raw!r} as a date or instant")
        if m != Mode.CURRENT and value is None:
            raise ValueError(f"{m} requires as_of")
        if m == Mode.CURRENT and value is not None:
            raise ValueError("CURRENT takes no date")
        return TimeSpec(m, value, "parameters", None, question)
    for cue_mode, cue in _CUES:
        rx = re.compile(rf"\b(?:{cue})\s*(?:{_DATE})", re.IGNORECASE)
        found = rx.search(question)
        if found:
            dates = find_dates(found.group(0), year, tz)
            if dates:
                remainder = (question[:found.start()] + question[found.end():]).strip(" ,?.") + "?"
                return TimeSpec(cue_mode, dates[-1][0], "wording", found.group(0).strip(), remainder)
    return TimeSpec(Mode.CURRENT, None, "default", None, question)
