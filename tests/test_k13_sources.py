"""K-13 source-specific admission, CSV row exports, business keys and text-integer contracts (no database)."""

import tomllib
from pathlib import Path

import pytest

from veloxa.catalogue import load_catalogue
from veloxa.ingest.contracts import Contract, validate_rows
from veloxa.ingest.parsers import parse
from veloxa.releases import AdmissionRefused, Authorisation, load_authorisations

ROOT = Path(__file__).resolve().parents[1]


def test_non_baseline_pack_is_refused_for_any_file_without_its_own_approval():
    a = Authorisation("D9", False, "Owner", "2026-11-18", "test", ("data/a.xlsx", "mail/*.eml"))
    a.check_sources(["data/a.xlsx", "mail/01.eml"])
    with pytest.raises(AdmissionRefused) as e:
        a.check_sources(["data/a.xlsx", "data/b.xlsx"])
    assert e.value.reason == "source_not_authorised"


def test_a_pack_wide_authorisation_without_sources_is_rejected(tmp_path):
    p = tmp_path / "auth.toml"
    p.write_text('[[pack]]\nid = "D2"\napproved_by = "Owner"\napproved_on = 2026-11-18\ndecision_record = "x"\n')
    with pytest.raises(ValueError, match="source-specific"):
        load_authorisations(p)


def test_configured_d2_to_d4_authorisations_list_sources():
    auth = load_authorisations()
    assert auth["D1"].baseline
    assert len(auth["D2"].sources) == 10 and len(auth["D3"].sources) == 14
    assert auth["D4"].sources == ("transcripts/T6c_CEO_Voice_Note.docx", "transcripts/T6d_CEO_Voice_Note.docx")


def test_every_authorised_source_has_a_catalogue_rule():
    from veloxa.catalogue import classify

    rules = load_catalogue()
    for pack in ("D2", "D3"):
        for src in load_authorisations()[pack].sources:
            classify(rules, src)                                     # raises if a source has no rule


def test_protected_d2_sources_keep_their_classes():
    by = {r.pattern: r for r in load_catalogue()}
    assert by["data_room/hr/Leave_Register.xlsx"].data_class == "sensitive_personal"
    assert by["data_room/hr/Hiring_Tracker.xlsx"].data_class == "personal"
    assert by["data_room/email/Email_Printouts.pdf"].data_class == "private_comms"


def test_business_keys_are_loaded_from_the_file_catalogue():
    by = {r.system: r for r in load_catalogue()}
    assert by["git"].key_columns == ("sha",) and by["jira"].key_columns == ("Key",)
    assert by["git"].record_key("r2", {"sha": "abc"}) == "key:sha=abc"
    assert by["jira"].record_key("Versions!r3", {"Version": "v3"}) == "Versions!r3"   # no Key: locator kept


def test_csv_rows_keep_text_values_and_line_numbers_and_reject_ragged_rows():
    envs = parse(b"sha,files_changed\nabc,3\ndef,12\n", ".csv", "rows")
    assert [e.payload for e in envs] == [{"sha": "abc", "files_changed": "3"}, {"sha": "def", "files_changed": "12"}]
    assert envs[0].locator == {"row": 2, "header_row": 1} and envs[0].locator_key() == "r2"
    with pytest.raises(ValueError):
        parse(b"a,b\n1,2,3\n", ".csv", "rows")


def test_integer_text_contract_accepts_digits_only():
    c = Contract.from_toml({"required_columns": ["n"], "integer_text": ["n"]})
    validate_rows("f.csv", c, [({"row": 2}, {"n": "12"})])
    with pytest.raises(AdmissionRefused):
        validate_rows("f.csv", c, [({"row": 2}, {"n": "1.5"})])


def test_sheet_scoped_contract_validates_only_its_sheet():
    c = Contract.from_toml({"sheet": "Issues", "required_columns": ["Key"], "unique": ["Key"]})
    validate_rows("j.xlsx", c, [({"sheet": "Issues", "row": 3}, {"Key": "A-1"}),
                                ({"sheet": "Versions", "row": 2}, {"Version": "v3"})])


def test_the_d15_decision_file_is_never_in_the_repository():
    assert not list(ROOT.joinpath("config").rglob("D15_*.toml"))     # private: VELOXA_D15_POLICY
    d = tomllib.loads((ROOT / "tests/fixtures/d15_test_policy.toml").read_text(encoding="utf-8"))
    assert d["content"]["probabilities"] == "disabled"
