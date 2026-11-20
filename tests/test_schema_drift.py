"""Regression: a database one migration behind the code (the Demo Lab at 0020) must never produce anonymous 500s.

Found on the running Demo Lab, 18 Nov 2026: `veloxa_demolab` was at 0020_product_records while the code needs
0021_company_intelligence. Every page touching the new tables failed with `UndefinedTable` or `InsufficientPrivilege`
(HTTP 500): Overview "What to worry about", the executive brief, People, findings, the Conflict Registry,
connectors, and EVERY Ask question (Departments page included), because Ask's optional specialist lookup read
`runtime.agent_person`.

Expected now:
- feature endpoints answer 503 `schema_out_of_date` naming the migration (never an empty list, never invented data);
- authentication still comes first (401 before any schema state is revealed);
- Ask answers from the governed metrics and states that specialist routing is unavailable;
- /health reports the missing feature;
- on a current database every one of those endpoints answers 200.
"""

import uuid

import conftest
import pytest
from conftest import ADMIN_DSN, DEV_TOKEN, api_client, dev_fixture, ensure_configured_d1

FEATURE_ROUTES = ["/v1/worry", "/v1/people", "/v1/findings", "/v1/knowledge/conflicts", "/v1/admin/connectors",
                  "/v1/brief"]
AUTH = {"Authorization": f"Bearer {DEV_TOKEN}"}


@pytest.fixture(scope="module")
def db_at_0020():
    if not ADMIN_DSN:
        pytest.skip("VELOXA_TEST_ADMIN_DSN not set: database tests skipped")
    import psycopg

    from veloxa import migrate as m

    name = f"veloxa_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    tdb = conftest.TestDatabase(ADMIN_DSN, name)
    real = m._sql_files
    try:
        m._sql_files = lambda: [f for f in real() if f[0] < "0021"]          # stop at 0020, as the Demo Lab was
        m.migrate(tdb.admin_dsn, tdb.dsn("migrator"), name)
        yield tdb
    finally:
        m._sql_files = real
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture(scope="module")
def old_data(db_at_0020, tmp_path_factory, monkeypatch_module):
    # the 0020-era projector wrote no claims; the current one needs 0021 for them (a privileged process that
    # fails loudly, never a user-facing 500), so admission here uses the projector as it was
    from veloxa import knowledge

    monkeypatch_module.setattr(knowledge, "build_batch", lambda *a, **k: 0)
    ensure_configured_d1(db_at_0020, tmp_path_factory.mktemp("d1"))
    return db_at_0020


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def test_the_catalogue_check_names_what_is_missing(old_data, db):
    from veloxa import schema
    from veloxa.db import connect

    with connect(old_data.dsn("app")) as conn:
        missing = schema.missing(conn)
        assert [m["feature"] for m in missing] == ["company_intelligence", "finding_workflow", "source_identity",
                                                   "single_approver"]
        assert missing[0]["migration"] == "0021_company_intelligence"
        assert "runtime.agent_person" in missing[0]["relations"]
        with pytest.raises(schema.SchemaOutOfDate):
            schema.require(conn, "company_intelligence")
    with connect(db.dsn("app")) as conn:                     # the fully migrated session database
        assert schema.missing(conn) == [] and schema.available(conn, "company_intelligence")


@pytest.mark.parametrize("route", FEATURE_ROUTES)
def test_feature_routes_on_an_old_schema_are_an_explicit_503_not_a_500(old_data, monkeypatch, route):
    with api_client(old_data, monkeypatch, **dev_fixture("T001")) as client:
        assert client.get(route).status_code == 401                       # authentication still comes first
        r = client.get(route, headers=AUTH)
    assert r.status_code == 503, r.text
    body = r.json()
    assert body["error"] == "schema_out_of_date" and body["migration"] == "0021_company_intelligence"
    assert "operator" in body["detail"] and "people" not in body                 # no empty or invented payload


def test_ask_answers_on_an_old_schema_and_says_specialist_routing_is_unavailable(old_data, monkeypatch):
    with api_client(old_data, monkeypatch, **dev_fixture("T001")) as client:
        r = client.post("/v1/ask", json={"question": "What is our headcount by department?"}, headers=AUTH)
        refused = client.post("/v1/ask", json={"question": "What is Person Beta's salary?"}, headers=AUTH)
        health = client.get("/health").json()
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["status"] in ("answered", "qualified") and a["claims"] and a["evidence"]
    assert a["specialists"] == [] and "0021_company_intelligence" in a["diagnostics"]["specialists"]
    assert refused.status_code == 200 and refused.json()["status"] == "refused"        # refusals unchanged
    assert health["status"] == "degraded" and health["schema"]["current"] is False
    assert health["schema"]["missing"][0]["migration"] == "0021_company_intelligence"


def test_on_a_current_schema_every_feature_route_and_ask_succeed(db, tmp_path, monkeypatch):
    ensure_configured_d1(db, tmp_path)
    with api_client(db, monkeypatch, **dev_fixture("T001")) as client:
        for route in FEATURE_ROUTES:
            r = client.get(route, headers=AUTH)
            assert r.status_code == 200, (route, r.text)
        a = client.post("/v1/ask", json={"question": "What is our headcount by department?"}, headers=AUTH).json()
        health = client.get("/health").json()
    assert a["status"] in ("answered", "qualified") and "specialists" not in a["diagnostics"]
    assert health["schema"] == {"current": True, "missing": []}
