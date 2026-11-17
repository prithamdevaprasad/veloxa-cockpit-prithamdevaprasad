"""Evidence detectors against the database: sealed release boundaries, evidence integrity, tenant isolation,
idempotent recording, the signal store's own constraints, and briefing integration. SYNTHETIC packs only: every
name, date and figure is invented, and no output here is a real company finding."""

import json
import uuid
from dataclasses import replace
from datetime import datetime, timedelta

import psycopg
import pytest

from veloxa import ledger, projector, tools
from veloxa.db import connect, tenant_tx
from veloxa.risk import briefing
from veloxa.signals import registry
from veloxa.signals.contract import (
    Authority,
    Criterion,
    DetectorOutput,
    EvidenceRef,
    Flag,
    Signal,
    SignalType,
    Status,
)
from veloxa.signals.evidence import EvidenceError, verify
from veloxa.temporal import Mode, resolve_basis

pytestmark = pytest.mark.db

IST = "Asia/Kolkata"
D1_AT = datetime.fromisoformat("2026-11-16T08:00:00+05:30")
D2_AT = datetime.fromisoformat("2026-11-16T09:00:00+05:30")
ALPHA = tools.Principal("person:T001", "authenticated", "alpha@example.test")
Q1_DOC = "documents:data_room/documents/Plan_Q1.pdf#document"
Q2_DOC = "documents:data_room/documents/Plan_Q2.pdf#document"


@pytest.fixture
def packs(db, ingest_conn, tenant, tmp_path):
    """D1: a synthetic plan stating a dated milestone. D2: a later plan recording a delay, and a meeting summary."""
    from synthetic_pack import make_d1, make_document, make_transcript

    from veloxa.ingest.pipeline import admit_pack
    from veloxa.releases import Pack, Schedule, current_time

    sched = Schedule("sig", IST, (Pack("D1", 1, "Day1_Mon_16Nov", D1_AT), Pack("D2", 2, "Day2_Tue_17Nov", D2_AT)))
    d1 = make_d1(tmp_path)
    make_document(d1, "Plan_Q1.pdf", ["Synthetic company: Board update",
                                      "Quarterly update. Period: Jan to Mar 2026. Sent 9 Apr 2026.",
                                      "Key numbers", "Metric Value", "Customers 10", "Product",
                                      "Orion is on track for GA on 15 June 2026."])
    d2 = tmp_path / "Day2_Tue_17Nov"
    make_document(d2, "Plan_Q2.pdf", ["Synthetic company: Board update",
                                      "Quarterly update. Period: Apr to Jun 2026. Sent 9 Jul 2026.",
                                      "Key numbers", "Metric Value", "Customers 12", "Product",
                                      "Orion is in final testing. We postponed GA after extra testing."])
    make_transcript(d2, "T9_Synthetic.docx", ["T9 synthetic call",
                                              "The board expects the figure by Q3 (unclear which figure)."])
    for p in ("D1", "D2"):
        admit_pack(ingest_conn, tenant, sched, p, tmp_path, current_time())
    with connect(db.dsn("projection")) as prj:
        projector.build(prj, tenant)
    return tenant


def _basis(conn, tenant, mode=Mode.CURRENT, at=None):
    from veloxa.releases import current_time

    return resolve_basis(tenant, ledger.sealed_releases(conn, tenant), mode, at, IST, current_time())


def _run(conn, basis):
    return registry.run(conn, basis, registry.context_for(basis, "UTC", [], []))


def _c1(result, t):
    return [s for s in result.signals if s.criterion == Criterion.C1 and s.signal_type == t]


def test_signals_respect_sealed_release_boundaries_and_later_supersession(app_conn, packs):
    k1 = _run(app_conn, _basis(app_conn, packs, Mode.KNOWN_AT, D2_AT - timedelta(seconds=1)))
    assert k1.basis.batch_seq == 1
    assert all(s.lineage_batch_seq <= 1 for s in k1.signals)
    assert not any(Q2_DOC in s.record_ids for s in k1.signals)          # D2 was not yet released
    (c,) = _c1(k1, SignalType.COMMITMENT)
    assert c.status == Status.ACTIVE and Flag.DATE_SUPERSEDED not in c.flags and not _c1(k1, SignalType.DELIVERY_DELAY)

    k2 = _run(app_conn, _basis(app_conn, packs))
    assert k2.basis.batch_seq == 2 and max(s.lineage_batch_seq for s in k2.signals) == 2
    (c,) = [s for s in _c1(k2, SignalType.COMMITMENT) if s.subject == "Orion"]
    (st,) = _c1(k2, SignalType.COMMITMENT_STATUS)
    assert Flag.DATE_SUPERSEDED in c.flags and st.value.category == "delayed_new_date_not_stated"
    assert {Q1_DOC, Q2_DOC} == set(st.record_ids)
    unresolved = [s for s in _c1(k2, SignalType.COMMITMENT) if s.subject == "unresolved commitment"]
    assert unresolved and unresolved[0].authority == Authority.TRANSCRIPT_SUMMARY
    assert unresolved[0].status == Status.PROVISIONAL


def test_cited_evidence_must_be_admitted_visible_at_k_and_not_protected(app_conn, ingest_conn, packs):
    k1 = _basis(app_conn, packs, Mode.KNOWN_AT, D2_AT - timedelta(seconds=1))
    probe = Signal("probe@1", Criterion.C1, SignalType.COMMITMENT, Status.ACTIVE, "probe", "C1.probe@1",
                   authority=Authority.FORMAL_REPORTED, evidence=(EvidenceRef(Q1_DOC),))
    assert verify(app_conn, k1, [probe])[0].lineage_batch_seq == 1
    for bad in ("documents:invented#document", Q2_DOC):   # invented; admitted only in a later, unreleased batch
        with pytest.raises(EvidenceError, match="not admitted and visible"):
            verify(app_conn, k1, [replace(probe, evidence=(EvidenceRef(bad),))])
    with tenant_tx(ingest_conn, packs):
        pay = ingest_conn.execute("SELECT record_id FROM ledger.source_record WHERE data_class = 'sensitive_personal'"
                                  " LIMIT 1").fetchone()["record_id"]
    with pytest.raises(EvidenceError):
        verify(app_conn, k1, [replace(probe, evidence=(EvidenceRef(pay),))])


def test_runs_are_reproducible_idempotent_and_keep_detector_versions_and_provenance(app_conn, packs):
    basis = _basis(app_conn, packs)
    first, again = _run(app_conn, basis), _run(app_conn, basis)
    assert [s.signal_id for s in first.signals] == [s.signal_id for s in again.signals]
    assert first.output_sha256 == again.output_sha256
    now = datetime.fromisoformat("2026-11-17T12:00:00+05:30")
    ref = registry.record(app_conn, first, now)
    assert registry.record(app_conn, again, now + timedelta(hours=1))["run_id"] == ref["run_id"]
    with tenant_tx(app_conn, packs):
        assert app_conn.execute("SELECT count(*) AS n FROM runtime.detector_run").fetchone()["n"] == 1
        withheld = app_conn.execute("SELECT withheld FROM runtime.detector_run").fetchone()["withheld"]
    assert any("e-mail is not read" in w["reason"] for w in withheld)   # why a signal was withheld is kept
    stored = registry.fetch_signals(app_conn, packs, ref["run_id"])
    assert len(stored) == len(first.signals)
    detectors = {s["detector"] for s in stored}
    assert detectors == {"c1_delivery@2", "c2_financial@2", "c3_continuity@2", "c4_integrity@2"}
    c2 = next(s for s in stored if s["criterion"] == "C2")
    assert c2["status"] == "missing_evidence" and c2["value"]["low"] is None   # missing, never zero
    assert all(s["evidence"] for s in stored if s["status"] in ("active", "provisional"))


def test_duplicate_signals_collapse_to_one(app_conn, packs, monkeypatch):
    dup = Signal("dup@1", Criterion.C1, SignalType.COMMITMENT, Status.ACTIVE, "x", "C1.x@1",
                 authority=Authority.FORMAL_REPORTED, evidence=(EvidenceRef(Q1_DOC),))
    fake = registry.Detector("dup@1", Criterion.C1, lambda c, b, x: DetectorOutput([dup, dup]), "test", ())
    monkeypatch.setattr(registry, "REGISTRY", (fake,))
    assert len(_run(app_conn, _basis(app_conn, packs)).signals) == 1


def test_a_detector_cannot_emit_for_another_criterion(app_conn, packs, monkeypatch):
    wrong = Signal("dup@1", Criterion.C2, SignalType.FINANCIAL_EXPOSURE, Status.MISSING, "x", "C2.x@1")
    fake = registry.Detector("dup@1", Criterion.C1, lambda c, b, x: DetectorOutput([wrong]), "test", ())
    monkeypatch.setattr(registry, "REGISTRY", (fake,))
    with pytest.raises(ValueError, match="emitted a signal"):
        _run(app_conn, _basis(app_conn, packs))


def _insert(conn, tenant, run_id, **over):
    row = {"signal_id": "S-" + "a" * 16, "detector": "probe@1", "criterion": "C1", "signal_type": "commitment",
           "status": "active", "lineage": 1, "evidence": [Q1_DOC], "payload": {"value": {"low": None, "high": None}}}
    row |= over
    with tenant_tx(conn, tenant, batch_seq=2):
        conn.execute("INSERT INTO runtime.risk_signal (tenant_id, run_id, signal_id, detector, criterion, signal_type,"
                     " status, lineage_batch_seq, evidence, payload) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                     (tenant, run_id, row["signal_id"], row["detector"], row["criterion"], row["signal_type"],
                      row["status"], row["lineage"], row["evidence"], json.dumps(row["payload"])))


def test_the_signal_store_enforces_evidence_lineage_numberless_states_and_append_only(db, app_conn, packs):
    k1 = _basis(app_conn, packs, Mode.KNOWN_AT, D2_AT - timedelta(seconds=1))
    ref = registry.record(app_conn, _run(app_conn, k1), datetime.fromisoformat("2026-11-17T12:00:00+05:30"))
    rid = ref["run_id"]
    with pytest.raises(psycopg.errors.ForeignKeyViolation, match="not admitted"):
        _insert(app_conn, packs, rid, evidence=["documents:invented#document"])
    with pytest.raises(psycopg.errors.ForeignKeyViolation, match="not admitted"):
        _insert(app_conn, packs, rid, evidence=[Q2_DOC])            # admitted only after this run's K
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(app_conn, packs, rid, lineage=2)                      # lineage beyond the run's K
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(app_conn, packs, rid, status="missing_evidence", evidence=[],
                payload={"value": {"low": 0, "high": 0}})            # missing is never zero
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(app_conn, packs, rid, evidence=[])                    # an active signal must cite evidence
    with pytest.raises(psycopg.errors.InsufficientPrivilege), tenant_tx(app_conn, packs):
        app_conn.execute("UPDATE runtime.risk_signal SET status = 'active'")
    with pytest.raises(psycopg.errors.InsufficientPrivilege), connect(db.admin_dsn) as admin, \
            tenant_tx(admin, packs):
        admin.execute("DELETE FROM runtime.detector_run")              # append-only even for the administrator


def test_one_tenant_cannot_see_another_tenants_signals(db, app_conn, packs):
    ref = registry.record(app_conn, _run(app_conn, _basis(app_conn, packs)),
                          datetime.fromisoformat("2026-11-17T12:00:00+05:30"))
    other = uuid.uuid4()
    with psycopg.connect(db.admin_dsn, autocommit=True) as admin:
        admin.execute("INSERT INTO directory.tenant (tenant_id, slug, synthetic, time_zone) VALUES (%s, %s, true,"
                      " 'UTC')", (other, f"t-{other.hex[:12]}"))
    assert registry.fetch_signals(app_conn, other, ref["run_id"]) == []
    with tenant_tx(app_conn, other):
        assert app_conn.execute("SELECT count(*) AS n FROM runtime.risk_signal WHERE tenant_id = %s",
                                (packs,)).fetchone()["n"] == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege), tenant_tx(app_conn, other):
        app_conn.execute("INSERT INTO runtime.detector_run (tenant_id, run_id, detected_at, batch_seq, mode,"
                         " assessment_date, detectors, config_sha256, output_sha256, signal_count, withheld) VALUES"
                         " (%s, gen_random_uuid(), now(), 1, 'CURRENT', '2026-11-17', '[]', %s, %s, 0, '[]')",
                         (packs, "0" * 64, "0" * 64))                  # RLS WITH CHECK: not into another tenant
    empty = _run(app_conn, _basis(app_conn, other))
    assert empty.signals == [] and empty.basis.batch_seq == 0


def _keys(obj):
    if isinstance(obj, dict):
        return set(obj) | {k for v in obj.values() for k in _keys(v)}
    if isinstance(obj, list):
        return {k for v in obj for k in _keys(v)}
    return set()


def test_the_briefing_shows_every_criterion_unranked_with_its_status(db, app_conn, packs):
    from veloxa.releases import current_time

    basis = _basis(app_conn, packs)
    b = briefing.serve(app_conn, packs, ALPHA, basis, current_time(), [])
    assert b["top_three"] is None and b["ranking_mode"] == "unweighted_partial_order"
    sec = b["evidence_signals"]
    assert set(sec["by_criterion"]) == {"C1", "C2", "C3", "C4", "C5"}
    by = sec["by_criterion"]
    assert by["C2"]["counts"] == {"missing_evidence": 1}
    assert by["C3"]["counts"] == {"missing_evidence": 4, "blocked_owner_decision": 1}
    assert by["C4"]["counts"] == {"unquantified": 1, "unmapped_requires_owner_approval": 1}  # Customers: periods
    assert by["C5"]["signals"] == [] and "time context" in by["C5"]["measurability"].lower()
    assert all(by[c]["measurability"] for c in by)       # nothing silently disappears
    c1_types = {s["signal_type"] for s in by["C1"]["signals"]}
    assert {"commitment", "delivery_delay", "commitment_status", "delivery_exposure"} <= c1_types
    assert all(e["batch_seq"] <= 2 and e["data_class"] not in ("sensitive_personal", "payroll_aggregate")
               for c in by for s in by[c]["signals"] for e in s["evidence_records"])
    assert not {"score", "rank", "priority", "weight", "weights", "severity", "probability"} & _keys(b)
    assert b["detector_run"]["detectors"] == [d.detector_id for d in registry.REGISTRY]
    again = briefing.serve(app_conn, packs, ALPHA, basis, current_time(), [])
    assert again["snapshot_id"] == b["snapshot_id"] and again["detector_run"] == b["detector_run"]
    with connect(db.admin_dsn) as admin, tenant_tx(admin, packs):
        assert admin.execute("SELECT count(*) AS n FROM policy.ranking_decision WHERE tenant_id = %s",
                             (packs,)).fetchone()["n"] == 0  # the administrator bypasses RLS: filter explicitly
