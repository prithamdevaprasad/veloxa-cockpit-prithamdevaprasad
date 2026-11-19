"""Orbitra (Phase 5): an internally consistent, clearly synthetic company, isolated from the supplied company."""

import importlib.util
from pathlib import Path

import openpyxl
import pytest
from synthetic_company import conversations, orbitra

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("orbitra")
    return out, orbitra.generate(out)


def rows(path):
    ws = openpyxl.load_workbook(path, read_only=True).active
    it = ws.iter_rows(values_only=True)
    title, _blank, header = next(it), next(it), next(it)
    return title[0], header, [r for r in it if any(r)]


def test_people_are_consistent_and_within_range(built):
    out, m = built
    title, header, ppl = rows(out / "hr_directory" / "Employee_Directory.xlsx")
    assert "SYNTHETIC" in title and 100 <= len(ppl) <= 300
    ids = {r[0] for r in ppl}
    assert len(ids) == len(ppl) and len({r[8] for r in ppl}) == len(ppl)
    assert all(r[4] in ids for r in ppl if r[4]) and all(r[8].endswith("@orbitra.example") for r in ppl)
    assert sum(r[3] == "Chief Executive Officer" for r in ppl) == 1
    assert m["facts"]["ceo"] != m["facts"]["engineering_head"]
    assert all(r[7] for r in ppl if r[6] in ("Notice period", "Exited"))           # exits carry a date


def test_every_sheet_is_marked_synthetic_and_scenarios_are_present(built):
    out, m = built
    for f in out.rglob("*.xlsx"):
        assert "SYNTHETIC" in rows(f)[0], f.name
    f = m["facts"]
    assert f["delayed_from"] < f["delayed_to"] and f["missed_committed"] < f["missed_actual"]
    assert f["arr_sep_board"] != f["arr_sep_export"]                                # financial disagreement
    assert f["blocked"] > 0 and f["on_notice"] > 0 and f["last_headcount_snapshot"] == "2026-08"
    _t, _h, issues = rows(out / "issues" / "Issues.xlsx")
    assert {r[7] for r in issues if r[4] == "Blocked"} == {"ORQ-001 (paused hiring)", "Vendor delivery ODL-004"}
    assert not any("lynx" in str(r).lower() for r in issues)                     # missing evidence by design


def test_no_supplied_company_identifiers_leak_into_the_synthetic_company(built):
    out, _ = built
    text = " ".join(str(c) for f in out.rglob("*.xlsx") for r in openpyxl.load_workbook(f, read_only=True).active
                    .iter_rows(values_only=True) for c in r if c)
    for real in ("Atlas", "Veloxa", "veloxa.in", "VX0", "Keka", "Zoho"):
        assert real not in text, real


def test_the_conversation_suite_has_at_least_100_cases_in_every_category(built):
    _, m = built
    cs = conversations.cases(m["facts"])
    cats = {c["category"] for c in cs}
    assert len(cs) >= 100 and len({c["id"] for c in cs}) == len(cs)
    assert {"greeting", "follow_up", "business", "conflict", "privacy", "missing", "historical", "jira"} <= cats


def test_the_lab_refuses_every_database_but_its_own():
    spec = importlib.util.spec_from_file_location("synthetic_lab", ROOT / "scripts" / "synthetic_lab.py")
    lab = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lab)
    for bad in ("veloxa", "veloxa_company_v3", "veloxa_demolab", "veloxa_test_x", "veloxa_synthco_TOO_LONG_NAME_1"):
        with pytest.raises(SystemExit):
            lab.check_name(bad)
    lab.check_name("veloxa_synthco")
    lab.check_name("veloxa_synthco_v3")
