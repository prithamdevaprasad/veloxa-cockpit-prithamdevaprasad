"""Time axes of detector output (ARCHITECTURE.md §8.1-§8.4): K, recorded time R, valid time V, extraction time E,
corrections (T8) and reproducibility, under all four read modes. SYNTHETIC packs only: every name, date and
figure is invented, and nothing here is a real company finding."""

import json
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest

from veloxa import ledger, projector
from veloxa.db import connect, tenant_tx
from veloxa.signals import registry, timebasis
from veloxa.signals.contract import Criterion, SignalType
from veloxa.temporal import Mode, ReadBasis, resolve_basis

IST = "Asia/Kolkata"
D1_AT = datetime.fromisoformat("2026-11-16T08:00:00+05:30")
D2_AT = datetime.fromisoformat("2026-11-16T09:00:00+05:30")
Q1 = "documents:data_room/documents/Plan_Q1.pdf#document"
Q2 = "documents:data_room/documents/Plan_Q2.pdf#document"
E = datetime(2026, 11, 16, 4, 30, tzinfo=UTC)


# ---------------------------------------------------------------------------------------------------------------
# Pure: what R and V a fact may claim, and when it may be used
# ---------------------------------------------------------------------------------------------------------------


def test_stated_periods_are_parsed_exactly_or_left_unknown():
    assert timebasis.stated_period("Apr to Jun 2026") == (date(2026, 4, 1), date(2026, 7, 1))
    assert timebasis.stated_period("Oct to Dec 2026") == (date(2026, 10, 1), date(2027, 1, 1))
    for text in (None, "", "Q2 FY27", "Nov to Feb 2026", "around mid-year"):
        assert timebasis.stated_period(text) is None   # never guessed


def test_recorded_time_comes_from_the_source_date_or_is_bounded_by_extraction():
    ft = timebasis.statement_time(date(2026, 4, 9), E, "UTC")
    assert ft.recorded_basis == timebasis.RecordedBasis.SOURCE_DATE and ft.recorded_day == date(2026, 4, 9)
    assert ft.recorded_hi == datetime(2026, 4, 10, tzinfo=UTC)                     # T6: the whole day
    assert ft.valid_basis == timebasis.ValidBasis.STATEMENT_DATE and ft.valid_from == date(2026, 4, 9)
    undated = timebasis.statement_time(None, E, "UTC")
    assert undated.recorded_hi == E and undated.recorded_basis == timebasis.RecordedBasis.EXTRACTION_UPPER_BOUND
    assert undated.valid_basis == timebasis.ValidBasis.UNKNOWN                     # T3: never assumed valid
    future = timebasis.statement_time(date(2099, 1, 1), E, "UTC")                 # R <= E violated: not trusted
    assert future.recorded_day is None and future.recorded_hi == E and "R <= E" in future.note


def _basis(mode, cutoff=None, valid=None):
    return ReadBasis(None, 2, mode, cutoff, valid, "x")


def test_mode_rules_for_one_fact():
    ft = timebasis.figure_time(date(2026, 7, 9), "Apr to Jun 2026", E, "UTC")
    at = lambda y, m, d: datetime(y, m, d, 12, tzinfo=UTC)  # noqa: E731
    assert timebasis.excluded(ft, _basis(Mode.CURRENT, at(2026, 11, 17), at(2026, 11, 17)), "UTC") is None
    assert timebasis.excluded(ft, _basis(Mode.RECORDED_BY, at(2026, 7, 9)), "UTC")       # recorded at day end
    assert timebasis.excluded(ft, _basis(Mode.RECORDED_BY, at(2026, 7, 10)), "UTC") is None
    assert timebasis.excluded(ft, _basis(Mode.VALID_AT, None, at(2026, 5, 1)), "UTC") is None
    assert "does not hold" in timebasis.excluded(ft, _basis(Mode.VALID_AT, None, at(2026, 7, 1)), "UTC")
    unknown = timebasis.figure_time(date(2026, 7, 9), "half one", E, "UTC")
    assert "unknown" in timebasis.excluded(unknown, _basis(Mode.VALID_AT, None, at(2026, 5, 1)), "UTC")


# ---------------------------------------------------------------------------------------------------------------
# Database: a correction, a later delay, and an undated meeting summary
# ---------------------------------------------------------------------------------------------------------------


def _plan(pack, name, period, sent, customers, product_line):
    from synthetic_pack import make_document

    make_document(pack, name, ["Synthetic company: Board update", f"Quarterly update. Period: {period}. Sent {sent}.",
                               "Key numbers", "Metric Value", f"Customers {customers}", "Product", product_line])


@pytest.fixture
def timed(db, ingest_conn, tenant, tmp_path):
    from synthetic_pack import make_d1, make_transcript

    from veloxa.ingest.pipeline import admit_pack
    from veloxa.releases import Pack, Schedule, current_time

    sched = Schedule("time", IST, (Pack("D1", 1, "Day1_Mon_16Nov", D1_AT), Pack("D2", 2, "Day2_Tue_17Nov", D2_AT)))
    d1 = make_d1(tmp_path)
    _plan(d1, "Plan_Q1.pdf", "Jan to Mar 2026", "9 Apr 2026", 10, "Orion is on track for GA on 15 June 2026.")
    make_transcript(d1, "T9_Synthetic.docx", ["T9 synthetic call",
                                              "The board expects the figure by Q3 (unclear which figure)."])
    d2 = tmp_path / "Day2_Tue_17Nov"
    _plan(d2, "Plan_Q1.pdf", "Jan to Mar 2026", "9 Apr 2026", 11,     # a CORRECTION of the same document
          "Orion is on track for GA on 30 June 2026.")
    _plan(d2, "Plan_Q2.pdf", "Apr to Jun 2026", "9 Jul 2026", 12, "Orion is in final testing. We postponed GA "
          "after extra testing.")
    for p in ("D1", "D2"):
        admit_pack(ingest_conn, tenant, sched, p, tmp_path, current_time())
    with connect(db.dsn("projection")) as prj:
        projector.build(prj, tenant)
    return tenant


def basis_at(conn, tenant, mode=Mode.CURRENT, at=None):
    from veloxa.releases import current_time

    return resolve_basis(tenant, ledger.sealed_releases(conn, tenant), mode, at, IST, current_time())


def run(conn, basis):
    return registry.run(conn, basis, registry.context_for(basis, "UTC", [], []))


def k1(conn, tenant):
    return basis_at(conn, tenant, Mode.KNOWN_AT, D2_AT - timedelta(seconds=1))


def of(result, criterion, stype):
    return [s for s in result.signals if s.criterion == criterion and s.signal_type == stype]


def commitment(result):
    (c,) = [s for s in of(result, Criterion.C1, SignalType.COMMITMENT) if s.subject == "Orion"]
    return c


def test_a_correction_replaces_the_record_from_its_batch_and_history_is_reproducible(app_conn, ingest_conn, timed):
    with tenant_tx(ingest_conn, timed):
        versions = ingest_conn.execute("SELECT version, supersedes, batch_seq FROM ledger.source_record"
                                       " WHERE record_id = %s ORDER BY version", (Q1,)).fetchall()
    assert [(v["version"], v["supersedes"], v["batch_seq"]) for v in versions] == [(1, None, 1), (2, 1, 2)]
    first = run(app_conn, k1(app_conn, timed))
    c = commitment(first)
    assert c.event_date == date(2026, 6, 15) and c.evidence[0].version == 1 and c.lineage_batch_seq == 1
    now = run(app_conn, basis_at(app_conn, timed))
    c2 = commitment(now)
    assert c2.event_date == date(2026, 6, 30) and c2.evidence[0].version == 2 and c2.lineage_batch_seq == 2
    unmapped = {s.subject: s for s in of(now, Criterion.C4, SignalType.FIGURE_UNMAPPED)}
    assert {(e.record_id, e.excerpt) for e in unmapped["Customers"].evidence} == {(Q1, "11"), (Q2, "12")}
    again = run(app_conn, k1(app_conn, timed))   # the earlier knowledge point is reproduced exactly
    assert [s.signal_id for s in again.signals] == [s.signal_id for s in first.signals]
    assert again.output_sha256 == first.output_sha256


def test_recorded_by_uses_only_facts_recorded_by_the_cutoff(app_conn, timed):
    early = run(app_conn, basis_at(app_conn, timed, Mode.RECORDED_BY, date(2026, 5, 1)))
    assert early.basis.batch_seq == 2                                 # K = current; R limits the facts
    assert commitment(early).recorded_on == date(2026, 4, 9)
    assert not of(early, Criterion.C1, SignalType.DELIVERY_DELAY)     # recorded 9 Jul: after the cutoff
    assert all(Q2 not in s.record_ids for s in early.signals)
    assert not any(s.subject == "unresolved commitment" for s in early.signals)   # undated: bounded by E only
    assert any("bounded only by its extraction" in w.reason for w in early.withheld)
    assert all(s.recorded_upper is None or s.recorded_upper <= early.basis.recorded_cutoff.isoformat()
               for s in early.signals)
    late = run(app_conn, basis_at(app_conn, timed, Mode.RECORDED_BY, date(2026, 8, 1)))
    (d,) = of(late, Criterion.C1, SignalType.DELIVERY_DELAY)
    assert d.recorded_on == date(2026, 7, 9) and Q2 in d.record_ids


def test_valid_at_uses_only_facts_that_hold_at_the_instant(app_conn, timed):
    may = run(app_conn, basis_at(app_conn, timed, Mode.VALID_AT, date(2026, 5, 1)))
    assert commitment(may).valid_from == date(2026, 4, 9)
    assert not of(may, Criterion.C1, SignalType.DELIVERY_DELAY)       # stated 9 Jul: not yet holding on 1 May
    assert any("valid time is unknown" in w.reason for w in may.withheld)      # the undated summary
    (u,) = [s for s in of(may, Criterion.C4, SignalType.FIGURE_UNMAPPED) if s.subject == "Customers"]
    assert u.record_ids == (Q2,)          # Apr-Jun holds on 1 May (best current knowledge); Jan-Mar does not
    assert u.evidence[0].valid_from == "2026-04-01" and u.evidence[0].valid_to == "2026-07-01"
    aug = run(app_conn, basis_at(app_conn, timed, Mode.VALID_AT, date(2026, 8, 1)))
    (st,) = of(aug, Criterion.C1, SignalType.COMMITMENT_STATUS)
    assert st.value.category == "delayed_new_date_not_stated" and st.valid_from == date(2026, 7, 9)


def test_recorded_signals_keep_versions_and_every_time_axis(app_conn, timed):
    now = datetime.fromisoformat("2026-11-17T12:00:00+05:30")
    basis = basis_at(app_conn, timed, Mode.RECORDED_BY, date(2026, 8, 1))
    ref = registry.record(app_conn, run(app_conn, basis), now)
    with tenant_tx(app_conn, timed):
        r = app_conn.execute("SELECT mode, recorded_cutoff, valid_at, time_zone, detected_at FROM runtime.detector_run"
                             " WHERE run_id = %s", (ref["run_id"],)).fetchone()
        rows = app_conn.execute("SELECT criterion, recorded_upper, valid_from, payload FROM runtime.risk_signal"
                                " WHERE run_id = %s", (ref["run_id"],)).fetchall()
    assert r["mode"] == "RECORDED_BY" and r["recorded_cutoff"] == basis.recorded_cutoff and r["valid_at"] is None
    assert r["time_zone"] == "UTC" and r["detected_at"] == now
    c1 = [x for x in rows if x["criterion"] == "C1"]
    assert c1 and all(x["recorded_upper"] is not None and x["recorded_upper"] <= basis.recorded_cutoff for x in c1)
    ev = [e for x in rows for e in x["payload"]["evidence"]]
    assert ev and all(e["version"] and e["batch_seq"] and e["extracted_at"] and e["recorded_basis"] for e in ev)
    assert {e["recorded_basis"] for e in ev} == {"source_date"}


def test_different_read_bases_are_separate_reproducible_runs(app_conn, timed):
    now = datetime.fromisoformat("2026-11-17T12:00:00+05:30")
    bases = [k1(app_conn, timed), basis_at(app_conn, timed),
             basis_at(app_conn, timed, Mode.RECORDED_BY, date(2026, 8, 1)),
             basis_at(app_conn, timed, Mode.VALID_AT, date(2026, 8, 1))]
    ids = [registry.record(app_conn, run(app_conn, b), now)["run_id"] for b in bases]
    assert len(set(ids)) == 4
    assert [registry.record(app_conn, run(app_conn, b), now + timedelta(hours=1))["run_id"] for b in bases] == ids


def _probe(conn, tenant, run_id, payload, recorded_upper=None):
    with tenant_tx(conn, tenant, batch_seq=2):
        conn.execute("INSERT INTO runtime.risk_signal (tenant_id, run_id, signal_id, detector, criterion, signal_type,"
                     " status, lineage_batch_seq, evidence, payload, recorded_upper) VALUES"
                     " (%s, %s, %s, 'probe@1', 'C1', 'commitment', 'active', 1, %s, %s, %s)",
                     (tenant, run_id, "S-" + "b" * 16, [Q1], json.dumps(payload), recorded_upper))


def test_the_database_rejects_signals_outside_their_runs_time_basis(app_conn, timed):
    now = datetime.fromisoformat("2026-11-17T12:00:00+05:30")
    rb = basis_at(app_conn, timed, Mode.RECORDED_BY, date(2026, 5, 1))
    rb_run = registry.record(app_conn, run(app_conn, rb), now)["run_id"]
    with pytest.raises(psycopg.errors.CheckViolation, match="after the RECORDED_BY cutoff"):
        _probe(app_conn, timed, rb_run, {"value": {}, "evidence": []}, rb.recorded_cutoff + timedelta(days=1))
    va = basis_at(app_conn, timed, Mode.VALID_AT, date(2026, 5, 1))
    va_run = registry.record(app_conn, run(app_conn, va), now)["run_id"]
    for e in ({"record_id": Q1, "valid_basis": "unknown"},
              {"record_id": Q1, "valid_basis": "stated_period", "valid_from": "2026-01-01", "valid_to": "2026-04-01"}):
        with pytest.raises(psycopg.errors.CheckViolation, match="valid time is unknown or does not hold"):
            _probe(app_conn, timed, va_run, {"value": {}, "evidence": [e]})
