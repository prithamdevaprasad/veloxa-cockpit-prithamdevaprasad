"""Figure observations from meeting summaries (C4 source adapter; ARCHITECTURE.md §16.2, D15-03c/d).

Deterministic extraction (method `summary-figures/1`, never a model) of figures stated in admitted meeting-summary
prose. Each observation keeps the exact span and its offsets, the number as parsed (digits or words), the unit or
currency only if stated, the metric label only as written nearby, a period only if an absolute period is stated,
a conservative rounding interval from the precision as written, and whether the speaker hedged.

It never normalises a label ("ARR" stays "ARR"), never infers an unstated period or unit, and never treats a bare
number as a metric: without a nearby metric label the observation is `label_unclear`. A spoken form it cannot read
unambiguously (e.g. "one-eighty") is `unparsed`, not guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

METHOD = "summary-figures/1"
LABELS = ("ARR", "MRR", "revenue", "burn", "runway", "headcount", "team", "customers", "margin", "churn",
          "attrition", "bookings", "cash", "EBITDA", "gross margin", "net revenue retention", "NRR")
_LABEL = re.compile(r"\b(" + "|".join(re.escape(x) for x in sorted(LABELS, key=len, reverse=True)) + r")\b", re.I)
_HEDGE = re.compile(r"\b(around|about|roughly|approximately|approx\.?|maybe|give or take|sometimes|or so|i think|"
                    r"may have said|or was it|something like|nearly|almost)\b", re.I)
_UNITS = {"crore": ("INR", Decimal(10) ** 7), "cr": ("INR", Decimal(10) ** 7), "lakh": ("INR", Decimal(10) ** 5),
          "lakhs": ("INR", Decimal(10) ** 5), "l": ("INR", Decimal(10) ** 5), "%": ("percent", Decimal(1)),
          "percent": ("percent", Decimal(1)), "months": ("months", Decimal(1)), "month": ("months", Decimal(1)),
          "people": ("people", Decimal(1)), "employees": ("people", Decimal(1)),
          "customers": ("customers", Decimal(1))}
_RATE = re.compile(r"^\W*(?:a|per)\s+(month|year|quarter)\b|^\W*(monthly|annually)\b", re.I)
_PERIOD = re.compile(r"\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+to\s+(?:Jan|Feb|Mar|Apr|May|"
                     r"Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}|Q[1-4]\s+(?:FY)?\d{4}|FY\s?\d{2,4})\b")
_ONES = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                    "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if i >= 2}
_WORD = r"(?:" + "|".join(sorted(list(_ONES) + list(_TENS) + ["hundred", "point"], key=len, reverse=True)) + r")"
_SPOKEN = re.compile(rf"\b{_WORD}(?:[\s-]+{_WORD})*\b", re.I)
_DIGITS = re.compile(r"(?<![\w.])(?P<neg>-|\()?(?:(?P<cur>Rs\.?|₹|INR)\s*)?(?P<num>\d{1,3}(?:,\d{2,3})+|\d+(?:\.\d+)?)"
                     r"\)?(?P<pct>%)?")


@dataclass(frozen=True)
class FigureObservation:
    record_id: str
    sentence: str
    span: str                 # the figure exactly as written
    start: int                # character offsets of the span in the sentence
    end: int
    label: str | None         # the metric label as written nearby, or None
    number: Decimal | None    # in the stated unit's own scale (e.g. 2.2 for "two point two lakhs")
    low: Decimal | None       # conservative rounding interval, in base units (INR for currency)
    high: Decimal | None
    unit: str | None          # INR | percent | months | people | customers | None (not stated)
    rate: str | None          # per month | per year ... when stated
    period: str | None        # only an absolute period stated in the sentence
    hedged: bool
    quality: str              # extracted | unit_missing | label_unclear | unparsed
    method: str = METHOD


def spoken_number(text: str) -> tuple[Decimal, int] | None:
    """'forty-two' -> (42, 0); 'two point two' -> (2.2, 1). Ambiguous spoken forms ('one-eighty') -> None."""
    words = [w for w in re.split(r"[\s-]+", text.lower()) if w]
    if "point" in words:
        i = words.index("point")
        whole, frac = spoken_number(" ".join(words[:i])), words[i + 1:]
        if whole is None or not frac or any(w not in _ONES or _ONES[w] > 9 for w in frac):
            return None
        return Decimal(f"{whole[0]}.{''.join(str(_ONES[w]) for w in frac)}"), len(frac)
    total, current, last = 0, 0, None
    for w in words:
        if w in _TENS:
            if last in ("tens", "ones") and current:      # "one eighty", "forty fifty": not a single number
                return None
            current += _TENS[w]
            last = "tens"
        elif w in _ONES:
            if last == "ones" or (last == "tens" and _ONES[w] >= 10) or (last == "tens" and current % 10):
                return None
            current += _ONES[w]
            last = "ones"
        elif w == "hundred":
            if not current:
                return None
            current *= 100
            last = "hundred"
        else:
            return None
    total += current
    return (Decimal(total), 0) if words else None


def _unit_after(rest: str) -> tuple[str | None, Decimal, int, str | None]:
    """(unit, scale, chars consumed, rate) from the text right after a number."""
    m = re.match(r"\s*(crore|cr|lakhs|lakh|l|percent|%|months|month|people|employees|customers)\b\.?", rest, re.I) \
        or re.match(r"\s*(%)", rest)
    unit, scale, used = None, Decimal(1), 0
    if m:
        unit, scale = _UNITS[m.group(1).lower()]
        used = m.end()
    r = _RATE.match(rest[used:])
    return unit, scale, used, (f"per {r.group(1) or r.group(2)}".replace("per monthly", "per month")
                               .replace("per annually", "per year") if r else None)


def _interval(number: Decimal, decimals: int, scale: Decimal, hedged: bool) -> tuple[Decimal, Decimal]:
    half = Decimal(5) * Decimal(10) ** (-(decimals + 1))
    return (number - half) * scale, (number + half) * scale


def extract(record_id: str, sentence: str) -> list[FigureObservation]:
    out: list[FigureObservation] = []
    period_m = _PERIOD.search(sentence)
    period = period_m.group(1) if period_m else None
    hedged = bool(_HEDGE.search(sentence))
    spans: list[tuple[int, int, Decimal | None, int, bool, bool]] = []   # start, end, number, decimals, neg, cur
    for m in _DIGITS.finditer(sentence):
        if period_m and period_m.start() <= m.start() < period_m.end():
            continue                                                     # part of a stated period, not a figure
        num = Decimal(m["num"].replace(",", ""))
        decimals = len(m["num"].split(".")[1]) if "." in m["num"] else 0
        neg = bool(m["neg"]) and (m["neg"] == "-" or m.group(0).endswith(")"))
        spans.append((m.start(), m.end(), -num if neg else num, decimals, neg, bool(m["cur"]) or False))
    for m in _SPOKEN.finditer(sentence):
        if m.group(0).lower() in ("point", "hundred"):
            continue
        parsed = spoken_number(m.group(0))
        spans.append((m.start(), m.end(), parsed[0] if parsed else None, parsed[1] if parsed else 0, False, False))
    for start, end, number, decimals, _neg, cur in sorted(spans):
        unit, scale, used, rate = _unit_after(sentence[end:])
        if sentence[end:end + 1] == "%" or sentence[start:end].endswith("%"):
            unit, scale = "percent", Decimal(1)
        if cur and unit is None:
            unit = "INR"
        span = sentence[start:end + used].strip()
        if span.endswith(".") and not span.lower().endswith(("cr.", "l.")):
            span, used = span[:-1], used - 1      # a sentence-final full stop is not part of the figure
        before = sentence[:start]
        labels = list(_LABEL.finditer(before))
        label = labels[-1].group(1) if labels else None
        if unit in ("people", "customers") and label is None:
            label = None                                     # a count with no metric label stays unclear
        if number is None:
            quality = "unparsed"
        elif label is None:
            quality = "label_unclear"
        elif unit is None:
            quality = "unit_missing"
        else:
            quality = "extracted"
        low = high = None
        if number is not None and unit is not None:
            low, high = _interval(number, decimals, scale, hedged)
        out.append(FigureObservation(record_id, sentence, span, start, end + used, label, number, low, high, unit,
                                     rate, period, hedged, quality))
    return out


def from_summary(record_id: str, body: str) -> list[FigureObservation]:
    """Every figure in a meeting summary, line by line (summary lines are independent points)."""
    out = []
    for line in body.splitlines():
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z\"“])", line.strip()):
            if sentence:
                out += extract(record_id, sentence)
    return out
