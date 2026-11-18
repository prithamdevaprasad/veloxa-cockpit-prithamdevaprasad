"""Demo Lab (Profile B): build the fictional company through the real data room in a disposable test database,
check integrity and isolation, then run the expanded synthetic benchmark (tests/synthetic_company/gold_b.py).
Profile A (the 41 original files and the original 30 gold questions) is untouched and runs separately."""

import json
import sys
import uuid
from pathlib import Path

import pytest

from veloxa.db import connect
from veloxa.storage import LocalContentStore

pytestmark = pytest.mark.db
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


@pytest.fixture(scope="module")
def lab(db, tmp_path_factory):
    import demo_lab

    root = tmp_path_factory.mktemp("lab")
    dsns = {"admin": db.admin_dsn, "app": db.dsn("app"), "ingest": db.dsn("ingest"),
            "projection": db.dsn("projection")}
    summary = demo_lab.populate(dsns, LocalContentStore(root / "originals"), root / "files",
                                slug=f"demo-{uuid.uuid4().hex[:6]}")
    return summary | {"dsns": dsns}


def test_the_allowlist_refuses_every_other_database():
    import demo_lab

    for bad in ("veloxa", "veloxa_test_542ed2bea1", "postgres", "veloxa_demolab; drop", "Veloxa_demolab"):
        with pytest.raises(SystemExit):
            demo_lab.check_name(bad)
    demo_lab.check_name("veloxa_demolab")
    demo_lab.check_name("veloxa_demolab_ci1")


def test_every_file_was_admitted_through_the_data_room_and_is_marked_synthetic(lab):
    import uuid as _u

    tid = _u.UUID(lab["tenant_id"])
    with connect(lab["dsns"]["admin"]) as a:
        states = a.execute("SELECT state, count(*) AS n FROM (SELECT DISTINCT ON (submission_id) state FROM"
                           " intake.submission_event WHERE tenant_id = %s ORDER BY submission_id, at DESC, event_id"
                           " DESC) s GROUP BY state", (tid,)).fetchall()
        batches = a.execute("SELECT count(*) AS n, bool_and(sealed) AS sealed FROM ledger.admission_batch"
                            " WHERE tenant_id = %s", (tid,)).fetchone()
        synthetic = a.execute("SELECT synthetic FROM directory.tenant WHERE tenant_id = %s", (tid,)).fetchone()
        rows = a.execute("SELECT count(*) AS n FROM ledger.source_record WHERE tenant_id = %s", (tid,)).fetchone()
        unmarked = a.execute("SELECT count(*) AS n FROM ledger.source_record WHERE tenant_id = %s AND kind = 'export'"
                             " AND system NOT IN ('keka_hrms', 'zoho_books') AND NOT (payload::text ILIKE"
                             " '%%SYNTHETIC%%')", (tid,)).fetchone()
    assert {r["state"]: r["n"] for r in states} == {"projected": lab["files"]}
    assert batches["n"] == lab["files"] and batches["sealed"] and synthetic["synthetic"]
    assert rows["n"] > 800 and unmarked["n"] == 0
    assert lab["agents"] == 14 and lab["updates"] >= 7


def test_finance_reconciles_and_no_individual_pay_exists(lab):
    import uuid as _u

    tid = _u.UUID(lab["tenant_id"])
    with connect(lab["dsns"]["admin"]) as a:
        gl = a.execute("SELECT sum((payload->>'Amount (Rs.)')::numeric) AS s FROM ledger.source_record"
                       " WHERE tenant_id = %s AND system = 'zoho_books'", (tid,)).fetchone()["s"]
        bva = a.execute("SELECT count(*) AS n FROM ledger.source_record WHERE tenant_id = %s AND system = 'fp_and_a'"
                        " AND (payload->>'Actual')::numeric - (payload->>'Budget')::numeric"
                        " <> (payload->>'Variance')::numeric", (tid,)).fetchone()["n"]
        pay = a.execute("SELECT count(*) AS n FROM ledger.source_record WHERE tenant_id = %s AND (payload::text"
                        " ILIKE '%%salary%%' OR payload::text ILIKE '%%gross (rs.)%%' OR data_class IN"
                        " ('sensitive_personal', 'payroll_aggregate'))", (tid,)).fetchone()["n"]
    assert gl == lab["facts"]["ledger_total"] and bva == 0 and pay == 0


def test_the_expanded_synthetic_benchmark(lab):
    from synthetic_company import gold_b

    report = gold_b.run(lab)
    print("PROFILE_B_REPORT " + json.dumps(report["metrics"]))
    assert len(report["cases"]) >= 75
    m = report["metrics"]
    assert m["citation_precision"] == 1.0                 # every cited record exists, is readable and verified
    assert m["refusal_correctness"] == 1.0 and m["access_control_correctness"] == 1.0
    assert m["temporal_correctness"] == 1.0


def _client(lab, monkeypatch, principal):
    from conftest import DEV_TOKEN
    from fastapi.testclient import TestClient

    from veloxa.api import PRIVILEGED_ENV, create_app

    for n in PRIVILEGED_ENV + ("VELOXA_OIDC",):
        monkeypatch.delenv(n, raising=False)
    for k, v in {"VELOXA_DEV_AUTH": "1", "VELOXA_DEV_TOKEN": DEV_TOKEN, "VELOXA_DEV_PRINCIPAL": principal,
                 "VELOXA_DEV_TENANT": lab["slug"]}.items():
        monkeypatch.setenv(k, v)
    return TestClient(create_app(dsn=lab["dsns"]["app"])), {"Authorization": f"Bearer {DEV_TOKEN}"}


def test_overview_kpis_and_series_come_from_admitted_records_only(lab, monkeypatch):
    c, h = _client(lab, monkeypatch, "person:demo-ceo")
    with c:
        o = c.get("/v1/overview", headers=h).json()
        keys = {k["key"]: k for k in o["kpis"]}
        assert keys["headcount"]["value"] == lab["facts"]["headcount_total"] and o["synthetic"] is True
        arr = next(v for k, v in keys.items() if k.startswith("reported:arr"))
        assert arr["basis"].startswith("as reported for") and arr["evidence"]
        rev = c.get("/v1/analytics/series", headers=h,
                    params={"system": "revenue_reporting", "x": "Month", "agg": "sum", "y": "ARR"}).json()
        assert len(rev["points"]) == 12 and rev["points"][-1]["value"] == lab["facts"]["arr_dec"]
        bva = c.get("/v1/analytics/series", headers=h, params={"system": "fp_and_a", "x": "Month", "agg": "sum",
                                                                "y": "Variance"}).json()
        assert len(bva["points"]) == 12 and bva["records"] == 84
        assert c.get("/v1/analytics/series", headers=h,
                     params={"system": "keka_hrms", "x": "Department"}).status_code == 403   # personal rows
        assert {s["system"] for s in c.get("/v1/analytics/sources", headers=h).json()["sources"]} >= {
            "revenue_reporting", "crm", "issue_tracker"}
    c, h = _client(lab, monkeypatch, "person:demo-outsider")
    with c:
        assert [k["key"] for k in c.get("/v1/overview", headers=h).json()["kpis"]] == ["coverage"]
        assert c.get("/v1/analytics/series", headers=h,
                     params={"system": "revenue_reporting", "x": "Month"}).status_code == 403
        assert c.get("/v1/analytics/sources", headers=h).json()["sources"] == []
