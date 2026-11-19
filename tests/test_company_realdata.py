"""The REAL release packs, admitted through the governed pipeline into a disposable database (local only).

Needs VELOXA_DATA_PATH and VELOXA_TEST_ADMIN_DSN; never runs in CI (the data is never in the repository). Asserts
counts, statuses and knowledge points only; it never prints or compares record values.
"""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

pytestmark = [pytest.mark.db, pytest.mark.realdata]
DATA = os.environ.get("VELOXA_DATA_PATH")


@pytest.fixture(scope="module")
def company(db):
    if not DATA or not (Path(DATA) / "Day3_Wed_18Nov").is_dir():
        pytest.skip("VELOXA_DATA_PATH with the Day folders not available (expected outside CI)")
    from veloxa import config, projector
    from veloxa.agents import people, registry
    from veloxa.db import connect
    from veloxa.ingest.pipeline import admit_pack
    from veloxa.releases import AdmissionRefused, load_authorisations, load_schedule

    schedule, auth, now = load_schedule(), load_authorisations(), datetime.now(UTC)
    tid = config.tenant_uuid(schedule.tenant_slug)
    results, d4 = {}, None
    with connect(db.dsn("ingest")) as conn:
        for p in schedule.packs:
            if p.release_at > now or p.id not in auth:
                try:
                    admit_pack(conn, tid, schedule, p.id, Path(DATA), now, authorisation=auth.get(p.id))
                except AdmissionRefused as e:
                    d4 = e.reason
                break
            results[p.id] = admit_pack(conn, tid, schedule, p.id, Path(DATA), now, authorisation=auth[p.id])
    with connect(db.dsn("projection")) as conn:
        projector.build(conn, tid)
    with connect(db.admin_dsn) as a:
        registry.seed_slots(a, tid)
        grounded = people.ground(a, tid, max(r.batch_seq for r in results.values()))
    return {"tid": tid, "results": results, "d4": d4, "grounded": grounded}


def test_released_and_authorised_day_folders_are_admitted_source_by_source(company):
    r = company["results"]
    assert [p for p in r] == ["D1", "D2", "D3", "D4"][:len(r)] and len(r) >= 3     # D4 only after its release
    assert (r["D2"].file_count, r["D3"].file_count) == (10, 14)
    if datetime.now(UTC) < datetime.fromisoformat("2026-11-19T10:00:00+05:30"):
        assert company["d4"] in ("not_yet_released", "not_authorised")      # D4 embargoed: never opened


def test_exactly_fourteen_agents_are_grounded_in_hr_records(company):
    assert len(company["grounded"]) == 14


def test_the_conflict_registry_and_knowledge_points_respect_release_times(db, company):
    from veloxa import knowledge
    from veloxa.db import connect, tenant_tx

    with connect(db.admin_dsn) as c, tenant_tx(c, company["tid"]):
        at = {k: len(knowledge.conflicts_at(c, company["tid"], k)) for k in (1, 2, 3)}
        d3_claims_at_2 = c.execute("SELECT count(*) AS n FROM knowledge.claim WHERE batch_seq <= 2"
                                   " AND subject_type IN ('issue', 'rota_week', 'incident')").fetchone()["n"]
        d3_claims_at_3 = c.execute("SELECT count(*) AS n FROM knowledge.claim WHERE batch_seq <= 3"
                                   " AND subject_type IN ('issue', 'rota_week', 'incident')").fetchone()["n"]
    assert at[3] >= 1 and all(v >= 1 for v in at.values())          # the board-vs-spoken figure disagreement
    assert d3_claims_at_2 == 0 and d3_claims_at_3 > 0                # 17 Nov knows nothing from the 18 Nov pack


def test_point_in_time_answers_on_17_and_18_november(db, company, monkeypatch):
    from fastapi.testclient import TestClient

    from veloxa.api import PRIVILEGED_ENV, create_app
    from veloxa.db import connect, tenant_tx

    with connect(db.admin_dsn) as c, tenant_tx(c, company["tid"]):
        ceo = c.execute("SELECT emp_id FROM projection.person WHERE title = 'CEO' LIMIT 1").fetchone()["emp_id"]
    for k in PRIVILEGED_ENV:
        monkeypatch.delenv(k, raising=False)
    tok = "realdata-local-fixture-token-0123456789abcdef"
    for k, v in {"VELOXA_DEV_AUTH": "1", "VELOXA_DEV_TOKEN": tok, "VELOXA_DEV_PRINCIPAL": ceo,
                 "VELOXA_RATE_LIMIT_EXPENSIVE_PER_MIN": "600"}.items():
        monkeypatch.setenv(k, v)
    with TestClient(create_app(dsn=db.dsn("app"))) as client:
        h = {"Authorization": f"Bearer {tok}"}
        k = {d: client.post("/v1/ask", json={"question": "What is our headcount?", "as_known_at": d},
                            headers=h).json()["read_basis"]["batch_seq"] for d in ("2026-11-17", "2026-11-18")}
        assert k == {"2026-11-17": 2, "2026-11-18": 3}
        cro17 = client.post("/v1/ask", json={"question": "Who will marketing report to?",
                                             "as_known_at": "2026-11-17"}, headers=h).json()
        assert not any("T6b" in (e.get("source_path") or "") for e in cro17["evidence"])   # T6b released 18 Nov
