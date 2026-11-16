"""Temporal model (ARCHITECTURE.md §8): read basis, query modes, predicate builder and leakage canaries.
SYNTHETIC data only."""

import uuid
from datetime import date, datetime, timedelta

import pytest

from veloxa.temporal import Mode, end_of_day, predicate, resolve_basis

IST = "Asia/Kolkata"
T = uuid.uuid4()
D1 = datetime.fromisoformat("2026-11-16T10:00:00+05:30")
D2 = datetime.fromisoformat("2026-11-17T10:00:00+05:30")
BATCHES = [(1, D1), (2, D2)]
NOW = datetime.fromisoformat("2026-11-17T12:00:00+05:30")


def test_date_only_means_the_end_of_that_day_in_the_tenant_time_zone():
    assert end_of_day(date(2026, 11, 16), IST) == datetime.fromisoformat("2026-11-17T00:00:00+05:30")


def test_current_uses_the_latest_released_sealed_batch_and_clamps_to_the_clock():
    assert resolve_basis(T, BATCHES, Mode.CURRENT, None, IST, NOW).batch_seq == 2
    on_day_one = datetime.fromisoformat("2026-11-16T12:00:00+05:30")
    assert resolve_basis(T, BATCHES, Mode.CURRENT, None, IST, on_day_one).batch_seq == 1  # D2 not yet released


@pytest.mark.parametrize("as_of, k", [
    (date(2026, 11, 15), 0),
    (date(2026, 11, 16), 1),
    (date(2026, 11, 17), 2),
    (datetime.fromisoformat("2026-11-16T09:59:59+05:30"), 0),
    (datetime.fromisoformat("2026-11-16T10:00:00+05:30"), 1),
    (datetime.fromisoformat("2026-11-17T09:59:59+05:30"), 1),
])
def test_known_at_maps_dates_and_instants_to_release_times(as_of, k):
    b = resolve_basis(T, BATCHES, Mode.KNOWN_AT, as_of, IST, NOW)
    assert b.batch_seq == k
    assert b.nothing_admitted == (k == 0)
    if k:
        assert b.recorded_cutoff == b.valid_at == dict(BATCHES)[k]


def test_known_at_before_any_admission_says_so_and_offers_recorded_by():
    b = resolve_basis(T, BATCHES, Mode.KNOWN_AT, date(2026, 9, 1), IST, NOW)
    assert b.nothing_admitted and b.batch_seq == 0
    assert "RECORDED_BY" in b.notes[0]
    assert "Nothing had been admitted" in b.banner()


def test_recorded_by_uses_current_knowledge_and_an_end_of_day_cutoff():
    b = resolve_basis(T, BATCHES, Mode.RECORDED_BY, date(2026, 9, 1), IST, NOW)
    assert b.batch_seq == 2 and b.recorded_cutoff == end_of_day(date(2026, 9, 1), IST) and b.valid_at is None


def test_valid_at_uses_current_knowledge_and_the_last_instant_of_the_day():
    b = resolve_basis(T, BATCHES, Mode.VALID_AT, date(2026, 9, 1), IST, NOW)
    assert b.batch_seq == 2 and b.recorded_cutoff is None
    assert b.valid_at == end_of_day(date(2026, 9, 1), IST) - timedelta(microseconds=1)


@pytest.mark.parametrize("mode, as_of", [
    (Mode.CURRENT, date(2026, 11, 16)),
    (Mode.KNOWN_AT, None),
    (Mode.KNOWN_AT, date(2026, 11, 18)),       # in the future relative to the clock
    (Mode.KNOWN_AT, datetime(2026, 11, 16, 10)),  # instant without an offset
])
def test_invalid_requests_are_rejected(mode, as_of):
    with pytest.raises(ValueError):
        resolve_basis(T, BATCHES, mode, as_of, IST, NOW)


def test_predicates_use_identifiers_and_bound_parameters_only():
    b = resolve_basis(T, BATCHES, Mode.VALID_AT, date(2026, 9, 1), IST, NOW)
    p = predicate(b, "f", recorded_column="recorded", valid_column="valid")
    text = p.clause.as_string(None)
    assert text == '"f"."batch_seq" <= %(k)s AND "f"."valid" @> %(v)s::timestamptz'
    assert p.params == {"k": 2, "v": b.valid_at}
    r = predicate(resolve_basis(T, BATCHES, Mode.RECORDED_BY, date(2026, 9, 1), IST, NOW), "f",
                  recorded_column="recorded", valid_column="valid")
    assert r.clause.as_string(None) == '"f"."batch_seq" <= %(k)s AND upper("f"."recorded") <= %(r)s'


@pytest.mark.parametrize("mode", [Mode.RECORDED_BY, Mode.VALID_AT])
def test_a_table_without_the_required_time_column_is_refused_not_left_unfiltered(mode):
    b = resolve_basis(T, BATCHES, mode, date(2026, 9, 1), IST, NOW)
    with pytest.raises(ValueError):
        predicate(b, "r")


# ---------------------------------------------------------------------------------------------------------------
# Leakage canaries across a real batch boundary (database)
# ---------------------------------------------------------------------------------------------------------------

D1_CANARIES = {"email:data_room/email/01.eml#document", "data_room:data_room/README.txt#document"}
D2_CANARIES = {"email:data_room/email/91.eml#document", "meeting_summaries:transcripts/T9_Synthetic.docx#document"}


def _make_synthetic_d2(root):
    from synthetic_pack import _docx

    pack = root / "Day2_Tue_17Nov"
    (pack / "data_room" / "email").mkdir(parents=True)
    (pack / "data_room" / "email" / "91.eml").write_text(
        "From: Person Beta <beta@example.test>\nTo: Person Alpha <alpha@example.test>\nSubject: later\n"
        "Date: Tue, 06 Jan 2099 10:00:00 +0530\nMessage-ID: <s-91@example.test>\n"
        "Content-Type: text/plain; charset=\"utf-8\"\n\nCANARY batch two.\n", encoding="utf-8")
    _docx(pack / "transcripts" / "T9_Synthetic.docx", ["T9 synthetic", "CANARY batch two."])


@pytest.fixture
def two_batches(ingest_conn, tenant, tmp_path):
    """A tenant with two sealed batches under a schedule whose releases are already in the past."""
    from synthetic_pack import make_d1

    from veloxa.ingest.pipeline import admit_pack
    from veloxa.releases import Pack, Schedule, current_time

    d1_at = datetime.fromisoformat("2026-11-16T08:00:00+05:30")
    d2_at = datetime.fromisoformat("2026-11-16T09:00:00+05:30")
    schedule = Schedule("canary", IST, (Pack("D1", 1, "Day1_Mon_16Nov", d1_at), Pack("D2", 2, "Day2_Tue_17Nov", d2_at)))
    make_d1(tmp_path)
    _make_synthetic_d2(tmp_path)
    now = current_time()
    for pack_id in ("D1", "D2"):
        assert admit_pack(ingest_conn, tenant, schedule, pack_id, tmp_path, now).status == "admitted"
    return d1_at, d2_at


def _ledger_view_surface(conn, basis):
    from veloxa.db import tenant_tx

    with tenant_tx(conn, basis.tenant_id, batch_seq=basis.batch_seq):
        return {r["record_id"] for r in conn.execute("SELECT record_id FROM ledger.visible_source_record")}


def _leaky_surface(conn, basis):
    """Deliberately wrong: ignores K. The harness must catch it."""
    from veloxa.db import tenant_tx

    with tenant_tx(conn, basis.tenant_id):
        return {r["record_id"] for r in conn.execute("SELECT record_id FROM ledger.source_record")}


@pytest.mark.db
def test_no_surface_leaks_later_material_across_the_batch_boundary(app_conn, tenant, two_batches):
    import canary

    from veloxa import ledger
    from veloxa.releases import current_time

    d1_at, d2_at = two_batches
    releases = ledger.sealed_releases(app_conn, tenant)
    now = current_time()
    bases = {
        0: resolve_basis(tenant, releases, Mode.KNOWN_AT, d1_at - timedelta(seconds=1), IST, now),
        1: resolve_basis(tenant, releases, Mode.KNOWN_AT, d2_at - timedelta(seconds=1), IST, now),
        2: resolve_basis(tenant, releases, Mode.CURRENT, None, IST, now),
    }
    assert [bases[k].batch_seq for k in (0, 1, 2)] == [0, 1, 2]
    report = canary.run({"ledger.visible_source_record": _ledger_view_surface}, app_conn, bases,
                        {1: D1_CANARIES, 2: D2_CANARIES})
    assert report.clean, report


@pytest.mark.db
def test_the_canary_harness_detects_a_leaky_surface(app_conn, tenant, two_batches):
    import canary

    from veloxa import ledger
    from veloxa.releases import current_time

    d1_at, d2_at = two_batches
    releases = ledger.sealed_releases(app_conn, tenant)
    basis_k1 = resolve_basis(tenant, releases, Mode.KNOWN_AT, d2_at - timedelta(seconds=1), IST, current_time())
    report = canary.run({"leaky": _leaky_surface}, app_conn, {1: basis_k1}, {1: D1_CANARIES, 2: D2_CANARIES})
    assert report.leaks == [("leaky", 1, D2_CANARIES)]
