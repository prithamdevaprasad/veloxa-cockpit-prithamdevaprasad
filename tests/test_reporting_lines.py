"""Voice note T6b: reporting lines must not break when people move. SYNTHETIC HR exports only.

The HR export is a snapshot per admitted batch (projection.person), so a reporting line is answered from the
snapshot at the read basis K: a move recorded in a later batch never changes an earlier answer, and an
announced but unrecorded reorganisation (a plan in a document, with no effective record) changes nothing.
"""

import uuid
from datetime import datetime, timedelta

import psycopg
import pytest

from veloxa import ledger, projector, tools
from veloxa.authz import current
from veloxa.db import connect
from veloxa.temporal import Mode, resolve_basis

pytestmark = pytest.mark.db
D1_AT = datetime.fromisoformat("2026-11-16T08:00:00+05:30")
D2_AT = datetime.fromisoformat("2026-11-16T09:00:00+05:30")
HR = ["Emp ID", "Full Name", "Department", "Title", "Manager ID", "Join Date", "Status", "Exit Date", "Work Email"]


def person(i, dept, manager):
    return [f"T00{i}", f"Person {i}", dept, "Synthetic", manager, "2099-01-01", "Active", None, f"p{i}@example.test"]


@pytest.fixture
def moves(db, ingest_conn, tenant, tmp_path):
    """D1: T002 in Marketing reports to T001. D2: a board update ANNOUNCES an undated move to a new sales leader,
    and a corrected HR export records T002 now reporting to T003 (a recorded move)."""
    from synthetic_pack import _xlsx, make_board_update, make_d1

    from veloxa.ingest.pipeline import admit_pack
    from veloxa.releases import Pack, Schedule, current_time

    d1 = make_d1(tmp_path)
    _xlsx(d1 / "data_room" / "hr" / "Employee_Master.xlsx", "Synthetic HR export", HR,
          [person(1, "Leadership", None), person(2, "Marketing", "T001"), person(3, "Sales", "T001")])
    d2 = tmp_path / "Day2_Tue_17Nov"
    make_board_update(d2, [("Team", "3")], "Board_Plan.pdf")       # its prose announces nothing dated
    _xlsx(d2 / "data_room" / "hr" / "Employee_Master.xlsx", "Synthetic HR export", HR,
          [person(1, "Leadership", None), person(2, "Marketing", "T003"), person(3, "Sales", "T001")])
    sched = Schedule("org", "UTC", (Pack("D1", 1, "Day1_Mon_16Nov", D1_AT), Pack("D2", 2, "Day2_Tue_17Nov", D2_AT)))
    for p in ("D1", "D2"):
        admit_pack(ingest_conn, tenant, sched, p, tmp_path, current_time())
    with connect(db.dsn("projection")) as prj:
        projector.build(prj, tenant)
    return tenant


def manager_of(conn, tenant, emp, mode=Mode.CURRENT, at=None):
    from veloxa.releases import current_time

    b = resolve_basis(tenant, ledger.sealed_releases(conn, tenant), mode, at, "UTC", current_time())
    rows = {p["emp_id"]: p for p in tools._person_snapshot(conn, b)}
    return rows[emp]["manager_emp_id"], rows[emp]["source_version"], b.batch_seq


def test_a_recorded_move_is_visible_from_its_batch_and_never_rewrites_earlier_answers(app_conn, moves):
    before = manager_of(app_conn, moves, "T002", Mode.KNOWN_AT, D2_AT - timedelta(seconds=1))
    after = manager_of(app_conn, moves, "T002")
    assert before == ("T001", 1, 1)                   # the reporting line as known at K=1
    assert after == ("T003", 2, 2)                    # the recorded move, as a new record version at K=2
    assert manager_of(app_conn, moves, "T002", Mode.KNOWN_AT, D2_AT - timedelta(seconds=1)) == before  # unchanged


def test_an_announced_but_unrecorded_move_changes_nothing(app_conn, moves):
    # T003's line is untouched by the board update: only an HR record moves people.
    assert manager_of(app_conn, moves, "T003")[0] == "T001"
    assert manager_of(app_conn, moves, "T001")[0] is None


def test_no_effective_date_is_inferred(app_conn, moves):
    from veloxa.releases import current_time

    b = resolve_basis(moves, ledger.sealed_releases(app_conn, moves), Mode.CURRENT, None, "UTC", current_time())
    row = next(p for p in tools._person_snapshot(app_conn, b) if p["emp_id"] == "T002")
    assert "effective_date" not in row and row["exit_date"] is None   # a snapshot states no move date


def test_reporting_lines_never_cross_tenants(db, app_conn, moves):
    from veloxa.releases import current_time

    other = uuid.uuid4()
    with psycopg.connect(db.admin_dsn, autocommit=True) as admin:
        admin.execute("INSERT INTO directory.tenant (tenant_id, slug, synthetic, time_zone) VALUES (%s, %s, true,"
                      " 'UTC')", (other, f"t-{other.hex[:12]}"))
    b = resolve_basis(other, ledger.sealed_releases(app_conn, other), Mode.CURRENT, None, "UTC", current_time())
    assert b.batch_seq == 0 or tools._person_snapshot(app_conn, b) == []


def test_a_manager_change_is_not_a_current_authorization_grant(app_conn, moves):
    """Moving people never grants access: current authorization comes only from relationship tuples."""
    doc = current.Resource("document", "d", moves, "internal")
    assert not current.authorize(app_conn, "person:T003", moves, "read", doc, "answer").allowed
