"""Figure disagreements for Ask (H-11: "the sources disagree"). Deterministic; chooses no value.

Two observations of POSSIBLY the same metric disagree when:
- the metric match is either an identical label or a meeting-summary label that is the leading token of a formal
  label (e.g. "ARR" and "ARR (recurring)"). The latter is reported as `possibly_same_metric` with the definition
  unconfirmed; definitions are never merged;
- both state the SAME unit (never converted or inferred). A value without a unit is listed as not comparable;
- their conservative rounding intervals do not overlap;
- their periods do not exclude each other. Two different STATED periods are not a disagreement (a later quarter
  is not a contradiction of an earlier one); an unknown period cannot be ruled out, so it is flagged
  `period_unconfirmed`.
"""

from __future__ import annotations

import re

from ..signals import c1_delivery, c4_integrity, timebasis


def _first(label: str) -> str:
    return re.split(r"[\s(]", label.strip(), maxsplit=1)[0].lower()


def match(summary_label: str, formal_label: str) -> str | None:
    if summary_label.strip().lower() == formal_label.strip().lower():
        return "same_label"
    if " " not in summary_label.strip() and _first(formal_label) == summary_label.strip().lower():
        return "possibly_same_metric"
    return None


def disagreements(formal: list[c4_integrity.Reported], spoken: list[c4_integrity.Reported],
                  unparsed: list[dict], question: str) -> list[dict]:
    """Pure. `formal`: board figures; `spoken`: extracted summary figures (with parsed intervals)."""
    asked = {w.lower() for w in re.findall(r"[A-Za-z][\w-]*", question)}
    out = []
    for f in formal:
        if _first(f.label) not in asked:
            continue
        pf = c4_integrity.parse(f.value_text)
        if pf is None:
            continue
        for s in spoken:
            kind = match(s.label, f.label)
            if kind is None or s.parsed is None or s.parsed.unit != pf.unit:
                continue
            if f.period and s.period and f.period != s.period:
                continue
            if s.parsed.high >= pf.low and pf.high >= s.parsed.low:
                continue                                   # rounding could explain it
            out.append({
                "subject": f.label, "metric_match": kind,
                "definition": "confirmed" if kind == "same_label" else "unconfirmed",
                "period": "same" if f.period and s.period else "period_unconfirmed",
                "claims": [{"record_id": f.record_id, "value": f.value_text, "unit": pf.unit, "period": f.period,
                            "source": "formal document (reported, not verified)"},
                           {"record_id": s.record_id, "value": s.value_text, "unit": s.parsed.unit,
                            "period": s.period, "source": "meeting summary (auto-generated, non-binding)"
                            + (", stated approximately" if s.hedged else "")}],
                "not_comparable": [u for u in unparsed if match(u["label"], f.label)],
            })
    return out


def for_question(conn, basis, tz: str, question: str) -> list[dict]:
    formal = c4_integrity.load_reported(conn, basis, tz)
    sources = [s for s in c1_delivery.load_sources(conn, basis, tz)
               if s.kind == "transcript" and timebasis.excluded(s.fact_time, basis, tz) is None]
    _, spoken = c4_integrity.summary_observations(sources)
    from ..signals import figures

    unparsed = [{"record_id": s.record_id, "label": o.label, "value": o.span, "reason": "no unit stated"}
                for s in sources for o in figures.from_summary(s.record_id, s.body)
                if o.label and o.unit is None and o.number is not None]
    return disagreements(formal, spoken, unparsed, question)
