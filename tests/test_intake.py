"""Company data room end to end (migration 0018) on a SYNTHETIC workspace: approve a source type, upload,
validation dry run, department review, separate admission authorisation, admission through the existing
sequencer, sealing, projection and search. Every refusal of the separation-of-duties rules is checked in the
database, not only in Python."""

import uuid

import pytest
from synthetic_pack import GL_COLUMNS, _docx, _xlsx

from veloxa import intake, ledger, tenancy, tools
from veloxa.ask import pipeline
from veloxa.authz import evidence
from veloxa.db import connect
from veloxa.storage import LocalContentStore, StoreError

pytestmark = pytest.mark.db
ADMIN = "person:syn-admin"
PEOPLE = ("contrib", "reviewer", "authoriser", "ceo", "outsider")


@pytest.fixture
def room(db, tmp_path):
    slug = f"room-{uuid.uuid4().hex[:8]}"
    with connect(db.admin_dsn) as a, connect(db.dsn("app")) as app:
        tid = tenancy.create_tenant(a, slug, "Asia/Kolkata", "Room Co", synthetic=True)
        tenancy.bootstrap_synthetic_admin(a, tid, ADMIN, "Admin")
        for p in PEOPLE:
            tenancy.add_synthetic_member(a, tid, f"person:{p}", p)
        tenancy.create_department(app, tid, ADMIN, "finance", "Finance")
        g = lambda *x: tenancy.grant(app, tid, ADMIN, *x)  # noqa: E731
        g("department", "finance", "contributor", "person:contrib", "uploads")
        g("department", "finance", "reviewer", "person:reviewer", "reviews")
        g("tenant", str(tid), "admission_authoriser", "person:authoriser", "authorises")
        g("tenant", str(tid), "company_reader", "person:ceo", "CEO company-wide read")
        g("evidence_source", "finance_docs", "read", "department:finance", "finance documents", "member")
        intake.authorise_source_type(app, tid, "person:authoriser", source_type="finance_update",
                                     title="Finance updates", department_id="finance", kind="document",
                                     system="finance_docs", data_class="confidential", records="document",
                                     file_glob="*.docx", contract=None, key_columns=[],
                                     decision_record="SYN-DECISION-1 (synthetic)")
        intake.authorise_source_type(app, tid, "person:authoriser", source_type="gl_export", title="GL export",
                                     department_id="finance", kind="export", system="finance_gl",
                                     data_class="confidential", records="rows", file_glob="*.xlsx",
                                     contract={"required_columns": GL_COLUMNS}, key_columns=["Voucher"],
                                     decision_record="SYN-DECISION-2 (synthetic)")
    store = LocalContentStore(tmp_path / "originals")
    return {"tenant": tid, "store": store, "tmp": tmp_path}


def _doc(tmp, text):
    p = tmp / f"{uuid.uuid4().hex}.docx"
    _docx(p, ["Synthetic finance update", text])
    return p.read_bytes()


def _upload(db, room, actor="person:contrib", name="Q3_update.docx", data=None, st="finance_update"):
    with connect(db.dsn("app")) as app:
        return intake.upload(app, room["store"], room["tenant"], actor, "finance", st, "Q3 update",
                             [intake.Upload(name, data or _doc(room["tmp"], "Project Falcon moved to March 2027."))])


def _validate(db, room, sid):
    with connect(db.dsn("ingest")) as ing:
        return intake.validate(ing, room["store"], room["tenant"], sid)


def _decide(db, room, actor, sid, d):
    with connect(db.dsn("app")) as app:
        return intake.decide(app, room["tenant"], actor, sid, d, "synthetic test decision")


def _admit(db, room, sid):
    with connect(db.dsn("ingest")) as ing, connect(db.dsn("projection")) as prj:
        return intake.admit(ing, prj, room["store"], room["tenant"], sid)


def _ask(db, room, pid, q):
    from veloxa.releases import current_time

    with connect(db.dsn("app")) as c:
        acc = evidence.access_for(c, room["tenant"], pid, "rebac")
        return pipeline.answer(c, room["tenant"], "Asia/Kolkata",
                               tools.Principal(pid, "authenticated", None, acc, room["tenant"]), q, current_time())


def test_the_whole_journey_makes_evidence_searchable_only_after_authorised_admission(db, room):
    sid = _upload(db, room)
    with connect(db.dsn("app")) as app:
        assert ledger.batches(app, room["tenant"]) == []                     # uploading is NOT admission
    rep = _validate(db, room, sid)
    assert rep["errors"] == [] and rep["records"] == 1 and rep["classification"]["data_class"] == "confidential"
    assert _ask(db, room, "person:ceo", "What happened to Project Falcon?")["evidence"] == []   # not yet evidence
    assert _decide(db, room, "person:reviewer", sid, "accept") == "awaiting_authorisation"
    assert _decide(db, room, "person:authoriser", sid, "authorise") == "authorised"
    out = _admit(db, room, sid)
    assert out["batch_seq"] == 1 and out["records"] == 1
    with connect(db.dsn("app")) as app:
        d = intake.detail(app, room["tenant"], "person:ceo", sid)
        assert [e["state"] for e in d["events"]] == ["uploaded", "validating", "awaiting_review",
                                                     "awaiting_authorisation", "authorised", "admitting",
                                                     "admitted", "sealed", "projected"]
        assert ledger.batches(app, room["tenant"])[0]["sealed"]
    a = _ask(db, room, "person:ceo", "What happened to Project Falcon?")
    assert a["evidence"] and {e["system"] for e in a["evidence"]} == {"finance_docs"}
    assert a["evidence"][0]["source_path"].startswith("finance/finance_update/")
    assert _ask(db, room, "person:outsider", "What happened to Project Falcon?")["evidence"] == []


def test_separation_of_duties_is_enforced_by_the_database(db, room):
    with pytest.raises(intake.NotPermitted, match="contributor"):
        _upload(db, room, actor="person:outsider")
    sid = _upload(db, room)
    _validate(db, room, sid)
    with pytest.raises(intake.NotPermitted, match="reviewer"):
        _decide(db, room, "person:contrib", sid, "accept")                  # the submitter has no reviewer relation
    with connect(db.dsn("app")) as app:                                      # even a submitter who IS a reviewer
        tenancy.grant(app, room["tenant"], ADMIN, "department", "finance", "reviewer", "person:contrib", "x")
    with pytest.raises(intake.NotPermitted, match="own submission"):
        _decide(db, room, "person:contrib", sid, "accept")
    with pytest.raises(intake.IntakeError, match="not awaiting authorisation"):
        _decide(db, room, "person:authoriser", sid, "authorise")
    _decide(db, room, "person:reviewer", sid, "accept")
    with pytest.raises(intake.NotPermitted, match="admission_authoriser"):
        _decide(db, room, "person:reviewer", sid, "authorise")
    with pytest.raises(intake.IntakeError, match="not authorised"):
        _admit(db, room, sid)
    with connect(db.dsn("app")) as app:                                      # authoriser who reviewed: refused
        tenancy.grant(app, room["tenant"], ADMIN, "tenant", str(room["tenant"]), "admission_authoriser",
                      "person:reviewer", "x")
    with pytest.raises(intake.NotPermitted, match="differ"):
        _decide(db, room, "person:reviewer", sid, "authorise")


def test_a_reviewer_cannot_create_a_data_class_and_protected_classes_are_impossible(db, room):
    with connect(db.dsn("app")) as app:
        with pytest.raises(intake.NotPermitted):
            intake.authorise_source_type(app, room["tenant"], "person:reviewer", source_type="payroll",
                                         title="x", department_id="finance", kind="export", system="pay",
                                         data_class="confidential", records="rows", file_glob="*.xlsx",
                                         contract=None, key_columns=[], decision_record="x")
        with pytest.raises(intake.IntakeError):
            intake.authorise_source_type(app, room["tenant"], "person:authoriser", source_type="payroll",
                                         title="x", department_id="finance", kind="export", system="pay",
                                         data_class="payroll_aggregate", records="rows", file_glob="*.xlsx",
                                         contract=None, key_columns=[], decision_record="x")


def test_unsafe_files_are_refused_before_storage_and_contract_failures_before_review(db, room, tmp_path):
    with pytest.raises(intake.IntakeError):
        _upload(db, room, name="../escape.docx")
    with pytest.raises(Exception, match="type_mismatch|not a"):
        _upload(db, room, name="fake.docx", data=b"MZ not a docx at all")
    with pytest.raises(intake.IntakeError, match="supported"):
        _upload(db, room, name="tool.exe", data=b"MZ")
    bad = tmp_path / "bad.xlsx"
    _xlsx(bad, "GL", ["Date", "Narration only"], [["2099-01-01", "x"]])
    sid = _upload(db, room, name="bad.xlsx", data=bad.read_bytes(), st="gl_export")
    rep = _validate(db, room, sid)
    assert rep["errors"] and rep["errors"][0]["reason"] == "contract_violation"
    with connect(db.dsn("app")) as app:
        assert intake.detail(app, room["tenant"], ADMIN, sid)["state"] == "validation_failed"


def test_duplicates_are_reported_and_business_keys_identify_corrected_rows(db, room, tmp_path):
    gl = tmp_path / "GL.xlsx"
    rows = [["2099-01-01", "V-1", "Vendor One", "CC-A", 10, "line"],
            ["2099-01-02", "V-2", "Vendor Two", "CC-A", 20, "x"]]
    _xlsx(gl, "GL", GL_COLUMNS, rows)
    s1 = _upload(db, room, name="GL.xlsx", data=gl.read_bytes(), st="gl_export")
    _validate(db, room, s1)
    s2 = _upload(db, room, name="GL.xlsx", data=gl.read_bytes(), st="gl_export")
    rep = _validate(db, room, s2)
    assert rep["duplicates"] and rep["duplicates"][0]["same_file_in_submissions"] == [str(s1)]
    for s in (s1,):
        _decide(db, room, "person:reviewer", s, "accept")
        _decide(db, room, "person:authoriser", s, "authorise")
        _admit(db, room, s)
    corrected = tmp_path / "GL2.xlsx"                                         # a row inserted ABOVE V-2
    _xlsx(corrected, "GL", GL_COLUMNS, [rows[0], ["2099-01-01", "V-9", "Vendor Nine", "CC-B", 5, "new"],
                                        [*rows[1][:4], 25, "corrected"]])
    s3 = _upload(db, room, name="GL.xlsx", data=corrected.read_bytes(), st="gl_export")
    _validate(db, room, s3)
    _decide(db, room, "person:reviewer", s3, "accept")
    _decide(db, room, "person:authoriser", s3, "authorise")
    _admit(db, room, s3)
    with connect(db.admin_dsn) as a:
        v = {r["record_id"]: (r["version"], r["payload"]) for r in a.execute(
            "SELECT DISTINCT ON (record_id) record_id, version, payload FROM ledger.source_record WHERE tenant_id = %s"
            " ORDER BY record_id, version DESC", (room["tenant"],)).fetchall()}
    assert v["finance_gl:key:Voucher=V-2"][0] == 2                           # the same voucher, corrected
    assert v["finance_gl:key:Voucher=V-9"][0] == 1 and v["finance_gl:key:Voucher=V-1"][0] == 2


def test_the_original_store_is_write_once_verified_and_outside_the_repository(tmp_path):
    from pathlib import Path

    s = LocalContentStore(tmp_path / "o")
    t = uuid.uuid4()
    sha = s.put(t, b"synthetic bytes")
    assert s.put(t, b"synthetic bytes") == sha and s.get(t, sha) == b"synthetic bytes"
    p = s._path(t, sha)
    p.chmod(0o640)
    p.write_bytes(b"tampered")
    with pytest.raises(StoreError, match="tamper"):
        s.get(t, sha)
    with pytest.raises(StoreError, match="tamper"):
        s.put(t, b"synthetic bytes")
    with pytest.raises(StoreError, match="outside the Git repository"):
        LocalContentStore(Path(__file__).resolve().parents[1] / "originals")
    with pytest.raises(StoreError):
        s.get(t, "../" + "0" * 61)
