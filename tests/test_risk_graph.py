"""Evidence dependency graph and early warnings (Sprint 8). SYNTHETIC packs only (Orion: dated in D1, postponed
without a new date in D2). Never ranked, scored or given a probability; only visible evidence."""

from datetime import datetime

import pytest
from test_signals_db import IST, _basis, packs  # noqa: F401  (fixture re-export)

from veloxa.risk import graph
from veloxa.temporal import Mode

pytestmark = pytest.mark.db
BETWEEN = datetime.fromisoformat("2026-11-16T08:30:00+05:30")      # after D1's release, before D2's


def _warnings(g, name):
    return {w["kind"]: w for w in g["warnings"] if w["commitment"] == name}


def test_the_graph_links_commitments_to_their_visible_records(app_conn, packs):  # noqa: F811
    g = graph.build(app_conn, _basis(app_conn, packs, Mode.KNOWN_AT, BETWEEN), IST)
    ids = {n["id"] for n in g["nodes"]}
    assert "commitment:Orion" in ids
    stated = [e for e in g["edges"] if e["relation"] == "stated_in" and e["from"] == "commitment:Orion"]
    assert stated and all(e["to"] in ids for e in stated)
    assert any(e["stated_date"] == "2026-06-15" for e in stated)
    assert all(n["data_class"] not in graph.HIDDEN for n in g["nodes"] if n["type"] == "record")
    assert g["ranked"] is False and g["probability"] is None
    assert "moved_without_replacement_date" not in _warnings(g, "Orion")      # D2 not yet known


def test_a_later_move_without_a_date_is_an_early_warning_with_its_evidence(app_conn, packs):  # noqa: F811
    g = graph.build(app_conn, _basis(app_conn, packs), IST)
    w = _warnings(g, "Orion")["moved_without_replacement_date"]
    assert w["evidence"] and "2026-06-15" in w["statement"] and "no replacement date" in w["statement"]
    assert all(isinstance(r, str) for r in w["evidence"])


def test_the_graph_is_deterministic_and_reproducible_per_knowledge_point(app_conn, packs):  # noqa: F811
    b = _basis(app_conn, packs)
    assert graph.build(app_conn, b, IST) == graph.build(app_conn, b, IST)


def test_signals_with_evidence_not_visible_at_the_basis_are_dropped(app_conn, packs, monkeypatch):  # noqa: F811
    monkeypatch.setattr(graph.tools, "_evidence", lambda conn, basis, ids: [])        # nothing visible
    g = graph.build(app_conn, _basis(app_conn, packs), IST)
    assert g["nodes"] == [] and g["warnings"] == [] and any("dropped" in n for n in g["notes"])


def test_the_dependencies_api_fails_closed(db, monkeypatch, tmp_path):
    from conftest import DEV_TOKEN, api_client, dev_fixture, ensure_configured_d1

    ensure_configured_d1(db, tmp_path)
    with api_client(db, monkeypatch) as anon:
        assert anon.get("/v1/dependencies").status_code == 401
    with api_client(db, monkeypatch, **dev_fixture()) as c:
        r = c.get("/v1/dependencies", headers={"Authorization": f"Bearer {DEV_TOKEN}"})
        assert r.status_code == 200 and r.json()["ranked"] is False
        assert c.get("/v1/dependencies?as_known_at=not-a-date",
                     headers={"Authorization": f"Bearer {DEV_TOKEN}"}).status_code == 422
