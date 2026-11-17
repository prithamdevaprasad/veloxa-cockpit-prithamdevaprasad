"""Projections, governed metrics and declared query tools (ARCHITECTURE.md §6.2, §11, §13). SYNTHETIC data only."""

from datetime import date, datetime, timedelta

import pytest
from conftest import D1_DAY

from veloxa import metrics, projector, tools
from veloxa.db import connect, tenant_tx
from veloxa.temporal import Mode, resolve_basis

IST = "Asia/Kolkata"

BOARD_TEXT = [
    "Company: Board update\nQuarterly update. Period: Jan to Mar 2099. Sent 9 Apr 2099. Confidential.\n"
    "Key numbers\nMetric Value\nRevenue (recurring) Rs. 3.7 Cr\nCustomers 12\nRunway 9 months\nProduct\nAll good.\n"
]


def test_reported_figures_are_read_exactly_as_written_and_the_table_ends_at_the_next_heading():
    period, issued, figures = projector.parse_reported_figures(BOARD_TEXT)
    assert period == "Jan to Mar 2099" and issued == date(2099, 4, 9)
    assert figures == [("Revenue (recurring)", "Rs. 3.7 Cr"), ("Customers", "12"), ("Runway", "9 months")]


def test_a_document_without_a_key_numbers_table_yields_no_figures():
    assert projector.parse_reported_figures(["Just prose.\nNo table."])[2] == []


def test_metric_definitions_load_and_only_registered_sources_columns_and_measures_are_allowed(tmp_path):
    defs = metrics.load_definitions()
    assert {"headcount", "ledger_spend", "ledger_line_count", "reported_figure", "payroll_total"} <= set(defs)
    bad = tmp_path / "metrics.toml"
    for measure, source in (("sum:narration", "ledger_line"), ("sum:amount_inr", "nowhere"), ("avg:amount_inr",
                                                                                               "ledger_line")):
        bad.write_text(f'[[metric]]\nname="x"\nversion=1\ndescription="d"\nunit="u"\nsource="{source}"\n'
                       f'measure="{measure}"\n', encoding="utf-8")
        with pytest.raises(ValueError):
            metrics.load_definitions(bad)


def test_compiled_sql_contains_identifiers_and_placeholders_never_values():
    import uuid

    defs = metrics.load_definitions()
    basis = resolve_basis(uuid.uuid4(), [(1, D1_DAY - timedelta(hours=2))], Mode.CURRENT, None, IST, D1_DAY)
    query, params = metrics.compile_query(defs["ledger_spend"], basis, ["cost_centre"],
                                          {"vendor": "Robert'); DROP TABLE x; --"})
    text = query.as_string(None)
    assert "DROP TABLE" not in text and "%(f0)s" in text and params["f0"].startswith("Robert")
    with pytest.raises(metrics.MetricError):
        metrics.compile_query(defs["ledger_spend"], basis, ["narration"], {})  # undeclared dimension


# ---------------------------------------------------------------------------------------------------------------
# Database: projections, metrics and tools over a synthetic sealed batch
# ---------------------------------------------------------------------------------------------------------------

@pytest.fixture
def projected(db, ingest_conn, tenant, schedule, synthetic_root):
    from veloxa.ingest.pipeline import admit_pack

    admit_pack(ingest_conn, tenant, schedule, "D1", synthetic_root, D1_DAY)
    with connect(db.dsn("projection")) as conn:
        built = projector.build(conn, tenant)
    return built


def _basis(app_conn, tenant, mode=Mode.CURRENT, as_of=None):
    from veloxa import ledger
    from veloxa.releases import current_time

    return resolve_basis(tenant, ledger.sealed_releases(app_conn, tenant), mode, as_of, IST, current_time())


ALPHA = tools.Principal("person:T001", "executive", "alpha@example.test")
OUTSIDER = tools.Principal("person:T999", "executive", "nobody@example.test")


@pytest.mark.db
def test_projections_are_built_once_per_sealed_batch_and_are_rebuildable(db, tenant, projected):
    assert [k for k, _ in projected] == [1]
    with connect(db.dsn("projection")) as conn:
        assert projector.build(conn, tenant) == []  # already built: idempotent
        counts = {}
        with tenant_tx(conn, tenant):
            for t in ("person", "ledger_line", "document_text"):
                counts[t] = conn.execute(f"SELECT count(*) AS n FROM projection.{t}").fetchone()["n"]
            events = [e["kind"] for e in conn.execute("SELECT kind FROM audit.event ORDER BY seq")]
        assert counts == {"person": 2, "ledger_line": 3, "document_text": 4}  # brief, readme, email, transcript
        assert events == ["admission.sealed", "projection.built"]
        assert [k for k, _ in projector.rebuild(conn, tenant)] == [1]


@pytest.mark.db
def test_the_app_role_never_sees_payroll_lines_in_projections(app_conn, tenant, projected):
    with tenant_tx(app_conn, tenant):
        classes = {r["data_class"] for r in app_conn.execute("SELECT data_class FROM projection.ledger_line")}
    assert classes == {"confidential"}


@pytest.mark.db
def test_ledger_spend_comes_from_the_governed_metric_and_excludes_payroll(app_conn, tenant, projected):
    defs = metrics.load_definitions()
    basis = _basis(app_conn, tenant)
    total = metrics.run(app_conn, defs["ledger_spend"], basis)
    assert [r.value for r in total.rows] == [30] and len(total.rows[0].inputs) == 2
    by_cc = metrics.run(app_conn, defs["ledger_spend"], basis, ["cost_centre"])
    assert [(r.dims["cost_centre"], r.value) for r in by_cc.rows] == [("CC-A", 30)]
    assert "payroll lines excluded (disclosure-controlled)" in total.qualifiers


@pytest.mark.db
def test_headcount_and_the_payroll_total_fails_closed_without_the_disclosure_auditor(app_conn, tenant, projected):
    defs = metrics.load_definitions()
    basis = _basis(app_conn, tenant)
    assert [r.value for r in metrics.run(app_conn, defs["headcount"], basis).rows] == [2]
    with pytest.raises(metrics.MetricError) as exc:
        metrics.run(app_conn, defs["payroll_total"], basis)
    assert exc.value.reason == "disclosure_auditor_unavailable"


@pytest.mark.db
def test_metrics_respect_the_read_basis(app_conn, tenant, projected):
    defs = metrics.load_definitions()
    before = _basis(app_conn, tenant, Mode.KNOWN_AT, date(2026, 11, 15))
    assert metrics.run(app_conn, defs["headcount"], before).rows == []
    with pytest.raises(metrics.MetricError) as exc:
        metrics.run(app_conn, defs["headcount"], _basis(app_conn, tenant, Mode.RECORDED_BY, date(2026, 11, 15)))
    assert exc.value.reason == "unsupported_mode"


@pytest.mark.db
@pytest.mark.parametrize("name, status", [("Person Alpha", "ok"), ("T002", "ok"), ("person", "ambiguous"),
                                          ("Persn Alpha", "unresolved")])
def test_person_tool_never_guesses_an_identity(app_conn, tenant, projected, name, status):
    assert tools.person(app_conn, _basis(app_conn, tenant), ALPHA, name).status == status


@pytest.mark.db
def test_person_tool_returns_record_facts_with_exact_evidence(app_conn, tenant, projected):
    r = tools.person(app_conn, _basis(app_conn, tenant), ALPHA, "Person Beta")
    facts = {f.fact_id: f for f in r.facts}
    assert facts["person.manager"].value == "Person Alpha"
    assert {e.locator["row"] for e in r.evidence} == {4, 5}  # Beta's row and the manager's row
    assert all(e.data_class == "personal" for e in r.evidence)


@pytest.mark.db
def test_payroll_total_tool_is_withheld(app_conn, tenant, projected):
    r = tools.payroll_total(app_conn, _basis(app_conn, tenant), ALPHA)
    assert (r.status, r.reason, r.facts) == ("withheld", "disclosure_auditor_unavailable", [])


@pytest.mark.db
def test_text_search_shows_an_email_only_to_its_parties(app_conn, tenant, projected):
    basis = _basis(app_conn, tenant)
    seen_by_party = {e.record_id for e in tools.text_search(app_conn, basis, ALPHA, "synthetic").evidence}
    seen_by_outsider = {e.record_id for e in tools.text_search(app_conn, basis, OUTSIDER, "synthetic").evidence}
    email = "email:data_room/email/01.eml#document"
    assert email in seen_by_party and email not in seen_by_outsider
    assert seen_by_outsider  # non-private documents are still found


@pytest.mark.db
def test_timeline_cites_records_and_conflicts_reports_no_owner_records(app_conn, tenant, projected):
    basis = _basis(app_conn, tenant)
    t = tools.timeline(app_conn, basis, ALPHA)
    assert t.status == "ok" and all(f.inputs for f in t.facts)
    assert any("joined" in f.display for f in t.facts) and any("email" in f.display for f in t.facts)
    assert tools.timeline(app_conn, basis, OUTSIDER).facts != t.facts  # the outsider does not see the email
    c = tools.conflicts(app_conn, basis, ALPHA)
    assert c.status == "no_data" and c.facts == []


@pytest.mark.db
def test_tools_and_metrics_do_not_leak_later_material(db, app_conn, tenant, tmp_path):
    """Leakage canaries for the new surfaces across a real two-batch boundary."""
    import canary
    from synthetic_pack import make_d1
    from test_temporal import D1_CANARIES, D2_CANARIES, _make_synthetic_d2

    from veloxa import ledger
    from veloxa.ingest.pipeline import admit_pack
    from veloxa.releases import Pack, Schedule, current_time

    d1_at = datetime.fromisoformat("2026-11-16T08:00:00+05:30")
    d2_at = datetime.fromisoformat("2026-11-16T09:00:00+05:30")
    sched = Schedule("canary", IST, (Pack("D1", 1, "Day1_Mon_16Nov", d1_at), Pack("D2", 2, "Day2_Tue_17Nov", d2_at)))
    make_d1(tmp_path)
    _make_synthetic_d2(tmp_path)
    with connect(db.dsn("ingest")) as ing:
        for p in ("D1", "D2"):
            admit_pack(ing, tenant, sched, p, tmp_path, current_time())
    with connect(db.dsn("projection")) as prj:
        projector.build(prj, tenant)
    releases = ledger.sealed_releases(app_conn, tenant)
    now = current_time()
    bases = {k: resolve_basis(tenant, releases, Mode.KNOWN_AT, at, IST, now)
             for k, at in ((0, d1_at - timedelta(seconds=1)), (1, d2_at - timedelta(seconds=1)))}
    bases[2] = resolve_basis(tenant, releases, Mode.CURRENT, None, IST, now)
    surfaces = {
        "text_search": lambda c, b: {e.record_id for e in tools.text_search(c, b, ALPHA, "synthetic OR canary",
                                                                            limit=50).evidence},
        "timeline": lambda c, b: {i for f in tools.timeline(c, b, ALPHA).facts for i in f.inputs},
    }
    universe_d1 = {"email:data_room/email/01.eml#document"}
    report = canary.run(surfaces, app_conn, bases, {1: universe_d1 & D1_CANARIES, 2: D2_CANARIES})
    assert not report.leaks, report.leaks
