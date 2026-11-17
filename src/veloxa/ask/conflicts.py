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

The conflict REGISTER (`register`) lists every such disagreement visible at a read basis, with provenance for each
side (record, version, batch K, source path) and each side's own recorded and valid time. It is derived, never
stored: an earlier K reproduces the earlier register exactly. Resolution is a human judgement (§7.6); no resolution
record exists yet, so every entry is `open` and no value is chosen.
"""

from __future__ import annotations

import hashlib
import re

from ..db import tenant_tx
from ..signals import c1_delivery, c4_integrity, timebasis

REGISTER_VERSION = "conflicts/1"


def _first(label: str) -> str:
    return re.split(r"[\s(]", label.strip(), maxsplit=1)[0].lower()


def match(summary_label: str, formal_label: str) -> str | None:
    if summary_label.strip().lower() == formal_label.strip().lower():
        return "same_label"
    if " " not in summary_label.strip() and _first(formal_label) == summary_label.strip().lower():
        return "possibly_same_metric"
    return None


def disagreements(formal: list[c4_integrity.Reported], spoken: list[c4_integrity.Reported],
                  unparsed: list[dict], question: str | None) -> list[dict]:
    """Pure. `formal`: board figures; `spoken`: extracted summary figures (with parsed intervals).
    `question` None = every metric (the register); otherwise only the metrics the question names."""
    asked = None if question is None else {w.lower() for w in re.findall(r"[A-Za-z][\w-]*", question)}
    out = []
    for f in formal:
        if asked is not None and _first(f.label) not in asked:
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


def _observations(conn, basis, tz: str):
    formal = c4_integrity.load_reported(conn, basis, tz)
    sources = [s for s in c1_delivery.load_sources(conn, basis, tz)
               if s.kind == "transcript" and timebasis.excluded(s.fact_time, basis, tz) is None]
    _, spoken = c4_integrity.summary_observations(sources)
    from ..signals import figures

    unparsed = [{"record_id": s.record_id, "label": o.label, "value": o.span, "reason": "no unit stated"}
                for s in sources for o in figures.from_summary(s.record_id, s.body)
                if o.label and o.unit is None and o.number is not None]
    return formal, spoken, unparsed


def for_question(conn, basis, tz: str, question: str | None) -> list[dict]:
    formal, spoken, unparsed = _observations(conn, basis, tz)
    return disagreements(formal, spoken, unparsed, question)


def _time(ft) -> dict | None:
    if ft is None:
        return None
    return {"recorded_day": ft.recorded_day.isoformat() if ft.recorded_day else None,
            "recorded_basis": str(ft.recorded_basis), "valid_from": ft.valid_from.isoformat() if ft.valid_from
            else None, "valid_to": ft.valid_to.isoformat() if ft.valid_to else None,
            "valid_basis": str(ft.valid_basis), "extracted_at": ft.extracted_at.isoformat()}


def register(conn, basis, tz: str) -> list[dict]:
    """Every disagreement visible at `basis`, with per-side provenance and time. Deterministic order."""
    formal, spoken, unparsed = _observations(conn, basis, tz)
    found = disagreements(formal, spoken, unparsed, None)
    times = {o.record_id: o.fact_time for o in formal + spoken}
    ids = sorted({c["record_id"] for d in found for c in d["claims"]})
    with tenant_tx(conn, basis.tenant_id, batch_seq=basis.batch_seq):
        recs = {r["record_id"]: r for r in conn.execute(
            "SELECT DISTINCT ON (record_id) record_id, version, batch_seq, source_path FROM"
            " ledger.visible_source_record WHERE record_id = ANY(%s) ORDER BY record_id, version DESC",
            (ids,)).fetchall()} if ids else {}
    for d in found:
        key = "|".join([d["subject"]] + sorted(f"{c['record_id']}={c['value']}" for c in d["claims"]))
        d["conflict_id"] = hashlib.sha256(key.encode()).hexdigest()[:16]
        d["status"] = "open"                       # resolution is a human decision; none is recorded
        for c in d["claims"]:
            r = recs.get(c["record_id"])
            c["provenance"] = ({"version": r["version"], "batch_seq": r["batch_seq"], "source_path": r["source_path"]}
                               if r else None)
            c["time"] = _time(times.get(c["record_id"]))
    return sorted(found, key=lambda d: (d["subject"], d["conflict_id"]))
