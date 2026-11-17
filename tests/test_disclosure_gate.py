"""C19 disclosure gate end to end (ARCHITECTURE.md §15): segments, background rows, the append-only ledger,
serialization, the database release boundary and every existing channel. SYNTHETIC payroll only: invented people and
invented amounts; no real payroll value is used or printed.

Register (Jan-Mar 2099): Eng = T001..T006, T007 joins Eng in Feb; Ops = T008, T009. T003 is paid for 10 of 31 days
in Jan. T005 has no Mar row (a gap: never extrapolated). Every gross amount is 1000 x person number.
"""

import threading
import uuid
from datetime import datetime

import psycopg
import pytest

from veloxa import ledger, projector, tools
from veloxa.db import connect, tenant_tx
from veloxa.disclosure.gate import DisclosureGate, PayrollRequest, build_rows
from veloxa.temporal import Mode, resolve_basis

pytestmark = pytest.mark.db
D1_AT = datetime.fromisoformat("2026-11-16T08:00:00+05:30")
D2_AT = datetime.fromisoformat("2026-11-16T09:00:00+05:30")
ENG = [f"T00{i}" for i in range(1, 7)]
HR = ["Emp ID", "Full Name", "Department", "Title", "Manager ID", "Join Date", "Status", "Exit Date", "Work Email"]
PAY = ["Emp ID", "Month", "Days in Month", "Days Payable", "Gross (Rs.)", "Deductions (Rs.)", "Net (Rs.)",
       "Processed By"]


def person(i, dept):
    return [f"T00{i}", f"Person {i}", dept, "Synthetic", None, "2099-01-01", "Active", None, f"p{i}@example.test"]


def pay(i, month, days=31, payable=31):
    g = 1000 * i
    return [f"T00{i}", month, days, payable, g, g // 10, g - g // 10, "XX"]


def register(correct_t003=False):
    rows = []
    for m in ("Jan-2099", "Feb-2099", "Mar-2099"):
        for i in range(1, 7):
            if i == 5 and m == "Mar-2099":
                continue
            payable = 10 if (i == 3 and m == "Jan-2099" and not correct_t003) else 31
            rows.append(pay(i, m, 31, payable))
        if m != "Jan-2099":
            rows.append(pay(7, m))
        rows += [pay(8, m), pay(9, m)]
    return rows


@pytest.fixture
def payroll(db, ingest_conn, tenant, tmp_path):
    from synthetic_pack import _xlsx, make_d1

    from veloxa.ingest.pipeline import admit_pack
    from veloxa.releases import Pack, Schedule, current_time

    d1 = make_d1(tmp_path)
    people = [person(i, "Eng") for i in range(1, 8)] + [person(8, "Ops"), person(9, "Ops")]
    _xlsx(d1 / "data_room" / "hr" / "Employee_Master.xlsx", "Synthetic HR export", HR, people)
    _xlsx(d1 / "data_room" / "finance" / "Payroll_Register_Synthetic.xlsx", "Synthetic payroll", PAY, register())
    d2 = tmp_path / "Day2_Tue_17Nov"                     # D2: a CORRECTION of the register (T003 paid in full)
    _xlsx(d2 / "data_room" / "finance" / "Payroll_Register_Synthetic.xlsx", "Synthetic payroll", PAY,
          register(correct_t003=True))
    sched = Schedule("pay", "UTC", (Pack("D1", 1, "Day1_Mon_16Nov", D1_AT), Pack("D2", 2, "Day2_Tue_17Nov", D2_AT)))
    for p in ("D1", "D2"):
        admit_pack(ingest_conn, tenant, sched, p, tmp_path, current_time())
    with connect(db.dsn("projection")) as prj:
        projector.build(prj, tenant)
    return tenant


def enable(db, tenant, measure="payroll.gross"):
    with psycopg.connect(db.admin_dsn, autocommit=True) as admin:
        admin.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant),))
        admin.execute("INSERT INTO policy.disclosure_release (tenant_id, measure, enabled_by, enabled_on,"
                      " decision_record) VALUES (%s, %s, 'Synthetic owner', '2099-01-01', 'synthetic test')",
                      (tenant, measure))


def me(n=1):
    return tools.Principal(f"person:T00{n}", "authenticated", f"p{n}@example.test")


def basis(conn, tenant, k=None):
    from dataclasses import replace

    from veloxa.releases import current_time

    b = resolve_basis(tenant, ledger.sealed_releases(conn, tenant), Mode.CURRENT, None, "UTC", current_time())
    return replace(b, batch_seq=k) if k else b


def ask(conn, tenant, principal, months=None, department="Eng", key=None, gate=None):
    req = PayrollRequest(key or f"k-{uuid.uuid4()}", months, department)
    return (gate or DisclosureGate()).request(conn, principal, basis(conn, tenant), req)


def entries(conn, tenant, principal="person:T001"):
    with tenant_tx(conn, tenant, principal):
        return conn.execute("SELECT seq, decision, reason FROM policy.disclosure_ledger ORDER BY seq").fetchall()


# ---------------------------------------------------------------------------------------------- release boundary


def test_release_is_off_by_default_and_the_refusal_is_recorded_without_a_value(app_conn, payroll):
    r = ask(app_conn, payroll, me())
    assert (r.released, r.value, r.decision, r.reason) == (False, None, "refused", "release_not_enabled")
    assert [(e["decision"], e["reason"]) for e in entries(app_conn, payroll)] == [("refused", "release_not_enabled")]
    res = tools.payroll_total(app_conn, basis(app_conn, payroll), me())
    assert (res.status, res.facts, res.reason) == ("withheld", [], "release_not_enabled")


def test_the_database_releases_a_value_only_for_an_allowed_decision_of_the_same_principal(db, app_conn, payroll):
    enable(db, payroll)
    with pytest.raises(psycopg.errors.InsufficientPrivilege), tenant_tx(app_conn, payroll, "person:T001", 2):
        app_conn.execute("SELECT policy.release_payroll_sum(%s)", (str(uuid.uuid4()),))   # no decision recorded
    ok = ask(app_conn, payroll, me())
    assert ok.released
    with pytest.raises(psycopg.errors.InsufficientPrivilege), tenant_tx(app_conn, payroll, "person:T002", 2):
        app_conn.execute("SELECT policy.release_payroll_sum(%s)", (ok.entry_id,))        # another principal
    with pytest.raises(psycopg.errors.InsufficientPrivilege), tenant_tx(app_conn, payroll, "person:T001", 2):
        app_conn.execute("SELECT * FROM ledger.source_record WHERE system = 'payroll_register'").fetchall()
        app_conn.execute("UPDATE policy.disclosure_ledger SET decision = 'allowed'")      # append-only


# ---------------------------------------------------------------------------------------------- segments


def test_segments_are_one_per_person_with_exact_pro_rata_and_no_extrapolation(app_conn, payroll):
    with tenant_tx(app_conn, payroll, "person:T001", 1):
        k1 = app_conn.execute("SELECT * FROM policy.payroll_structure(1)").fetchall()
    assert all(set(s) == {"person", "month", "days_in_month", "days_payable", "record_id", "version"} for s in k1)
    row, cells, size = build_rows(k1, {f"person:{e}" for e in ENG} | {"person:T007"}, None)
    from fractions import Fraction as F

    assert row["seg:person:T003:0"] == F(10, 31) + 2 and row["seg:person:T001:0"] == 3
    assert row["seg:person:T005:0"] == 2 and "person:T005|Mar-2099" not in cells       # the gap stays a gap
    assert row["seg:person:T007:0"] == 2 and size == 7                                 # joiner: Feb and Mar only
    with tenant_tx(app_conn, payroll, "person:T001", 2):
        k2 = app_conn.execute("SELECT * FROM policy.payroll_structure(2)").fetchall()
    row2, _, _ = build_rows(k2, {"person:T003"}, ("Jan-2099",))
    assert row2 == {"seg:person:T003:0": F(1)}                                        # the correction, from K=2 only
    assert {s["version"] for s in k2} == {2} and {s["version"] for s in k1} == {1}


def test_ambiguous_or_unlinked_register_rows_are_refused():
    dup = [{"person": "person:A", "month": "Jan", "days_in_month": 31, "days_payable": 31, "record_id": "r1"},
           {"person": "person:A", "month": "Jan", "days_in_month": 31, "days_payable": 31, "record_id": "r2"}]
    with pytest.raises(ValueError, match="register_ambiguous"):
        build_rows(dup, {"person:A"}, None)
    same_pay = [{"person": f"person:{p}", "month": "Jan", "days_in_month": 31, "days_payable": 31,
                 "record_id": f"r{p}"} for p in "AB"]
    row, _, size = build_rows(same_pay, {"person:A", "person:B"}, None)
    assert size == 2 and set(row) == {"seg:person:A:0", "seg:person:B:0"}   # equal pay never merges people


# ---------------------------------------------------------------------------------------------- decisions


def test_an_allowed_release_returns_the_audited_aggregate(db, app_conn, payroll):
    enable(db, payroll)
    r = ask(app_conn, payroll, me(), ("Jan-2099",))
    assert r.released and r.decision == "allowed" and r.value == sum(1000 * i for i in range(1, 7))


def test_a_differencing_attack_across_a_joiner_is_denied(db, app_conn, payroll):
    enable(db, payroll)
    assert ask(app_conn, payroll, me(), ("Jan-2099", "Feb-2099")).released
    second = ask(app_conn, payroll, me(), ("Jan-2099",))      # stable pay: (Jan+Feb) - 2*Jan exposes the joiner
    assert (second.released, second.value, second.decision) == (False, None, "denied")
    assert "T007" not in second.reason and second.reason in ("would_disclose", "small_group")


def test_background_own_value_and_small_groups(db, app_conn, payroll):
    enable(db, payroll)
    ops = ask(app_conn, payroll, me(8), ("Jan-2099",), department="Ops")
    assert (ops.released, ops.reason) == (False, "cohort_below_k")
    # T001 knows its own pay: Eng in Jan without the joiner leaves the other five, a group of k: allowed.
    assert ask(app_conn, payroll, me(1), ("Jan-2099",)).released


def test_principals_measures_and_tenants_have_separate_ledgers(db, app_conn, payroll):
    enable(db, payroll)
    assert ask(app_conn, payroll, me(1), ("Jan-2099", "Feb-2099")).released
    other = ask(app_conn, payroll, me(2), ("Jan-2099",))      # T002's own ledger: no T001 history applies
    assert other.released
    assert [e["seq"] for e in entries(app_conn, payroll, "person:T001")] == [1]
    assert [e["seq"] for e in entries(app_conn, payroll, "person:T002")] == [1]
    req = PayrollRequest(f"k-{uuid.uuid4()}", ("Jan-2099",), "Eng", measure="payroll.net")
    assert DisclosureGate().request(app_conn, me(1), basis(app_conn, payroll), req).reason == "release_not_enabled"
    stranger = uuid.uuid4()
    with psycopg.connect(db.admin_dsn, autocommit=True) as admin:
        admin.execute("INSERT INTO directory.tenant (tenant_id, slug, synthetic, time_zone) VALUES (%s, %s, true,"
                      " 'UTC')", (stranger, f"t-{stranger.hex[:12]}"))
    with tenant_tx(app_conn, stranger, "person:T001", 2):
        assert app_conn.execute("SELECT count(*) AS n FROM policy.payroll_structure(2)").fetchone()["n"] == 0
        assert app_conn.execute("SELECT count(*) AS n FROM policy.disclosure_ledger").fetchone()["n"] == 0


def test_retries_reuse_the_decision_and_a_reused_key_cannot_change_the_question(db, app_conn, payroll):
    enable(db, payroll)
    first = ask(app_conn, payroll, me(), ("Jan-2099",), key="retry-key-1")
    again = ask(app_conn, payroll, me(), ("Jan-2099",), key="retry-key-1")
    assert again.entry_id == first.entry_id and again.value == first.value and len(entries(app_conn, payroll)) == 1
    changed = ask(app_conn, payroll, me(), ("Feb-2099",), key="retry-key-1")
    assert (changed.released, changed.reason) == (False, "idempotency_key_reused_for_a_different_request")
    assert len(entries(app_conn, payroll)) == 1


def test_concurrent_disclosures_for_one_scope_are_serialized(db, payroll):
    enable(db, payroll)
    barrier, results = threading.Barrier(2), {}

    def run(name, months):
        with connect(db.dsn("app")) as c:
            barrier.wait()
            results[name] = ask(c, payroll, me(), months)

    threads = [threading.Thread(target=run, args=("both", ("Jan-2099", "Feb-2099"))),
               threading.Thread(target=run, args=("jan", ("Jan-2099",)))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(r.released for r in results.values()) == [False, True]   # each alone is fine; together never
    with connect(db.dsn("app")) as c:
        assert [e["decision"] for e in entries(c, payroll)] in (["allowed", "denied"],)


def test_a_failure_after_the_decision_rolls_back_and_releases_nothing(db, app_conn, payroll, monkeypatch):
    enable(db, payroll)

    def boom(*a, **k):
        raise RuntimeError("connection lost")

    monkeypatch.setattr(DisclosureGate, "_finish", staticmethod(boom))
    r = ask(app_conn, payroll, me(), ("Jan-2099",))
    assert (r.released, r.value, r.reason) == (False, None, "disclosure_auditor_unavailable")
    assert entries(app_conn, payroll) == []                                    # nothing partial survives


def test_unresolved_principals_and_unsupported_bases_are_refused(app_conn, payroll):
    for p in (None, tools.Principal("user:x", "authenticated", None)):
        assert DisclosureGate().request(app_conn, p, basis(app_conn, payroll),
                                        PayrollRequest("k-unresolved")).reason == "principal_unresolved"
    from dataclasses import replace

    rb = replace(basis(app_conn, payroll), mode=Mode.RECORDED_BY)
    assert DisclosureGate().request(app_conn, me(), rb, PayrollRequest("k-recorded")).reason == \
        "read_basis_not_supported"
    assert DisclosureGate().request(app_conn, me(), basis(app_conn, payroll),
                                    PayrollRequest("k-max", statistic="MAX")).reason == "statistic_not_released"


# ---------------------------------------------------------------------------------------------- other channels


def test_no_other_channel_can_reach_payroll(db, app_conn, payroll):
    from veloxa.ask import pipeline
    from veloxa.risk import briefing

    b = basis(app_conn, payroll)
    with tenant_tx(app_conn, payroll, "person:T001", b.batch_seq):
        assert app_conn.execute("SELECT count(*) AS n FROM ledger.visible_source_record WHERE system ="
                                " 'payroll_register'").fetchone()["n"] == 0            # evidence views: RLS
    pay_ids = []
    with connect(db.dsn("ingest")) as ing, tenant_tx(ing, payroll):
        pay_ids = [r["record_id"] for r in ing.execute(
            "SELECT record_id FROM ledger.source_record WHERE data_class IN ('sensitive_personal',"
            " 'payroll_aggregate')").fetchall()]
    assert pay_ids and tools._evidence(app_conn, b, pay_ids) == []                    # evidence lookup
    brief = briefing.serve(app_conn, payroll, me(), b, datetime.fromisoformat("2026-11-17T12:00:00+05:30"), [])
    assert not any(i in str(brief) for i in pay_ids)                                   # briefing
    answer = pipeline.answer(app_conn, payroll, "UTC", me(), "What is the total payroll?",
                             datetime.fromisoformat("2026-11-17T12:00:00+05:30"))
    assert answer["status"] != "qualified" and not any(i in str(answer) for i in pay_ids)  # Ask


def test_there_is_no_export_agent_or_cache_path_to_bypass_the_gate(db, monkeypatch):
    from pathlib import Path

    from conftest import api_client

    routes = {getattr(r, "path", "") for r in api_client(db, monkeypatch).app.routes}
    assert not [r for r in routes if any(w in r for w in ("export", "agent", "payroll", "metric"))]
    src = Path(tools.__file__).resolve().parent
    assert not [p.name for p in src.rglob("*.py") if "lru_cache" in p.read_text(encoding="utf-8")
                or "functools.cache" in p.read_text(encoding="utf-8")]               # no protected caching exists
