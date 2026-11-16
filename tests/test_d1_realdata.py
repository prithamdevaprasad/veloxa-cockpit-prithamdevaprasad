"""Checks against the REAL Day 1 release pack. Local only: needs VELOXA_DATA_PATH and a database.

Never runs in CI (the data is never in the repository). Asserts counts, classes and hashes only;
it never prints or compares record values.
"""

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

import veloxa.ingest.pipeline as pipeline
from veloxa import ledger
from veloxa.db import tenant_tx
from veloxa.releases import AdmissionRefused

pytestmark = [pytest.mark.db, pytest.mark.realdata]

DATA = os.environ.get("VELOXA_DATA_PATH")
EXPECTED_FILES = 15
EXPECTED_RECORDS_BY_SYSTEM = {
    "challenge_brief": 1, "data_room": 1, "documents": 2, "email": 3, "meeting_summaries": 2,
    "keka_hrms": 19, "payroll_register": 56, "zoho_books": 28,
}


@pytest.fixture
def data_root():
    if not DATA or not (Path(DATA) / "Day1_Mon_16Nov").is_dir():
        pytest.skip("VELOXA_DATA_PATH with Day1_Mon_16Nov not available (expected outside CI)")
    return Path(DATA)


def test_real_d1_admission(ingest_conn, app_conn, tenant, schedule, data_root, monkeypatch):
    opened = []
    real_build = pipeline.build_manifest

    def spy(pack_dir, rules):
        opened.append(pack_dir.name)
        return real_build(pack_dir, rules)

    monkeypatch.setattr(pipeline, "build_manifest", spy)
    now = datetime.now(UTC)
    result = pipeline.admit_pack(ingest_conn, tenant, schedule, "D1", data_root, now)
    assert result.file_count == EXPECTED_FILES
    assert result.record_count == sum(EXPECTED_RECORDS_BY_SYSTEM.values())

    with tenant_tx(ingest_conn, tenant):
        by_system = {r["system"]: r["n"] for r in ingest_conn.execute(
            "SELECT system, count(*) AS n FROM ledger.source_record GROUP BY system").fetchall()}
        by_class = {r["data_class"]: r["n"] for r in ingest_conn.execute(
            "SELECT data_class, count(*) AS n FROM ledger.source_record GROUP BY data_class").fetchall()}
        raws = ingest_conn.execute(
            "SELECT source_path, content_sha256, record_mode FROM raw_index.raw_object").fetchall()
    assert by_system == EXPECTED_RECORDS_BY_SYSTEM
    assert by_class["sensitive_personal"] == 56 and by_class["payroll_aggregate"] == 6
    assert sum(r["record_mode"] == "none" for r in raws) == 3  # templates registered, never admitted
    for raw in raws:  # provenance: stored hash equals the bytes of the released file
        f = data_root / "Day1_Mon_16Nov" / raw["source_path"]
        assert raw["content_sha256"] == hashlib.sha256(f.read_bytes()).hexdigest()

    # The app role sees neither individual payroll lines (56) nor payroll aggregates (6).
    assert len(ledger.visible_record_ids(app_conn, tenant, 1)) == result.record_count - 56 - 6

    # Times stay distinct and ordered: release < extraction <= admission <= seal.
    with tenant_tx(ingest_conn, tenant):
        b = ingest_conn.execute("SELECT release_at, admitted_at, sealed_at FROM ledger.admission_batch").fetchone()
        e = ingest_conn.execute(
            "SELECT min(extracted_at) AS first, max(extracted_at) AS last FROM ledger.source_record").fetchone()
    assert b["release_at"] < e["first"] <= e["last"] <= b["admitted_at"] <= b["sealed_at"]

    # Later packs are refused today, before their folders are opened.
    if now < schedule.pack("D2").release_at:
        with pytest.raises(AdmissionRefused) as exc:
            pipeline.admit_pack(ingest_conn, tenant, schedule, "D2", data_root, now)
        assert exc.value.reason == "not_yet_released"
    assert opened == ["Day1_Mon_16Nov"]


def test_real_d1_projections_metrics_and_tools(db, ingest_conn, app_conn, tenant, schedule, data_root):
    """Counts and structure only; no value is printed or compared."""
    from datetime import UTC, datetime

    from veloxa import metrics, projector, tools
    from veloxa.db import connect
    from veloxa.temporal import Mode, resolve_basis

    pipeline.admit_pack(ingest_conn, tenant, schedule, "D1", data_root, datetime.now(UTC))
    with connect(db.dsn("projection")) as prj:
        assert [k for k, _ in projector.build(prj, tenant)] == [1]
        with tenant_tx(prj, tenant):
            n = {t: prj.execute(f"SELECT count(*) AS n FROM projection.{t}").fetchone()["n"]
                 for t in ("person", "ledger_line", "document_text", "reported_figure")}
            boards = prj.execute(
                "SELECT count(DISTINCT source_record_id) AS n, count(*) FILTER"
                " (WHERE issued_on IS NULL OR period IS NULL) AS undated FROM projection.reported_figure").fetchone()
    assert n == {"person": 19, "ledger_line": 28, "document_text": 9, "reported_figure": 10}
    assert boards["n"] == 2 and boards["undated"] == 0  # both board updates parsed, each with period and date

    with tenant_tx(app_conn, tenant):
        visible = app_conn.execute("SELECT count(*) AS n FROM projection.ledger_line").fetchone()["n"]
    assert visible == 22  # the 6 payroll lines are hidden from the app role
    basis = resolve_basis(tenant, ledger.sealed_releases(app_conn, tenant), Mode.CURRENT, None,
                          "Asia/Kolkata", datetime.now(UTC))
    defs = metrics.load_definitions()
    assert [r.value for r in metrics.run(app_conn, defs["headcount"], basis).rows] == [19]
    assert len(metrics.run(app_conn, defs["ledger_spend"], basis).rows[0].inputs) == 22

    ceo = tools.resolve_principal_by_title(app_conn, basis, "CEO")
    assert ceo is not None and ceo.email
    found = tools.text_search(app_conn, basis, ceo, "payroll OR payslip", limit=50)
    assert not any(e.kind == "email" for e in found.evidence)  # the CEO is not a party to the D1 emails
    assert tools.payroll_total(app_conn, basis, ceo).status == "withheld"


def test_real_d1_ask_v0(db, ingest_conn, app_conn, tenant, schedule, data_root):
    """/v1/ask pipeline over the real D1 pack. Structure only: statuses, grades, policies and consistency with the
    governed metric; no record value is printed or compared with a literal."""
    from datetime import UTC, datetime

    from veloxa import metrics, projector, tools
    from veloxa.ask import pipeline as ask
    from veloxa.db import connect
    from veloxa.temporal import Mode, resolve_basis

    now = datetime.now(UTC)
    pipeline.admit_pack(ingest_conn, tenant, schedule, "D1", data_root, now)
    with connect(db.dsn("projection")) as prj:
        projector.build(prj, tenant)
    basis = resolve_basis(tenant, ledger.sealed_releases(app_conn, tenant), Mode.CURRENT, None, "Asia/Kolkata", now)
    ceo = tools.resolve_principal_by_title(app_conn, basis, "CEO")

    def q(text, **kw):
        return ask.answer(app_conn, tenant, "Asia/Kolkata", ceo, text, now, **kw)

    head = q("What is our headcount?")
    metric = metrics.run(app_conn, metrics.load_definitions()["headcount"], basis).rows[0].value
    fact = next(f for f in head["facts"] if f["fact_id"] == head["claims"][0]["evidence"][0])
    assert head["status"] == "qualified" and head["grade"]["value"] == "A" and fact["value"] == metric
    assert q("What is the total payroll?")["refusal"]["policy"] == "POL-PAY-02"
    assert q("Rank the team by attrition risk")["refusal"]["policy"] == "POL-PEOPLE-01"
    assert q("What did we know on 15 Nov 2026?", as_known_at="2026-11-15")["status"] == "abstained"
    for text in ("What is the ledger spend by cost centre?", "What did the board updates report?",
                 "What happened recently?", "Who does the CEO report to?"):
        a = q(text)
        assert a["status"] in ("qualified", "abstained")
        assert all(c["verification"] in ("supported", "downgraded") for c in a["claims"])
        assert all(e["batch_seq"] == 1 and e["data_class"] not in ("sensitive_personal", "payroll_aggregate")
                   for e in a["evidence"])
        assert not any(e["kind"] == "email" for e in a["evidence"])  # the CEO is not a party to the D1 emails


def test_real_d1_briefing(db, ingest_conn, app_conn, tenant, schedule, data_root):
    """Interim briefing over the real D1 pack. With no owner-approved figure pairing and no other detector, the
    honest result is an empty watchlist: nothing is invented to fill it. Structure only; no value compared."""
    from datetime import UTC, datetime

    from veloxa import projector, tools
    from veloxa.db import connect
    from veloxa.risk import briefing
    from veloxa.temporal import Mode, resolve_basis

    now = datetime.now(UTC)
    pipeline.admit_pack(ingest_conn, tenant, schedule, "D1", data_root, now)
    with connect(db.dsn("projection")) as prj:
        projector.build(prj, tenant)
    basis = resolve_basis(tenant, ledger.sealed_releases(app_conn, tenant), Mode.CURRENT, None, "Asia/Kolkata", now)
    ceo = tools.resolve_principal_by_title(app_conn, basis, "CEO")  # test-only lookup; the API never uses titles
    b = briefing.serve(app_conn, tenant, ceo, basis, now)
    assert b["read_basis"]["batch_seq"] == 1 and b["owner_decision_status"] == "D-15 pending"
    assert b["top_three"] is None and b["ranking_mode"] == "unweighted_partial_order"
    assert b["watchlist"] == [] and b["comparisons"] == []
    assert any("no owner-approved pairing" in n for n in b["signals_needing_data"])
