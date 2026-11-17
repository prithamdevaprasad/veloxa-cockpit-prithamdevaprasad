"""Conflict register (Sprint 5 exit criterion: provenance and temporal support). SYNTHETIC packs only."""

from datetime import date, datetime

import pytest
from conftest import D1_DAY

from veloxa import ledger, projector
from veloxa.ask import conflicts
from veloxa.db import connect
from veloxa.temporal import Mode, resolve_basis

pytestmark = pytest.mark.db
IST = "Asia/Kolkata"
D1_AT = datetime.fromisoformat("2026-11-16T10:00:00+05:30")
D2_AT = datetime.fromisoformat("2026-11-17T10:00:00+05:30")


@pytest.fixture
def two_batches(db, ingest_conn, tenant, tmp_path):
    """D1 has no figures; D2 adds a board update and a meeting summary that disagree about ARR."""
    from synthetic_pack import make_board_update, make_d1, make_transcript

    from veloxa.ingest.pipeline import admit_pack
    from veloxa.releases import Pack, Schedule

    sched = Schedule("conflicts", IST, (Pack("D1", 1, "Day1_Mon_16Nov", D1_AT), Pack("D2", 2, "Day2_Tue_17Nov", D2_AT)))
    make_d1(tmp_path)
    d2 = tmp_path / "Day2_Tue_17Nov"
    make_board_update(d2, [("ARR (recurring)", "Rs. 3.7 Cr")], "Board_Q1.pdf")
    make_transcript(d2, "T9_Synthetic_Call.docx", ["T9 synthetic call", 'ARR is "around ninety-one crore, maybe".'])
    for p, at in (("D1", D1_DAY), ("D2", D2_AT)):
        admit_pack(ingest_conn, tenant, sched, p, tmp_path, at)
    with connect(db.dsn("projection")) as prj:
        projector.build(prj, tenant)
    return tenant


def _basis(app_conn, tenant, mode=Mode.CURRENT, at=None):
    from veloxa.releases import current_time

    return resolve_basis(tenant, ledger.sealed_releases(app_conn, tenant), mode, at, IST, current_time())


def test_the_register_lists_each_disagreement_with_provenance_and_time(app_conn, two_batches):
    tenant = two_batches
    (c,) = conflicts.register(app_conn, _basis(app_conn, tenant), IST)
    assert c["status"] == "open" and c["metric_match"] == "possibly_same_metric" and len(c["conflict_id"]) == 16
    sides = {s["source"].split(" (")[0]: s for s in c["claims"]}
    board, call = sides["formal document"], sides["meeting summary"]
    assert board["provenance"]["batch_seq"] == 2 and board["provenance"]["source_path"].endswith("Board_Q1.pdf")
    assert call["provenance"]["batch_seq"] == 2 and call["provenance"]["source_path"].endswith(
        "T9_Synthetic_Call.docx")
    assert (board["time"]["valid_from"], board["time"]["valid_to"]) == ("2099-01-01", "2099-04-01")   # its period
    assert call["time"]["valid_from"] is None and call["time"]["recorded_day"] is None   # undated: nothing invented
    assert board["time"]["extracted_at"] and call["time"]["recorded_basis"]
    assert "value" in board and "chosen" not in c and "resolution" not in c   # no value is chosen
    again = conflicts.register(app_conn, _basis(app_conn, tenant), IST)
    assert [x["conflict_id"] for x in again] == [c["conflict_id"]]            # stable identity


def test_an_earlier_knowledge_point_reproduces_the_earlier_register(app_conn, two_batches):
    tenant = two_batches
    assert conflicts.register(app_conn, _basis(app_conn, tenant, Mode.KNOWN_AT, date(2026, 11, 16)), IST) == []
    assert len(conflicts.register(app_conn, _basis(app_conn, tenant), IST)) == 1


def test_the_api_serves_the_register_at_a_basis_and_fails_closed(db, monkeypatch, tmp_path):
    from conftest import DEV_TOKEN, api_client, dev_fixture, ensure_configured_d1

    ensure_configured_d1(db, tmp_path)
    with api_client(db, monkeypatch) as anonymous:
        assert anonymous.get("/v1/conflicts").status_code == 401
    with api_client(db, monkeypatch, **dev_fixture()) as client:
        r = client.get("/v1/conflicts", headers={"Authorization": f"Bearer {DEV_TOKEN}"})
        assert r.status_code == 200
        body = r.json()
        assert body["register_version"] == "conflicts/1" and body["read_basis"]["batch_seq"] >= 1
        assert isinstance(body["conflicts"], list)
        bad = client.get("/v1/conflicts?mode=VALID_AT&as_of=2026-11-10", headers={"Authorization":
                                                                                  f"Bearer {DEV_TOKEN}"})
        assert bad.status_code == 200 and bad.json()["read_basis"]["mode"].endswith("VALID_AT")   # never a 500
