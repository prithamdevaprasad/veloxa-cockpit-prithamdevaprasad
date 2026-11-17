"""Per-document Ask authorisation in `rebac` tenants (C20 + C21 through authz/evidence.py). SYNTHETIC D1 only.

Sources of the synthetic pack: keka_hrms (HR), zoho_books (ledger), payroll_register (protected), documents
(board updates), email (private comms), meeting_summaries, challenge_brief, data_room.
"""

import pytest
from conftest import D1_DAY

from veloxa import projector, tenancy, tools
from veloxa.ask import pipeline
from veloxa.authz import evidence
from veloxa.db import connect

pytestmark = pytest.mark.db
IST = "Asia/Kolkata"
ADMIN = "person:syn-admin"


@pytest.fixture
def org(db, ingest_conn, tenant, schedule, synthetic_root):
    from veloxa.ingest.pipeline import admit_pack

    admit_pack(ingest_conn, tenant, schedule, "D1", synthetic_root, D1_DAY)
    with connect(db.dsn("projection")) as c:
        projector.build(c, tenant)
    with connect(db.admin_dsn) as a, connect(db.dsn("app")) as app:
        tenancy.record_settings(a, tenant, display_name="Synthetic Org", admission_mode="release_schedule",
                                evidence_access="rebac", status="active")
        tenancy.bootstrap_synthetic_admin(a, tenant, ADMIN, "Admin")
        for pid in ("ceo", "eng-head", "fin-member", "people-member", "nobody"):
            tenancy.add_synthetic_member(a, tenant, f"person:{pid}", pid)
        for d in ("engineering", "finance", "people"):
            tenancy.create_department(app, tenant, ADMIN, d, d.title())
        g = lambda *a_: tenancy.grant(app, tenant, ADMIN, *a_)  # noqa: E731
        g("tenant", str(tenant), "company_reader", "person:ceo", "CEO company-wide read (explicit)")
        g("department", "engineering", "head", "person:eng-head", "head")
        fin = g("department", "finance", "member", "person:fin-member", "member")
        g("department", "people", "member", "person:people-member", "member")
        g("evidence_source", "zoho_books", "read", "department:finance", "finance owns the ledger", "member")
        g("evidence_source", "keka_hrms", "read", "department:people", "people own HR", "member")
        g("evidence_source", "meeting_summaries", "read", "department:engineering", "eng summaries", "member")
    return {"tenant": tenant, "fin_tuple": fin}


def _ask(db, tenant, pid, q):
    from veloxa.releases import current_time

    with connect(db.dsn("app")) as c:
        acc = evidence.access_for(c, tenant, pid, "rebac")
        return pipeline.answer(c, tenant, IST, tools.Principal(pid, "authenticated", None, acc, tenant), q,
                               current_time())


def _systems(a):
    return {e["system"] for e in a["evidence"]}


def test_finance_reads_the_ledger_but_engineering_does_not(db, org):
    t = org["tenant"]
    fin = _ask(db, t, "person:fin-member", "What is the ledger spend?")
    assert fin["claims"] and _systems(fin) == {"zoho_books"}
    eng = _ask(db, t, "person:eng-head", "What is the ledger spend?")
    assert eng["claims"] == [] and "zoho_books" not in _systems(eng)
    assert any("outside your authorised sources" in u for u in eng["unknowns"])
    assert eng["diagnostics"]["withheld_by_permission"] > 0 and eng["diagnostics"]["access_mode"] == "rebac"
    assert "INR" not in eng["answer"]                                              # no value leaks


def test_finance_cannot_read_hr_records_and_people_ops_can(db, org):
    t = org["tenant"]
    assert _ask(db, t, "person:fin-member", "What is our headcount?")["claims"] == []
    ppl = _ask(db, t, "person:people-member", "What is our headcount?")
    assert ppl["claims"] and _systems(ppl) <= {"keka_hrms"}


def test_explicit_company_read_spans_departments_but_never_payroll(db, org):
    t = org["tenant"]
    assert _ask(db, t, "person:ceo", "What is the ledger spend?")["claims"]
    assert _ask(db, t, "person:ceo", "What is our headcount?")["claims"]
    pay = _ask(db, t, "person:ceo", "What is the total payroll?")
    assert pay["status"] == "refused" and pay["evidence"] == []
    acc = evidence.EvidenceAccess("rebac", company=True)
    assert not acc.allows("payroll_register", "sensitive_personal") and not acc.allows("x", "payroll_aggregate")


def test_a_member_without_grants_and_an_unknown_principal_see_nothing(db, org):
    t = org["tenant"]
    for pid in ("person:nobody", "person:not-in-this-workspace"):
        a = _ask(db, t, pid, "What is the ledger spend?")
        assert a["claims"] == [] and a["evidence"] == []


def test_revocation_and_suspension_take_effect_immediately(db, org):
    t = org["tenant"]
    assert _ask(db, t, "person:fin-member", "What is the ledger spend?")["claims"]
    with connect(db.dsn("app")) as app:
        tenancy.revoke(app, t, ADMIN, org["fin_tuple"])
    assert _ask(db, t, "person:fin-member", "What is the ledger spend?")["claims"] == []
    assert _ask(db, t, "person:people-member", "What is our headcount?")["claims"]
    with connect(db.dsn("app")) as app:
        tenancy.set_status(app, t, ADMIN, "person:people-member", "suspended", "test")
    assert _ask(db, t, "person:people-member", "What is our headcount?")["claims"] == []


def test_access_from_one_tenant_never_reaches_another(db, org, admin_tenant_factory):
    other = admin_tenant_factory()
    with connect(db.dsn("app")) as c:
        assert evidence.access_for(c, other, "person:ceo", "rebac") == evidence.NONE


def test_tenant_wide_mode_keeps_the_pre_0017_behaviour(db, org):
    acc = evidence.TENANT_WIDE
    assert acc.allows("zoho_books", "confidential") and not acc.allows("payroll_register", "sensitive_personal")


@pytest.fixture
def admin_tenant_factory(db):
    def make():
        with connect(db.admin_dsn) as a:
            import uuid

            return tenancy.create_tenant(a, f"t-{uuid.uuid4().hex[:8]}", "UTC", "Other", synthetic=True)
    return make
