"""Evidence detectors C1-C4 (src/veloxa/signals): the output contract and each criterion pipeline, PURE (no
database). Every input here is SYNTHETIC and invented for the test; nothing is a real company finding."""

from datetime import date
from decimal import Decimal

import pytest

from veloxa.risk import detectors as approved
from veloxa.signals import c1_delivery as c1
from veloxa.signals import c2_financial as c2
from veloxa.signals import c3_continuity as c3
from veloxa.signals import c4_integrity as c4
from veloxa.signals.contract import (
    Authority,
    ContractError,
    Criterion,
    EvidenceRef,
    Flag,
    Signal,
    SignalType,
    Status,
    Value,
    ValueKind,
)

REF = EvidenceRef("synthetic:rec-1", "row 1")


def sig(**kw):
    base = dict(detector="test@1", criterion=Criterion.C2, signal_type=SignalType.FINANCIAL_EXPOSURE,
                status=Status.ACTIVE, subject="synthetic object", measure="C2.x@1",
                value=Value(ValueKind.EXACT, 5.0, 5.0, unit="INR"), authority=Authority.SYSTEM_RECORD,
                evidence=(REF,))
    return Signal(**(base | kw))


# ---------------------------------------------------------------------------------------------------------------
# Common contract
# ---------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("status", [Status.MISSING, Status.UNQUANTIFIED, Status.UNMAPPED, Status.BLOCKED])
def test_a_numberless_status_can_never_carry_a_number_so_missing_is_never_zero(status):
    with pytest.raises(ContractError, match="never zero"):
        sig(status=status, value=Value(ValueKind.EXACT, 0.0, 0.0, unit="INR"))
    assert sig(status=status, value=Value()).value.numeric is False


def test_evidence_backed_statuses_must_cite_records_and_state_authority():
    for status in (Status.ACTIVE, Status.PROVISIONAL):
        with pytest.raises(ContractError, match="cite admitted evidence"):
            sig(status=status, evidence=())
        with pytest.raises(ContractError, match="authority"):
            sig(status=status, authority=Authority.NONE)


def test_controlled_vocabularies_are_enforced():
    with pytest.raises(ContractError):
        sig(status="active")
    with pytest.raises(ContractError):
        sig(flags=frozenset({"made_up_flag"}))
    with pytest.raises(ContractError):
        sig(detector="no-version")
    for kind, low, high in ((ValueKind.INTERVAL, 3.0, 1.0), (ValueKind.EXACT, 1.0, 2.0),
                            (ValueKind.INTERVAL, float("nan"), 1.0)):
        with pytest.raises(ContractError):
            Value(kind, low, high, unit="INR")
    with pytest.raises(ContractError):
        Value(ValueKind.EXACT, 1.0, 1.0)  # no unit
    with pytest.raises(ContractError):
        Value(ValueKind.CATEGORY, 1.0, 1.0, category="x")


def test_signal_ids_are_deterministic_and_carry_the_detector_version():
    assert sig().signal_id == sig().signal_id
    assert sig(detector="test@2").signal_id != sig().signal_id
    assert sig().body()["detector"] == "test@1"
    assert sig(lineage_batch_seq=3).signal_id == sig().signal_id  # lineage is set later, not part of identity


# ---------------------------------------------------------------------------------------------------------------
# C1 delivery exposure (synthetic documents and summaries)
# ---------------------------------------------------------------------------------------------------------------


def doc(rid, sent, *lines, kind="document"):
    head = [f"Quarterly update. Period: Jan to Mar 2099. Sent {sent}."] if sent else []
    return c1.SourceText(f"synthetic:{rid}", kind, "Synthetic", "\n".join(head + list(lines)))


def by_type(out, t):
    return [s for s in out.signals if s.signal_type == t]


def test_c1_a_dated_commitment_in_a_formal_document_is_observed_with_its_date():
    out = c1.evaluate([doc("q1", "9 Apr 2099", "Product", "Orion is on track for GA on 15 June 2099.")])
    (c,) = by_type(out, SignalType.COMMITMENT)
    assert c.status == Status.ACTIVE and c.subject == "Orion" and c.event_date == date(2099, 6, 15)
    assert c.recorded_on == date(2099, 4, 9) and Flag.YEAR_INFERRED not in c.flags and not c.assumptions
    assert c.authority == Authority.FORMAL_REPORTED and Flag.REPORTED_NOT_VERIFIED in c.flags
    assert c.evidence[0].record_id == "synthetic:q1" and "15 June 2099" in c.evidence[0].excerpt


def test_c1_a_day_and_month_without_a_year_is_an_explicit_assumption():
    out = c1.evaluate([doc("q4", "9 Nov 2099", "Orion is on track for GA on 15 March.")])
    (c,) = by_type(out, SignalType.COMMITMENT)
    assert c.event_date == date(2100, 3, 15) and Flag.YEAR_INFERRED in c.flags  # first 15 March after 9 Nov 2099
    assert "year taken as" in c.assumptions[0]


def test_c1_a_later_documented_delay_supersedes_the_date_without_predicting_failure():
    out = c1.evaluate([doc("q1", "9 Apr 2099", "Orion is on track for GA on 15 June 2099."),
                       doc("q2", "9 Jul 2099", "Orion is in final testing. We postponed GA after extra testing.")])
    (c,) = by_type(out, SignalType.COMMITMENT)
    (d,) = by_type(out, SignalType.DELIVERY_DELAY)
    (st,) = by_type(out, SignalType.COMMITMENT_STATUS)
    assert Flag.DATE_SUPERSEDED in c.flags and Flag.LINK_LEXICAL in c.flags
    assert d.status == Status.ACTIVE and d.subject == "Orion" and Flag.SUBJECT_FROM_CONTEXT in d.flags
    assert Flag.NEW_DATE_NOT_STATED in d.flags and d.event_date is None
    assert st.value.category == "delayed_new_date_not_stated" and st.recorded_on == date(2099, 7, 9)
    (e,) = by_type(out, SignalType.DELIVERY_EXPOSURE)
    assert e.status == Status.UNQUANTIFIED and not e.value.numeric  # a delay is not a quantified failure


def test_c1_same_date_statements_that_disagree_are_a_conflict_not_a_choice():
    out = c1.evaluate([doc("a", "9 Apr 2099", "Orion is on track for GA on 15 June 2099."),
                       doc("b", "9 Apr 2099", "Orion is late. We delayed GA again.")])
    (st,) = by_type(out, SignalType.COMMITMENT_STATUS)
    assert Flag.STATUS_CONFLICT in st.flags and st.value.category == "conflicting_statements"
    assert st.status == Status.PROVISIONAL


def test_c1_a_commitment_without_a_date_or_with_a_vague_date_stays_unresolved():
    out = c1.evaluate([doc("a", "9 Apr 2099", "We promised customers the Orion release."),
                       doc("b", "9 Apr 2099", "Vega is promised to customers by the holiday season.")])
    cs = {s.subject: s for s in by_type(out, SignalType.COMMITMENT)}
    assert Flag.DATE_MISSING in cs["unresolved commitment"].flags
    assert Flag.SUBJECT_UNRESOLVED in cs["unresolved commitment"].flags
    assert Flag.DATE_IMPRECISE in cs["Vega"].flags and cs["Vega"].event_date is None
    assert all(s.status == Status.PROVISIONAL for s in cs.values())


def test_c1_meeting_summaries_are_non_binding_undated_and_never_borrow_a_subject():
    out = c1.evaluate([doc("t1", None, "Orion is the routing engine.",
                           "Speaker 2 says the board expects the figure by Q3 (unclear which figure).",
                           kind="transcript")])
    (c,) = by_type(out, SignalType.COMMITMENT)
    assert c.authority == Authority.TRANSCRIPT_SUMMARY and c.status == Status.PROVISIONAL
    assert {Flag.NON_BINDING_SOURCE, Flag.RECORDED_DATE_UNKNOWN, Flag.SOURCE_STATES_AMBIGUITY,
            Flag.SUBJECT_UNRESOLVED} <= c.flags and c.subject == "unresolved commitment"


def test_c1_throughput_statements_never_quantify_delivery_exposure():
    out = c1.evaluate([doc("a", "9 Apr 2099", "Orion is due on 15 June 2099. The team ships 20 points a week.")])
    (e,) = by_type(out, SignalType.DELIVERY_EXPOSURE)
    assert e.status == Status.UNQUANTIFIED and not e.value.numeric and "Q7" in e.decisions_pending
    assert Flag.THROUGHPUT_NOT_APPROVED in e.flags and any("Q7" in m for m in e.missing)


def test_c1_never_carries_a_financial_consequence():
    out = c1.evaluate([doc("a", "9 Apr 2099", "Orion is due on 1 March 2099. A Rs. 9 Cr penalty applies if GA "
                                              "is delayed.")])
    assert out.signals and all(s.criterion == Criterion.C1 for s in out.signals)
    assert not any(s.value.numeric or (s.value.unit or "") == "INR" for s in out.signals)


# ---------------------------------------------------------------------------------------------------------------
# C2 financial exposure (D15-04), synthetic items
# ---------------------------------------------------------------------------------------------------------------

A = date(2099, 1, 15)  # assessment date: H = [2099-01-15, 2100-01-15)


def item(iid, low, high, **kw):
    base = dict(item_id=iid, consequence_key=iid, authority=Authority.BINDING_DOCUMENT,
                evidence=(EvidenceRef(f"synthetic:{iid}"),), recurrence=c2.Recurrence.ONE_TIME, amount_low=low,
                amount_high=high, due_on=date(2099, 6, 1))
    return c2.FinancialItem(**(base | kw))


def test_c2_rolling_twelve_month_inclusion_and_exclusion():
    inside, edge_in, edge_out, past = (item("in", 100, 100), item("start", 10, 10, due_on=A),
                                       item("end", 7, 7, due_on=date(2100, 1, 15)),
                                       item("past", 1000, 1000, due_on=date(2098, 12, 31)))
    e = c2.compute([inside, edge_in, edge_out, past], A)
    assert (e.inside_low, e.inside_high) == (110, 110)       # [start, end): the end date is beyond H
    assert (e.beyond_low, e.beyond_high) == (7, 7) and Flag.HORIZON_PARTIAL in e.flags  # reported separately
    assert c2.horizon(A) == (A, date(2100, 1, 15))


def test_c2_one_time_and_recurring_are_separated_and_recurring_counts_only_inside_the_horizon():
    rec = item("rent", 50, 50, recurrence=c2.Recurrence.RECURRING, due_on=None, first_due=date(2098, 12, 15),
               last_due=date(2100, 6, 15), every_months=1)
    e = c2.compute([rec, item("fee", 30, 30)], A)
    assert e.recurring == (600, 600) and e.one_time == (30, 30)  # 12 monthly occurrences inside H
    assert (e.inside_low, e.inside_high) == (630, 630) and (e.beyond_low, e.beyond_high) == (300, 300)


def test_c2_a_recurring_item_without_a_stated_end_is_never_extrapolated():
    rec = item("sub", 50, 50, recurrence=c2.Recurrence.RECURRING, due_on=None, first_due=date(2099, 2, 1))
    s = c2.exposure_signal("synthetic contract", [rec], A)
    assert s.status == Status.UNQUANTIFIED and not s.value.numeric and Flag.UPPER_BOUND_MISSING in s.flags


def test_c2_supported_offsets_reduce_uncertain_offsets_widen_the_lower_bound_unsupported_are_ignored():
    off = lambda oid, lo, hi, sup: c2.Offset(oid, lo, hi, sup, () if sup == c2.OffsetSupport.UNSUPPORTED  # noqa
                                             else (EvidenceRef(f"synthetic:{oid}"),))
    confirmed = c2.compute([item("x", 100, 100, offsets=(off("o1", 20, 30, c2.OffsetSupport.CONFIRMED),))], A)
    assert (confirmed.inside_low, confirmed.inside_high) == (70, 80)
    uncertain = c2.compute([item("x", 100, 100, offsets=(off("o2", 20, 30, c2.OffsetSupport.UNCERTAIN),))], A)
    assert (uncertain.inside_low, uncertain.inside_high) == (70, 100) and Flag.OFFSET_UNCERTAIN in uncertain.flags
    ignored = c2.compute([item("x", 100, 100, offsets=(off("o3", 50, 50, c2.OffsetSupport.UNSUPPORTED),))], A)
    assert (ignored.inside_low, ignored.inside_high) == (100, 100)
    assert Flag.OFFSET_UNSUPPORTED_IGNORED in ignored.flags
    big = c2.compute([item("x", 10, 10, offsets=(off("o4", 50, 50, c2.OffsetSupport.CONFIRMED),))], A)
    assert (big.inside_low, big.inside_high) == (0, 0) and Flag.OFFSET_EXCEEDS_GROSS in big.flags
    with pytest.raises(ValueError, match="cite its record"):
        c2.Offset("o5", 1, 1, c2.OffsetSupport.CONFIRMED)


def test_c2_mutually_exclusive_outcomes_take_the_maximum_not_the_sum():
    e = c2.compute([item("settle", 40, 60, outcome_group="dispute"), item("lose", 90, 120, outcome_group="dispute"),
                    item("other", 5, 5)], A)
    assert (e.inside_low, e.inside_high) == (95, 125) and Flag.MUTUALLY_EXCLUSIVE in e.flags


def test_c2_the_same_consequence_is_never_counted_twice():
    one = c2.compute([item("inv", 100, 100), item("inv-copy", 100, 100, consequence_key="inv")], A)
    assert (one.inside_low, one.inside_high) == (100, 100) and Flag.DUPLICATE_CONSEQUENCE in one.flags
    differ = c2.compute([item("inv", 100, 100), item("inv-copy", 90, 110, consequence_key="inv")], A)
    assert (differ.inside_low, differ.inside_high) == (90, 110) and Flag.DUPLICATE_AMOUNTS_DIFFER in differ.flags
    with pytest.raises(ValueError, match="disagree on timing"):
        c2.compute([item("inv", 100, 100), item("inv-copy", 100, 100, consequence_key="inv",
                                                due_on=date(2099, 7, 1))], A)


def test_c2_a_missing_upper_bound_or_unknown_timing_is_unquantified_never_zero():
    for items in ([item("open", 100, None)], [item("when", 100, 100, due_on=None)]):
        s = c2.exposure_signal("synthetic object", items, A)
        assert s.status == Status.UNQUANTIFIED and not s.value.numeric and s.missing


@pytest.mark.parametrize("authority", [Authority.TRANSCRIPT_SUMMARY, Authority.FORMAL_REPORTED, Authority.NONE,
                                       Authority.CONFIGURATION])
def test_c2_only_system_records_and_binding_documents_are_accepted(authority):
    with pytest.raises(ValueError, match="system records and binding documents only"):
        item("x", 1, 1, authority=authority)


def test_c2_output_is_raw_inr_with_provenance_and_never_normalised():
    s = c2.exposure_signal("synthetic contract", [item("a", 1_500_000, 2_000_000)], A)
    assert s.status == Status.ACTIVE and s.value.unit == "INR" and (s.value.low, s.value.high) == (1.5e6, 2e6)
    assert s.window_start == A and s.window_end == date(2100, 1, 15)
    steps = dict(s.calculation)
    assert "rolling 12 months" in steps["horizon"] and "beyond_H_reported_separately" in steps
    assert s.measure == "C2.net_exposure_12m@1" and s.evidence[0].record_id == "synthetic:a"


# ---------------------------------------------------------------------------------------------------------------
# C3 continuity evidence (synthetic rota, qualification and ownership records)
# ---------------------------------------------------------------------------------------------------------------

W = (date(2099, 3, 1), date(2099, 4, 1))


def rota(resp, cov=4.0, version=1, start=W[0], end=W[1], service="svc-a", **kw):
    return c3.RotaEntry(service, resp, start, end, cov, EvidenceRef(f"synthetic:rota-v{version}", resp), version,
                        **kw)


def qual(resp, q=c3.Qualification.QUALIFIED, cur=c3.Currency.CURRENT, scope="svc-a"):
    return c3.QualificationRecord(resp, scope, q, cur, EvidenceRef(f"synthetic:qual-{resp}"))


def absence(**kw):
    base = dict(record=EvidenceRef("synthetic:absence"), current=True, applies_to_scope=True, covers_window=True,
                authoritative=True)
    return c3.AbsenceEvidence(**(base | kw))


def test_c3_combined_status_qualified_unknown_no_record_and_currency():
    quals = [qual("r1"), qual("r2", c3.Qualification.UNKNOWN), qual("r4", cur=c3.Currency.UNKNOWN),
             qual("r5", cur=c3.Currency.INVALIDATED)]
    assert c3.responder_status("r1", "svc-a", quals)[0] == "qualified"
    assert c3.responder_status("r2", "svc-a", quals) == ("unknown", {Flag.QUALIFICATION_INCOMPLETE})
    assert c3.responder_status("r3", "svc-a", quals) == ("no_record", {Flag.QUALIFICATION_MISSING})
    status, flags = c3.responder_status("r4", "svc-a", quals)     # currency unknown: neither current nor invalid
    assert status == "unknown" and Flag.CURRENCY_UNKNOWN in flags
    assert c3.responder_status("r5", "svc-a", quals) == ("disqualified", {Flag.RECORD_INVALIDATED})
    assert c3.responder_status("r1", "svc-b", quals)[0] == "no_record"   # scope: another service does not count


def test_c3_rota_periods_scope_and_supersession():
    entries = [rota("r1", version=1), rota("r2", version=2), rota("r9", start=date(2099, 5, 1), end=date(2099, 6, 1)),
               rota("r8", service="svc-b")]
    effective, superseded = c3.effective_rota([e for e in entries if e.service == "svc-a"], W)
    assert [e.responder for e in effective] == ["r2"] and [e.responder for e in superseded] == ["r1"]
    (ev, measure) = c3.service_signals("svc-a", W, entries, [qual("r2")])
    assert Flag.RECORD_SUPERSEDED in ev.flags and ev.value.category == "evidence_resolved"
    assert ev.observed == ("1 rostered responder(s) with coverage whose combined status is qualified",)


def test_c3_unresolved_qualifications_are_reported_never_scored_and_no_guardrail_is_emitted():
    ev, measure = c3.service_signals("svc-a", W, [rota("r1"), rota("r2")], [qual("r1", c3.Qualification.UNKNOWN)])
    assert ev.status == Status.PROVISIONAL and ev.value.category == "qualification_unresolved"
    assert {Flag.QUALIFICATION_MISSING, Flag.QUALIFICATION_INCOMPLETE, Flag.GUARDRAIL_NOT_ACTIVE,
            Flag.CRITICALITY_NOT_DESIGNATED} <= ev.flags
    assert measure.status == Status.BLOCKED and not measure.value.numeric
    assert set(measure.decisions_pending) == {"Q3", "Q9", "Q10"}


def test_c3_contradictory_absence_claims_are_invalid_input():
    for responders in ([qual("r1")], [qual("r1", c3.Qualification.UNKNOWN)], []):  # qualified, unknown, no record
        with pytest.raises(c3.InvalidC3Input, match="contradicts"):
            c3.service_signals("svc-a", W, [rota("r1")], responders, absence=absence())
    with pytest.raises(c3.InvalidC3Input, match="contradicts"):
        c3.ownership_signals("sys-a", [c3.OwnershipRecord("sys-a", "o1", c3.Currency.UNKNOWN,
                                                          EvidenceRef("synthetic:own"))], absence=absence())


def test_c3_absence_needs_sufficient_evidence_and_is_never_a_guardrail():
    disq = [qual("r1", c3.Qualification.DISQUALIFIED)]
    ev, _ = c3.service_signals("svc-a", W, [rota("r1")], disq, absence=absence())
    assert Flag.CONFIRMED_ABSENCE in ev.flags and ev.value.category == "confirmed_no_qualified_coverage"
    assert ev.status == Status.PROVISIONAL and Flag.GUARDRAIL_NOT_ACTIVE in ev.flags
    for weak in ({"current": False}, {"applies_to_scope": False}, {"covers_window": False},
                 {"authoritative": False}):
        ev, _ = c3.service_signals("svc-a", W, [rota("r1")], disq, absence=absence(**weak))
        assert Flag.ABSENCE_EVIDENCE_INSUFFICIENT in ev.flags and Flag.CONFIRMED_ABSENCE not in ev.flags


def test_c3_malformed_absence_evidence_and_rota_entries_are_rejected():
    with pytest.raises(c3.InvalidC3Input):
        absence(record=EvidenceRef(" "))
    with pytest.raises(c3.InvalidC3Input):
        absence(current="yes")
    for bad in (dict(start=W[1], end=W[0]), dict(cov=-1.0), dict(cov=float("nan")), dict(version=0)):
        with pytest.raises(c3.InvalidC3Input):
            rota("r1", **bad)


def test_c3_never_uses_leave_or_protected_absence_data():
    for kind in ("leave", "Annual leave export", "sickness-log"):
        with pytest.raises(c3.ProtectedSourceError):
            rota("r1", source_kind=kind)
        with pytest.raises(c3.ProtectedSourceError):
            c3.QualificationRecord("r1", "svc-a", c3.Qualification.QUALIFIED, c3.Currency.CURRENT,
                                   EvidenceRef("synthetic:q"), source_kind=kind)


def test_c3_missing_ownership_evidence_is_not_proof_that_nothing_is_owned():
    (s,) = c3.ownership_signals("sys-a", [])
    assert s.status == Status.MISSING and Flag.OWNERSHIP_MISSING in s.flags
    assert "does not show the system is unowned" in s.missing[0] and not s.value.numeric
    (u,) = c3.ownership_signals("sys-a", [c3.OwnershipRecord("sys-a", "o1", c3.Currency.UNKNOWN,
                                                             EvidenceRef("synthetic:own"))])
    assert u.value.category == "owner_currency_unknown" and Flag.CURRENCY_UNKNOWN in u.flags


# ---------------------------------------------------------------------------------------------------------------
# C4 integrity of figures reported upward (synthetic figures)
# ---------------------------------------------------------------------------------------------------------------


def rep(rid, label, text, period="Jan to Mar 2099"):
    return c4.Reported(f"synthetic:{rid}", label, text, period, None)


def test_c4_parsing_keeps_conservative_rounding_intervals_and_refuses_uncertain_units():
    assert c4.parse("Rs. 7.3 Cr") == c4.Parsed(Decimal("7.25") * 10**7, Decimal("7.35") * 10**7, "INR")
    assert c4.parse("Rs. 82 L").unit == "INR" and c4.parse("27 months").unit == "months"
    assert c4.parse("37") == c4.Parsed(Decimal("36.5"), Decimal("37.5"), "count (unit not stated)")
    for bad in ("about 40", "Rs. 4 bn", "9 approx", ""):
        assert c4.parse(bad) is None


def test_c4_matching_or_rounding_explained_values_emit_no_signal():
    out = c4.compare_reported([rep("a", "ARR", "Rs. 7.3 Cr"), rep("b", "ARR", "Rs. 7.34 Cr")])
    assert out.signals == [] and "rounding" in out.withheld[0].reason


def test_c4_differing_values_preserve_both_sources_without_saying_which_is_true():
    out = c4.compare_reported([rep("a", "ARR", "Rs. 7.3 Cr"), rep("b", "ARR", "Rs. 8.9 Cr")])
    (s,) = out.signals
    assert s.signal_type == SignalType.FIGURE_DISCREPANCY and s.status == Status.PROVISIONAL
    assert not s.value.numeric and s.value.category == "values_differ_beyond_rounding"   # no C4 score
    assert {Flag.METRIC_IDENTITY_LABEL_ONLY, Flag.MATERIALITY_NOT_ASSIGNED} <= s.flags
    assert [e.record_id for e in s.evidence] == ["synthetic:a", "synthetic:b"]
    assert "does not say which source is correct" in s.missing[0]


@pytest.mark.parametrize("a,b,flag", [
    (("Rs. 82 L", "Jan to Mar 2099"), ("82", "Jan to Mar 2099"), Flag.UNIT_MISMATCH),
    (("37", "Jan to Mar 2099"), ("41", "Apr to Jun 2099"), Flag.PERIOD_MISMATCH),
    (("37", None), ("41", None), Flag.PERIOD_MISMATCH),
    (("about 40", "Jan to Mar 2099"), ("41", "Jan to Mar 2099"), Flag.VALUE_FORMAT_UNPARSED),
])
def test_c4_incomparable_figures_are_preserved_as_not_comparable(a, b, flag):
    out = c4.compare_reported([rep("a", "M", a[0], a[1]), rep("b", "M", b[0], b[1])])
    (s,) = out.signals
    assert s.status == Status.UNQUANTIFIED and flag in s.flags and s.missing[0].startswith("not comparable")


def test_c4_a_figure_without_an_approved_mapping_is_unmapped_and_never_compared_to_a_system_metric():
    rows = [rep("a", "Team", "7"), rep("b", "Team", "7", "Apr to Jun 2099")]
    (s,) = c4.unmapped_signals(rows, [], [])
    assert s.status == Status.UNMAPPED and s.signal_type == SignalType.FIGURE_UNMAPPED and not s.value.numeric
    assert Flag.METRIC_IDENTITY_UNDEFINED in s.flags and len(s.evidence) == 2
    (p,) = c4.unmapped_signals(rows, [], [{"reported_figure": "Team", "status": "proposed"}])
    assert Flag.MAPPING_PROPOSED_NOT_APPROVED in p.flags and p.status == Status.UNMAPPED


def test_c4_proposed_mappings_stay_inactive_and_approved_ones_are_versioned(tmp_path):
    f = tmp_path / "m.toml"
    f.write_text('[[mapping]]\nreported_figure = "Team"\nmetric = "headcount"\nunit = "people"\nstatus = "proposed"\n'
                 '[[mapping]]\nreported_figure = "Headcount"\nmetric = "headcount"\nunit = "people"\n'
                 'approved_by = "Owner"\napproved_on = 2099-01-01\ndecision_record = "synthetic"\nversion = 2\n',
                 encoding="utf-8")
    active = approved.load_mappings(f)
    assert [(m.reported_figure, m.version) for m in active] == [("Headcount", 2)]
    assert [p["reported_figure"] for p in approved.load_proposed(f)] == ["Team"]
    f.write_text('[[mapping]]\nreported_figure = "Team"\nmetric = "headcount"\nunit = "people"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="owner approval is mandatory"):
        approved.load_mappings(f)        # neither proposed nor approved: refused, never silently active
    f.write_text('[[mapping]]\nreported_figure = "T"\nmetric = "m"\nunit = "u"\nstatus = "maybe"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="status"):
        approved.load_mappings(f)
