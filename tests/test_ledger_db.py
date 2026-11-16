"""Database tests: admission, provenance, append-only history, RLS, payroll restriction, visibility, audit.

All data here is SYNTHETIC (tests/synthetic_pack.py).
"""

import hashlib
import json
from datetime import timedelta

import psycopg
import pytest
from conftest import AFTER_ALL, D1_DAY
from synthetic_pack import EXPECTED_BY_CLASS, EXPECTED_FILES, EXPECTED_RECORDS, make_poisoned_d2

from veloxa import audit, ledger, selfcheck
from veloxa.db import connect, tenant_tx
from veloxa.ingest.pipeline import admit_pack
from veloxa.migrate import apply_migrations
from veloxa.releases import AdmissionRefused

pytestmark = pytest.mark.db


def _admit_d1(conn, tenant, schedule, root, now=D1_DAY):
    return admit_pack(conn, tenant, schedule, "D1", root, now)


def test_migrations_are_idempotent(db):
    assert apply_migrations(db.dsn("migrator")) == []


def test_selfcheck_passes_for_service_roles_and_fails_for_superuser(db):
    for role in ("app", "ingest"):
        with connect(db.dsn(role)) as conn:
            assert selfcheck.run(conn) == []
    with connect(db.admin_dsn) as conn:
        assert any("superuser" in f for f in selfcheck.run(conn))


def test_d1_admission_counts_and_seal(ingest_conn, tenant, schedule, synthetic_root):
    r = _admit_d1(ingest_conn, tenant, schedule, synthetic_root)
    assert (r.status, r.batch_seq, r.file_count, r.record_count) == ("admitted", 1, EXPECTED_FILES, EXPECTED_RECORDS)
    assert r.by_class == EXPECTED_BY_CLASS
    (batch,) = ledger.batches(ingest_conn, tenant)
    assert batch["sealed"] and batch["pack_id"] == "D1"
    assert batch["release_at"] == schedule.pack("D1").release_at


def test_provenance_hashes_locators_and_payloads_match_the_files(ingest_conn, tenant, schedule, synthetic_root):
    _admit_d1(ingest_conn, tenant, schedule, synthetic_root)
    pack_dir = synthetic_root / "Day1_Mon_16Nov"
    with tenant_tx(ingest_conn, tenant):
        raws = ingest_conn.execute("SELECT * FROM raw_index.raw_object").fetchall()
        recs = ingest_conn.execute(
            "SELECT r.*, o.content_sha256 FROM ledger.source_record r JOIN raw_index.raw_object o"
            " USING (tenant_id, batch_seq, source_path)"
        ).fetchall()
    for raw in raws:
        assert raw["content_sha256"] == hashlib.sha256((pack_dir / raw["source_path"]).read_bytes()).hexdigest()
    assert len(recs) == EXPECTED_RECORDS
    for rec in recs:
        assert rec["raw_object"] == rec["content_sha256"]  # every record points at the exact raw bytes
        assert rec["locator"] and rec["parser_version"]
        assert rec["extracted_at"] > schedule.pack("D1").release_at  # actual read time, not the release time
        assert rec["record_id"].startswith(f"{rec['system']}:{rec['source_path']}#")
    hr = {r["locator"]["row"]: r["payload"] for r in recs if r["source_path"] == "data_room/hr/Employee_Master.xlsx"}
    assert hr[4]["Emp ID"] == "T001" and hr[5]["Emp ID"] == "T002"


def test_readmission_is_idempotent_and_changed_content_is_refused(ingest_conn, tenant, schedule, synthetic_root):
    _admit_d1(ingest_conn, tenant, schedule, synthetic_root)
    assert _admit_d1(ingest_conn, tenant, schedule, synthetic_root).status == "already_admitted"
    (synthetic_root / "Day1_Mon_16Nov" / "data_room" / "README.txt").write_text("changed\n", encoding="utf-8")
    with pytest.raises(AdmissionRefused) as exc:
        _admit_d1(ingest_conn, tenant, schedule, synthetic_root)
    assert exc.value.reason == "manifest_changed"


def test_unreleased_pack_is_refused_before_any_file_is_read(ingest_conn, tenant, schedule, synthetic_root):
    _admit_d1(ingest_conn, tenant, schedule, synthetic_root)
    make_poisoned_d2(synthetic_root)  # reading this folder would raise UnclassifiedSource
    with pytest.raises(AdmissionRefused) as exc:
        admit_pack(ingest_conn, tenant, schedule, "D2", synthetic_root, D1_DAY)
    assert exc.value.reason == "not_yet_released"
    assert [b["batch_seq"] for b in ledger.batches(ingest_conn, tenant)] == [1]


def test_out_of_order_admission_is_refused_in_code_and_in_the_database(ingest_conn, tenant, schedule, synthetic_root):
    with pytest.raises(AdmissionRefused) as exc:
        admit_pack(ingest_conn, tenant, schedule, "D2", synthetic_root, AFTER_ALL)
    assert exc.value.reason == "out_of_order"
    with pytest.raises(psycopg.errors.CheckViolation):
        with tenant_tx(ingest_conn, tenant):
            ingest_conn.execute(
                "INSERT INTO ledger.admission_batch (tenant_id, batch_seq, pack_id, release_at, admitted_at,"
                " manifest_sha256, file_count, record_count) VALUES (%s, 3, 'X', now(), now(), %s, 0, 0)",
                (tenant, "0" * 64),
            )


@pytest.mark.parametrize("statement", [
    "UPDATE ledger.source_record SET payload = '{}'::jsonb",
    "DELETE FROM ledger.source_record",
    "UPDATE raw_index.raw_object SET kind = 'x'",
    "DELETE FROM raw_index.raw_object",
    "UPDATE ledger.admission_batch SET sealed = false, sealed_at = NULL",
    "DELETE FROM ledger.admission_batch",
    "TRUNCATE ledger.source_record",
    "UPDATE audit.event SET kind = 'x'",
])
def test_history_is_append_only_for_the_ingest_role(ingest_conn, tenant, schedule, synthetic_root, statement):
    _admit_d1(ingest_conn, tenant, schedule, synthetic_root)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with tenant_tx(ingest_conn, tenant):
            ingest_conn.execute(statement)


def test_nothing_can_be_added_to_a_sealed_batch(ingest_conn, tenant, schedule, synthetic_root):
    _admit_d1(ingest_conn, tenant, schedule, synthetic_root)
    with pytest.raises(psycopg.errors.CheckViolation):
        with tenant_tx(ingest_conn, tenant):
            ingest_conn.execute(
                "INSERT INTO raw_index.raw_object (tenant_id, batch_seq, source_path, content_sha256, size_bytes,"
                " kind, system, data_class, record_mode)"
                " VALUES (%s, 1, 'late.txt', %s, 1, 'k', 's', 'internal', 'none')",
                (tenant, "a" * 64),
            )


def test_tenant_isolation_with_rls(ingest_conn, tenant, schedule, synthetic_root, db):
    _admit_d1(ingest_conn, tenant, schedule, synthetic_root)
    import uuid

    other = uuid.uuid4()
    with psycopg.connect(db.admin_dsn, autocommit=True) as admin:
        admin.execute("INSERT INTO directory.tenant VALUES (%s, %s, true, 'UTC')", (other, f"t-{other.hex[:12]}"))
    with tenant_tx(ingest_conn, other):
        for table in ("ledger.admission_batch", "raw_index.raw_object", "ledger.source_record", "audit.event"):
            assert ingest_conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] == 0
    # Writing another tenant's rows is rejected by WITH CHECK.
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with tenant_tx(ingest_conn, other):
            ingest_conn.execute(
                "INSERT INTO audit.event (tenant_id, seq, occurred_at, kind, detail, hash)"
                " VALUES (%s, 1, now(), 'x', '{}', %s)", (tenant, "b" * 64))


def test_missing_tenant_context_fails_closed(app_conn):
    with pytest.raises(psycopg.errors.UndefinedObject):  # unrecognized configuration parameter
        with app_conn.transaction():
            app_conn.execute("SELECT count(*) FROM ledger.source_record")


def test_empty_tenant_context_fails_closed(app_conn):
    with pytest.raises(psycopg.errors.InvalidTextRepresentation):
        with app_conn.transaction():
            app_conn.execute("SELECT set_config('app.tenant_id', '', true)")
            app_conn.execute("SELECT count(*) FROM ledger.source_record")


@pytest.mark.parametrize("data_class, ingest_sees", [("sensitive_personal", 2), ("payroll_aggregate", 1)])
def test_app_role_cannot_read_payroll_lines_or_aggregates(ingest_conn, app_conn, tenant, schedule, synthetic_root,
                                                          data_class, ingest_sees):
    """Regression: payroll aggregates are released only through the disclosure auditor, so the app role
    (which every future query route uses) cannot read them directly."""
    _admit_d1(ingest_conn, tenant, schedule, synthetic_root)
    q = "SELECT count(*) AS n FROM ledger.source_record WHERE data_class = %s"
    with tenant_tx(ingest_conn, tenant):
        assert ingest_conn.execute(q, (data_class,)).fetchone()["n"] == ingest_sees
    with tenant_tx(app_conn, tenant):
        assert app_conn.execute(q, (data_class,)).fetchone()["n"] == 0
    with tenant_tx(app_conn, tenant, batch_seq=1):
        assert app_conn.execute(
            "SELECT count(*) AS n FROM ledger.visible_source_record WHERE data_class = %s", (data_class,)
        ).fetchone()["n"] == 0


class _Rollback(Exception):
    pass


def test_selfcheck_fails_if_the_protected_pay_policy_is_removed(db):
    with connect(db.admin_dsn) as conn:
        with pytest.raises(_Rollback):
            with conn.transaction():
                conn.execute("DROP POLICY protected_pay_for_app ON ledger.source_record")
                assert any("protected-pay" in f for f in selfcheck.run(conn))
                raise _Rollback


def test_visibility_by_knowledge_point(ingest_conn, app_conn, tenant, schedule, synthetic_root):
    assert ledger.visible_record_ids(app_conn, tenant, 0) == []
    _admit_d1(ingest_conn, tenant, schedule, synthetic_root)
    visible = ledger.visible_record_ids(app_conn, tenant, 1)
    assert len(visible) == EXPECTED_RECORDS - 3  # payroll lines and the payroll aggregate hidden from the app
    assert ledger.visible_record_ids(app_conn, tenant, 0) == []
    with pytest.raises(ledger.InvalidTime):
        ledger.visible_record_ids(app_conn, tenant, 2)  # beyond the sealed watermark


def test_visibility_view_requires_an_explicit_knowledge_point(ingest_conn, app_conn, tenant, schedule, synthetic_root):
    _admit_d1(ingest_conn, tenant, schedule, synthetic_root)
    with pytest.raises(psycopg.errors.InvalidTextRepresentation):
        with tenant_tx(app_conn, tenant):  # batch_seq not given -> '' -> cast error
            app_conn.execute("SELECT count(*) FROM ledger.visible_source_record")


def test_known_at_maps_dates_to_release_times(ingest_conn, app_conn, tenant, schedule, synthetic_root):
    _admit_d1(ingest_conn, tenant, schedule, synthetic_root)
    d1 = schedule.pack("D1").release_at
    assert ledger.k_for_instant(app_conn, tenant, d1 - timedelta(seconds=1)) == 0
    assert ledger.k_for_instant(app_conn, tenant, d1) == 1


def test_audit_chain_records_counts_only_and_verifies(ingest_conn, tenant, schedule, synthetic_root):
    _admit_d1(ingest_conn, tenant, schedule, synthetic_root)
    with tenant_tx(ingest_conn, tenant):
        (event,) = ingest_conn.execute("SELECT kind, detail FROM audit.event").fetchall()
        assert audit.verify_chain(ingest_conn, tenant)
    assert event["kind"] == "admission.sealed"
    assert event["detail"]["records"] == EXPECTED_RECORDS
    # Counts and hashes only: no synthetic value from the files appears in the audit detail.
    text = json.dumps(event["detail"])
    assert "Person Alpha" not in text and "T001" not in text and "1000" not in text


def test_release_extraction_admission_and_seal_times_are_distinct_and_ordered(ingest_conn, tenant, schedule,
                                                                              synthetic_root):
    """Regression for A-025: extracted_at is the actual read time, not the pack's release time."""
    from datetime import UTC, datetime

    started = datetime.now(UTC)
    _admit_d1(ingest_conn, tenant, schedule, synthetic_root)
    finished = datetime.now(UTC)
    with tenant_tx(ingest_conn, tenant):
        b = ingest_conn.execute("SELECT release_at, admitted_at, sealed_at FROM ledger.admission_batch").fetchone()
        e = ingest_conn.execute(
            "SELECT min(extracted_at) AS first, max(extracted_at) AS last FROM ledger.source_record").fetchone()
    assert b["release_at"] == schedule.pack("D1").release_at
    assert b["release_at"] < e["first"] <= e["last"] <= b["admitted_at"] <= b["sealed_at"]
    assert started <= e["first"] and b["sealed_at"] <= finished  # actual wall-clock times of this run
    assert e["first"] != b["release_at"]


def test_database_rejects_admission_before_release(ingest_conn, tenant):
    with pytest.raises(psycopg.errors.CheckViolation):
        with tenant_tx(ingest_conn, tenant):
            ingest_conn.execute(
                "INSERT INTO ledger.admission_batch (tenant_id, batch_seq, pack_id, release_at, admitted_at,"
                " manifest_sha256, file_count, record_count)"
                " VALUES (%s, 1, 'X', now() + interval '1 day', now(), %s, 0, 0)", (tenant, "0" * 64))


def _open_batch_with_raw(conn, tenant):
    conn.execute(
        "INSERT INTO ledger.admission_batch (tenant_id, batch_seq, pack_id, release_at, admitted_at,"
        " manifest_sha256, file_count, record_count)"
        " VALUES (%s, 1, 'X', now() - interval '1 hour', now(), %s, 1, 1)",
        (tenant, "0" * 64))
    conn.execute(
        "INSERT INTO raw_index.raw_object (tenant_id, batch_seq, source_path, content_sha256, size_bytes, kind,"
        " system, data_class, record_mode) VALUES (%s, 1, 'f.txt', %s, 1, 'k', 's', 'internal', 'document')",
        (tenant, "a" * 64))


def _insert_record(conn, tenant, raw_hash, extracted_sql):
    conn.execute(
        "INSERT INTO ledger.source_record (tenant_id, record_id, version, batch_seq, source_path, raw_object,"
        " locator, kind, system, parser_version, data_class, extracted_at, payload)"
        " VALUES (%s, 'r', 1, 1, 'f.txt', %s, '{}', 'k', 's', 'p/1', 'internal', " + extracted_sql + ", '{}')",
        (tenant, raw_hash))


def test_database_rejects_a_record_whose_hash_differs_from_the_raw_manifest(ingest_conn, tenant):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with tenant_tx(ingest_conn, tenant):
            _open_batch_with_raw(ingest_conn, tenant)
            _insert_record(ingest_conn, tenant, "b" * 64, "now()")


def test_seal_rejects_records_extracted_before_release(ingest_conn, tenant):
    with pytest.raises(psycopg.errors.CheckViolation):
        with tenant_tx(ingest_conn, tenant):
            _open_batch_with_raw(ingest_conn, tenant)
            _insert_record(ingest_conn, tenant, "a" * 64, "now() - interval '2 days'")
            ingest_conn.execute("UPDATE ledger.admission_batch SET sealed = true, sealed_at = now()"
                                " WHERE tenant_id = %s AND batch_seq = 1", (tenant,))


def test_concurrent_admissions_of_the_same_pack_are_idempotent(db, tenant, schedule, synthetic_root):
    """Regression: two runs that both pass the pre-lock check must not both insert; the second must
    re-check under the lock and report 'already_admitted'."""
    import threading

    barrier = threading.Barrier(2)
    results, errors = [], []

    def run():
        try:
            with connect(db.dsn("ingest")) as conn:
                barrier.wait()
                results.append(admit_pack(conn, tenant, schedule, "D1", synthetic_root, D1_DAY).status)
        except Exception as exc:  # surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=60)
    assert errors == []
    assert sorted(results) == ["admitted", "already_admitted"]
    with connect(db.dsn("ingest")) as conn:
        (batch,) = ledger.batches(conn, tenant)
        assert batch["sealed"] and batch["record_count"] == EXPECTED_RECORDS


def test_unsealed_data_is_never_visible_even_inside_the_writing_transaction(ingest_conn, tenant):
    """Owner decision (16 Nov 2026): never expose unsealed data. An unsealed batch contributes nothing to the
    sealed watermark or to reads at K, even for the transaction that is writing it."""
    with pytest.raises(_Rollback):
        with tenant_tx(ingest_conn, tenant, batch_seq=1):
            _open_batch_with_raw(ingest_conn, tenant)
            _insert_record(ingest_conn, tenant, "a" * 64, "now()")
            assert ingest_conn.execute("SELECT count(*) AS n FROM ledger.source_record").fetchone()["n"] == 1
            assert ingest_conn.execute("SELECT count(*) AS n FROM ledger.visible_source_record").fetchone()["n"] == 0
            assert ingest_conn.execute(
                "SELECT coalesce(max(batch_seq), 0) AS k FROM ledger.admission_batch WHERE sealed"
            ).fetchone()["k"] == 0
            raise _Rollback
