"""Source adapters: C4 meeting-summary figure extraction and C1 revised deadlines (pure). SYNTHETIC text only:
every name, figure and date is invented, and nothing here is a real company finding."""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from veloxa.signals import c1_delivery as c1
from veloxa.signals import c4_integrity as c4
from veloxa.signals import figures
from veloxa.signals.contract import Authority, Flag, SignalType, Status


def obs(sentence):
    return figures.extract("synthetic:t", sentence)


# ---------------------------------------------------------------------------------------------- C4 extraction


def test_spoken_and_digit_numbers_are_parsed_exactly_or_refused():
    assert figures.spoken_number("forty-two") == (Decimal(42), 0)
    assert figures.spoken_number("two point five") == (Decimal("2.5"), 1)
    assert figures.spoken_number("one hundred twenty") == (Decimal(120), 0)
    for ambiguous in ("one-eighty", "forty fifty", "point two", "seven point twelve"):
        assert figures.spoken_number(ambiguous) is None


def test_inr_with_scale_and_rate_keeps_the_span_and_a_rounding_interval():
    (o,) = obs("Burn has been seventy-three lakhs a month.")
    assert (o.label, o.span, o.unit, o.rate, o.quality) == ("Burn", "seventy-three lakhs", "INR", "per month",
                                                           "extracted")
    assert (o.low, o.high) == (Decimal("72.5") * 10**5, Decimal("73.5") * 10**5)
    assert o.sentence[o.start:o.end].startswith("seventy-three lakhs") and o.period is None


def test_percentages_counts_periods_and_negatives():
    (m,) = obs("Gross margin was 37.5% for Jan to Mar 2026.")
    assert (m.label, m.unit, m.period, m.low, m.high) == ("Gross margin", "percent", "Jan to Mar 2026",
                                                          Decimal("37.45"), Decimal("37.55"))
    (c,) = obs("Customers reached 212 customers in Q2 2026.")
    assert (c.unit, c.period, c.number) == ("customers", "Q2 2026", Decimal(212))
    (n,) = obs("Cash moved by (9) percent.")
    assert n.number == Decimal(-9) and n.unit == "percent"


def test_multiple_figures_hedges_and_alternatives_are_separate_observations():
    a, b = obs('ARR is "around ninety-one crore, maybe ninety-four now".')
    assert a.unit == "INR" and a.hedged and a.quality == "extracted"
    assert b.unit is None and b.quality == "unit_missing"      # the second value states no unit: none is assumed


def test_unclear_meaning_is_never_a_metric():
    (bare,) = obs("The team counted thirty-six people at the offsite.")
    assert bare.label == "team" and bare.unit == "people"   # the nearby word is kept as written, never normalised
    (none,) = obs("We spoke for twenty-five minutes.")
    assert none.quality == "label_unclear" and none.unit is None
    (odd,) = obs('Headcount is "one-eighty, give or take".')
    assert odd.quality == "unparsed" and odd.number is None and odd.hedged


def _summary(rid, *lines, fact_time=None):
    return c1.SourceText(f"synthetic:{rid}", "transcript", "T", "\n".join(lines), fact_time)


def test_summary_observations_are_provisional_non_binding_evidence_without_scores():
    sigs, rows = c4.summary_observations([_summary("t1", "Burn is about forty lakhs a month.",
                                                   "Runway is nine months.", "We met for twenty minutes.")])
    assert len(sigs) == 2 and {s.signal_type for s in sigs} == {SignalType.FIGURE_OBSERVATION}
    assert c4.bare_numbers([_summary("t1", "We met for twenty minutes.")]) == 1   # no label, no unit: not a figure
    assert all(s.status == Status.PROVISIONAL and s.authority == Authority.TRANSCRIPT_SUMMARY and not s.value.numeric
               for s in sigs)
    burn = next(s for s in sigs if s.subject == "Burn")
    assert {Flag.PERIOD_MISSING, Flag.VALUE_APPROXIMATE, Flag.NON_BINDING_SOURCE} <= burn.flags
    assert dict(burn.calculation)["method"] == "summary-figures/1" and "rounding_interval" in dict(burn.calculation)
    assert [r.label for r in rows] == ["Burn", "Runway"]          # only fully extracted figures can be compared


def test_conflicting_summary_figures_are_preserved_but_never_resolved():
    _, rows = c4.summary_observations([_summary("a", "Runway is nine months for Q2 2026."),
                                       _summary("b", "Runway is fourteen months for Q2 2026.")])
    (d,) = c4.compare_reported(rows).signals
    assert d.signal_type == SignalType.FIGURE_DISCREPANCY and d.status == Status.PROVISIONAL and not d.value.numeric
    assert d.authority == Authority.TRANSCRIPT_SUMMARY and Flag.NON_BINDING_SOURCE in d.flags
    assert "does not say which source is correct" in d.missing[0]


def test_unstated_periods_hedges_and_rounding_are_never_discrepancies():
    _, rows = c4.summary_observations([_summary("a", "Runway is nine months."), _summary("b", "Runway is ten months.")])
    (p,) = c4.compare_reported(rows).signals
    assert p.status == Status.UNQUANTIFIED and Flag.PERIOD_MISMATCH in p.flags
    _, rows = c4.summary_observations([_summary("a", "Runway is about nine months for Q2 2026."),
                                       _summary("b", "Runway is fourteen months for Q2 2026.")])
    (h,) = c4.compare_reported(rows).signals
    assert Flag.VALUE_APPROXIMATE in h.flags and h.status == Status.UNQUANTIFIED
    _, rows = c4.summary_observations([_summary("a", "Margin is 40.1% for Q2 2026."),
                                       _summary("b", "Margin is 40.14% for Q2 2026.")])
    out = c4.compare_reported(rows)
    assert out.signals == [] and "rounding" in out.withheld[0].reason


def test_duplicate_observations_in_one_summary_are_not_compared_with_themselves():
    _, rows = c4.summary_observations([_summary("a", "Runway is nine months for Q2 2026.",
                                                "Runway is nine months for Q2 2026.")])
    assert len(rows) == 2 and c4.compare_reported(rows).signals == []


# ---------------------------------------------------------------------------------------------- C1 revised deadlines


def doc(rid, sent, *lines, kind="document"):
    head = [f"Quarterly update. Period: Jan to Mar 2026. Sent {sent}."] if sent else []
    return c1.SourceText(f"synthetic:{rid}", kind, "S", "\n".join(head + list(lines)))


def status(out):
    (s,) = [x for x in out.signals if x.signal_type == SignalType.COMMITMENT_STATUS]
    return s


def test_original_and_revised_dates_are_both_kept():
    out = c1.evaluate([doc("a", "9 Apr 2026", "Vega is on track for GA on 15 June 2026."),
                       doc("b", "9 Jul 2026", "Vega is late. We moved GA to 30 September 2026.")])
    (d,) = [s for s in out.signals if s.signal_type == SignalType.DELIVERY_DELAY]
    assert d.event_date == date(2026, 9, 30) and Flag.REVISED_DATE_STATED in d.flags
    s = status(out)
    assert s.value.category == "delayed_revised_date_stated" and s.event_date == date(2026, 9, 30)
    calc = dict(s.calculation)
    assert calc["business_object"] == "commitment:vega"
    assert calc["2026-04-09 stated"] == "2026-06-15" and calc["2026-07-09 revised"] == "2026-09-30"


def test_a_delay_without_a_new_date_never_gets_one():
    out = c1.evaluate([doc("a", "9 Apr 2026", "Vega is on track for GA on 15 June 2026."),
                       doc("b", "9 Jul 2026", "Vega is late. We moved GA to absorb rework.")])
    s = status(out)
    assert s.value.category == "delayed_new_date_not_stated" and s.event_date is None


def test_multiple_revisions_keep_their_order_and_the_latest_applies():
    out = c1.evaluate([doc("a", "9 Apr 2026", "Vega is on track for GA on 15 June 2026."),
                       doc("b", "9 Jul 2026", "Vega is late. We moved GA to 30 September 2026."),
                       doc("c", "9 Oct 2026", "Vega is late again. We postponed GA to 15 December 2026.")])
    s = status(out)
    assert Flag.MULTIPLE_REVISIONS in s.flags and s.event_date == date(2026, 12, 15)
    assert [k for k, _ in s.calculation[1:]] == ["2026-04-09 stated", "2026-07-09 revised", "2026-10-09 revised"]
    (c,) = [x for x in out.signals if x.signal_type == SignalType.COMMITMENT]
    assert Flag.DATE_SUPERSEDED in c.flags


def test_contradictory_same_day_sources_stay_a_conflict():
    out = c1.evaluate([doc("a", "9 Jul 2026", "Vega is on track for GA on 15 June 2026."),
                       doc("b", "9 Jul 2026", "Vega is late. We moved GA to 30 September 2026.")])
    s = status(out)
    assert s.value.category == "conflicting_statements" and s.event_date is None and s.status == Status.PROVISIONAL


def test_a_summary_is_provisional_and_never_a_formal_commitment():
    out = c1.evaluate([doc("t", None, "Vega was promised to customers by 30 June 2026.", kind="transcript")])
    (c,) = [x for x in out.signals if x.signal_type == SignalType.COMMITMENT]
    assert c.status == Status.PROVISIONAL and c.authority == Authority.TRANSCRIPT_SUMMARY
    assert Flag.NON_BINDING_SOURCE in c.flags
    formal = c1.evaluate([doc("a", "9 Apr 2026", "Vega is due on 30 June 2026.")])
    (f,) = [x for x in formal.signals if x.signal_type == SignalType.COMMITMENT]
    assert f.status == Status.ACTIVE and f.authority == Authority.FORMAL_REPORTED


def test_a_milestone_word_is_never_the_subject():
    out = c1.evaluate([doc("a", "9 Apr 2026", "Vega is in testing. GA is due on 30 June 2026.")])
    (c,) = [x for x in out.signals if x.signal_type == SignalType.COMMITMENT]
    assert c.subject == "Vega" and Flag.SUBJECT_FROM_CONTEXT in c.flags


# ---------------------------------------------------------------------------------------------- database



D1_AT = datetime.fromisoformat("2026-11-16T08:00:00+05:30")
D2_AT = datetime.fromisoformat("2026-11-16T09:00:00+05:30")
T9 = "meeting_summaries:transcripts/T9_Synthetic.docx#document"


@pytest.fixture
def versions(db, ingest_conn, tenant, tmp_path):
    """D1: a plan with a dated milestone and a summary stating a figure. D2: a later plan revising the date, and
    a CORRECTION of the summary (same file) changing the figure."""
    from synthetic_pack import make_d1, make_document, make_transcript

    from veloxa import projector
    from veloxa.db import connect
    from veloxa.ingest.pipeline import admit_pack
    from veloxa.releases import Pack, Schedule, current_time

    sched = Schedule("adp", "UTC", (Pack("D1", 1, "Day1_Mon_16Nov", D1_AT), Pack("D2", 2, "Day2_Tue_17Nov", D2_AT)))
    d1 = make_d1(tmp_path)
    make_document(d1, "Plan_Q1.pdf", ["Synthetic company: Board update",
                                      "Quarterly update. Period: Jan to Mar 2026. Sent 9 Apr 2026.", "Product",
                                      "Vega is on track for GA on 15 June 2026."])
    make_transcript(d1, "T9_Synthetic.docx", ["T9 synthetic call", "Runway is nine months for Q2 2026."])
    d2 = tmp_path / "Day2_Tue_17Nov"
    make_document(d2, "Plan_Q2.pdf", ["Synthetic company: Board update",
                                      "Quarterly update. Period: Apr to Jun 2026. Sent 9 Jul 2026.", "Product",
                                      "Vega is late. We moved GA to 30 September 2026."])
    make_transcript(d2, "T9_Synthetic.docx", ["T9 synthetic call", "Runway is eleven months for Q2 2026."])
    for p in ("D1", "D2"):
        admit_pack(ingest_conn, tenant, sched, p, tmp_path, current_time())
    with connect(db.dsn("projection")) as prj:
        projector.build(prj, tenant)
    return tenant


def _run(conn, tenant, mode, at=None, k=None):
    from dataclasses import replace

    from veloxa import ledger
    from veloxa.releases import current_time
    from veloxa.signals import registry
    from veloxa.temporal import resolve_basis

    b = resolve_basis(tenant, ledger.sealed_releases(conn, tenant), mode, at, "UTC", current_time())
    if k is not None:
        b = replace(b, batch_seq=k)
    return registry.run(conn, b, registry.context_for(b, "UTC", [], []))


@pytest.mark.db
def test_a_corrected_summary_changes_the_figure_only_from_its_batch(app_conn, versions):
    from veloxa.temporal import Mode

    def runway(result):
        (s,) = [x for x in result.signals if x.signal_type == SignalType.FIGURE_OBSERVATION and x.subject == "Runway"]
        return s

    k1 = runway(_run(app_conn, versions, Mode.KNOWN_AT, D2_AT - timedelta(seconds=1)))
    k2 = runway(_run(app_conn, versions, Mode.CURRENT))
    assert dict(k1.calculation)["span"] == "nine months" and k1.evidence[0].version == 1
    assert dict(k2.calculation)["span"] == "eleven months" and k2.evidence[0].version == 2
    assert k2.evidence[0].recorded_basis == "extraction_upper_bound" and k2.evidence[0].valid_basis == "unknown"
    valid = _run(app_conn, versions, Mode.VALID_AT, date(2026, 8, 1))     # an undated summary has unknown V
    assert not any(x.signal_type == SignalType.FIGURE_OBSERVATION for x in valid.signals)


@pytest.mark.db
def test_revised_deadlines_follow_k_and_recorded_time(app_conn, versions):
    from veloxa.temporal import Mode

    def st(result):
        return [x for x in result.signals if x.signal_type == SignalType.COMMITMENT_STATUS]

    assert st(_run(app_conn, versions, Mode.CURRENT, k=1)) == []        # one statement only: no status yet
    (now,) = st(_run(app_conn, versions, Mode.CURRENT))
    assert now.value.category == "delayed_revised_date_stated" and now.event_date == date(2026, 9, 30)
    assert st(_run(app_conn, versions, Mode.RECORDED_BY, date(2026, 5, 1))) == []   # the revision was recorded 9 Jul
    (later,) = st(_run(app_conn, versions, Mode.RECORDED_BY, date(2026, 8, 1)))
    assert later.event_date == date(2026, 9, 30)
