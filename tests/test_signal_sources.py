"""C2 financial-obligation and C3 continuity source contracts (src/veloxa/signals/adapters.py). SYNTHETIC
validation only: every obligation, service, responder and amount is invented; nothing here is a real company
finding, and no production catalogue rule exists for these sources."""

import json
from dataclasses import replace
from datetime import date, datetime

import pytest

from veloxa import ledger, projector
from veloxa.catalogue import load_catalogue
from veloxa.db import connect
from veloxa.signals import adapters, registry
from veloxa.signals.contract import Authority, Criterion, Flag, SignalType, Status
from veloxa.temporal import Mode, resolve_basis

OBL = {"subject": "contract:synthetic-1", "item_id": "o1", "consequence_key": "k1", "instrument": "contract",
       "recurrence": "one_time", "amount_low": 300, "amount_high": 300, "due_on": "2027-01-10",
       "recorded_on": "2026-09-01"}


def test_financial_rows_need_a_binding_instrument_or_system_record():
    subject, item = adapters.financial_item("synthetic:r1", OBL)
    assert subject == "contract:synthetic-1" and item.authority == Authority.BINDING_DOCUMENT
    assert adapters.financial_item("synthetic:r1", {**OBL, "instrument": "system_record"})[1].authority == \
        Authority.SYSTEM_RECORD
    for bad in ({**OBL, "instrument": "meeting_summary"}, {**OBL, "instrument": "email"}, {**OBL, "subject": ""},
                {**OBL, "amount_low": 1.5}, {**OBL, "amount_low": 500, "amount_high": 100},
                {**OBL, "recurrence": "sometimes"}, {**OBL, "due_on": "next spring"},
                {**OBL, "offsets": [{"offset_id": "x", "low": 1, "high": 2, "support": "confirmed"}]}):
        with pytest.raises(adapters.AdapterError):
            adapters.financial_item("synthetic:r1", bad)


def test_continuity_rows_are_strict_and_never_derive_currency():
    rota = adapters.continuity_record("rota_export", "synthetic:r", {"service": "svc-a", "responder": "r1",
                                                                      "start": "2026-11-01", "end": "2026-12-01",
                                                                      "coverage": 4})
    assert rota.service == "svc-a" and rota.coverage == 4.0
    for kind, row in (("qualification_export", {"responder": "r1", "scope": "svc-a", "qualification": "qualified"}),
                      ("qualification_export", {"responder": "r1", "scope": "svc-a", "qualification": "qualified",
                                                "currency": "recent"}),
                      ("rota_export", {"service": "svc-a", "responder": "r1", "start": "2026-11-01",
                                       "end": "2026-12-01", "coverage": 4, "source_kind": "leave register"}),
                      ("absence_export", {"scope": "svc-a", "current": "yes", "applies_to_scope": True,
                                          "covers_window": True, "authoritative": True}),
                      ("continuity_event_export", {"event": "holiday", "scope": "svc-a", "effective_on": "2026-11-01"}),
                      ("rota_export", {"service": "svc-a", "responder": "r1", "start": "2026-12-01",
                                       "end": "2026-11-01", "coverage": 4})):
        with pytest.raises(adapters.AdapterError):
            adapters.continuity_record(kind, "synthetic:r", row)


# ---------------------------------------------------------------------------------------------- database

D1_AT = datetime.fromisoformat("2026-11-16T08:00:00+05:30")
KINDS = {"obligations": "financial_obligation_export", "rota": "rota_export", "qualifications": "qualification_export",
         "ownership": "ownership_export", "absence": "absence_export", "events": "continuity_event_export"}


def _rules():
    base = load_catalogue()
    return base + [replace(base[0], pattern=f"data_room/ops/{name}.jsonl", kind=kind, system="synthetic_ops",
                           data_class="confidential", records="rows", row_class=None, contract=None)
                   for name, kind in KINDS.items()]


def _write(pack, name, rows):
    path = pack / "data_room" / "ops" / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


@pytest.fixture
def sources(db, ingest_conn, tenant, tmp_path):
    from synthetic_pack import make_d1

    from veloxa.ingest.pipeline import admit_pack
    from veloxa.releases import Pack, Schedule, current_time

    d1 = make_d1(tmp_path)
    rec = "2026-09-01"
    _write(d1, "obligations", [
        OBL,
        {**OBL, "item_id": "o2", "consequence_key": "k2", "instrument": "invoice", "recurrence": "recurring",
         "due_on": None, "first_due": "2026-10-01", "last_due": "2027-03-01", "every_months": 1,
         "amount_low": 50, "amount_high": 50},
        {**OBL, "subject": "contract:synthetic-2", "item_id": "o3", "instrument": "meeting_summary"}])
    _write(d1, "rota", [{"service": "svc-a", "responder": "r1", "start": "2026-11-01", "end": "2026-12-01",
                         "coverage": 4, "recorded_on": rec},
                        {"service": "svc-a", "responder": "r2", "start": "2026-11-01", "end": "2026-12-01",
                         "coverage": 4, "recorded_on": rec},
                        {"service": "svc-b", "responder": "r3", "start": "2026-11-01", "end": "2026-12-01",
                         "coverage": 4, "source_kind": "leave register", "recorded_on": rec}])
    _write(d1, "qualifications", [{"responder": "r1", "scope": "svc-a", "qualification": "qualified",
                                   "currency": "current", "recorded_on": rec},
                                  {"responder": "r2", "scope": "svc-a", "qualification": "qualified",
                                   "currency": "currency_unknown", "recorded_on": rec}])
    _write(d1, "ownership", [{"system": "sys-a", "owner": "o1", "currency": "current", "recorded_on": rec}])
    _write(d1, "absence", [{"scope": "sys-b", "current": True, "applies_to_scope": True, "covers_window": False,
                            "authoritative": True, "recorded_on": rec}])
    _write(d1, "events", [{"event": "departure", "scope": "svc-a", "effective_on": "2026-10-15", "person": "r9",
                           "recorded_on": rec}])
    sched = Schedule("src", "UTC", (Pack("D1", 1, "Day1_Mon_16Nov", D1_AT),))
    admit_pack(ingest_conn, tenant, sched, "D1", tmp_path, current_time(), rules=_rules())
    with connect(db.dsn("projection")) as prj:
        projector.build(prj, tenant)
    return tenant


def _run(conn, tenant, mode=Mode.VALID_AT, at=date(2026, 11, 15)):
    from veloxa.releases import current_time

    b = resolve_basis(tenant, ledger.sealed_releases(conn, tenant), mode, at, "UTC", current_time())
    return registry.run(conn, b, registry.context_for(b, "UTC", [], []))


@pytest.mark.db
def test_c2_computes_raw_inr_exposure_from_binding_obligations_only(app_conn, sources):
    r = _run(app_conn, sources)
    c2 = {s.subject: s for s in r.signals if s.criterion == Criterion.C2}
    ok = c2["contract:synthetic-1"]
    # H = [15 Nov 2026, 15 Nov 2027): one-time 300 on 10 Jan 2027, plus monthly 50 from 1 Dec 2026 to 1 Mar 2027 (4)
    assert ok.status == Status.ACTIVE and ok.value.unit == "INR" and (ok.value.low, ok.value.high) == (500.0, 500.0)
    assert ok.window_start == date(2026, 11, 15) and ok.authority == Authority.BINDING_DOCUMENT
    assert all(e.version == 1 and e.recorded_basis == "source_date" for e in ok.evidence)
    refused = c2["contract:synthetic-2"]                                   # a meeting summary is never binding
    assert refused.status == Status.UNQUANTIFIED and not refused.value.numeric and "binding" in refused.missing[0]


@pytest.mark.db
def test_c3_reports_evidence_states_blocks_the_measure_and_refuses_leave_data(app_conn, sources):
    r = _run(app_conn, sources)
    c3 = [s for s in r.signals if s.criterion == Criterion.C3]
    (ev,) = [s for s in c3 if s.subject == "svc-a" and s.signal_type == SignalType.CONTINUITY_EVIDENCE]
    assert ev.status == Status.PROVISIONAL and ev.value.category == "qualification_unresolved"
    assert {Flag.CURRENCY_UNKNOWN, Flag.GUARDRAIL_NOT_ACTIVE, Flag.CRITICALITY_NOT_DESIGNATED} <= ev.flags
    (m,) = [s for s in c3 if s.subject == "svc-a" and s.signal_type == SignalType.CONTINUITY_MEASURE]
    assert m.status == Status.BLOCKED and not m.value.numeric
    assert not any(s.subject == "svc-b" for s in c3)                       # leave-derived rota refused
    assert any("leave" in w.reason for w in r.withheld)
    (absent,) = [s for s in c3 if s.subject == "sys-b"]
    assert Flag.ABSENCE_EVIDENCE_INSUFFICIENT in absent.flags and Flag.CONFIRMED_ABSENCE not in absent.flags
    assert any("not applied" in w.reason and "Q10" in w.reason for w in r.withheld)   # events never applied
    assert not any(s.value.numeric for s in c3)


@pytest.mark.db
def test_source_rows_follow_recorded_time_and_record_under_the_database_time_checks(app_conn, sources):
    early = _run(app_conn, sources, Mode.RECORDED_BY, date(2026, 8, 1))   # rows were recorded on 1 Sep
    assert [s.status for s in early.signals if s.criterion == Criterion.C2] == [Status.MISSING]
    ref = registry.record(app_conn, _run(app_conn, sources), datetime.fromisoformat("2026-11-17T12:00:00+05:30"))
    assert ref["run_id"]                                                   # VALID_AT run accepted by the DB checks
