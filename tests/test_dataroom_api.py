"""Data room and evidence API over HTTP, SYNTHETIC workspace: upload (base64 JSON), worker validation, review,
authorisation, worker admission, Ask, evidence detail and an audited original download."""

import base64
import uuid
from urllib.parse import quote

import pytest
from conftest import DEV_TOKEN, api_client
from synthetic_pack import _docx

from veloxa import intake, tenancy
from veloxa.db import connect
from veloxa.storage import LocalContentStore

pytestmark = pytest.mark.db
H = {"Authorization": f"Bearer {DEV_TOKEN}"}
ADMIN = "person:syn-admin"


@pytest.fixture
def ws(db, tmp_path):
    slug = f"dr-{uuid.uuid4().hex[:8]}"
    with connect(db.admin_dsn) as a, connect(db.dsn("app")) as app:
        tid = tenancy.create_tenant(a, slug, "UTC", "Data Room Co", synthetic=True)
        tenancy.bootstrap_synthetic_admin(a, tid, ADMIN, "Admin")
        for p in ("contrib", "reviewer", "authoriser", "ceo", "outsider"):
            tenancy.add_synthetic_member(a, tid, f"person:{p}", p)
        tenancy.create_department(app, tid, ADMIN, "operations", "Operations")
        g = lambda *x: tenancy.grant(app, tid, ADMIN, *x)  # noqa: E731
        g("department", "operations", "contributor", "person:contrib", "x")
        g("department", "operations", "reviewer", "person:reviewer", "x")
        g("tenant", str(tid), "admission_authoriser", "person:authoriser", "x")
        g("tenant", str(tid), "company_reader", "person:ceo", "x")
    store = LocalContentStore(tmp_path / "originals")
    return {"slug": slug, "tenant": tid, "store": store, "tmp": tmp_path,
            "ingest": db.dsn("ingest"), "projection": db.dsn("projection")}


def _work(db, ws):
    with connect(ws["ingest"]) as ing, connect(ws["projection"]) as prj:
        return intake.work_once(ing, prj, ws["store"])


def test_upload_review_authorise_admit_ask_and_open_the_original(db, monkeypatch, ws):
    doc = ws["tmp"] / "Vendor_review.docx"
    _docx(doc, ["Synthetic vendor review", "Vendor Orchid deliveries slipped by two weeks in Q3."])
    data = doc.read_bytes()
    factory = lambda: ws["store"]  # noqa: E731

    def mk(who):
        from fastapi.testclient import TestClient

        from veloxa.api import PRIVILEGED_ENV, create_app

        for n in PRIVILEGED_ENV + ("VELOXA_OIDC",):
            monkeypatch.delenv(n, raising=False)
        for k, v in {"VELOXA_DEV_AUTH": "1", "VELOXA_DEV_TOKEN": DEV_TOKEN, "VELOXA_DEV_PRINCIPAL": f"person:{who}",
                     "VELOXA_DEV_TENANT": ws["slug"]}.items():
            monkeypatch.setenv(k, v)
        return TestClient(create_app(dsn=db.dsn("app"), store_factory=factory))

    with mk("authoriser") as c:
        r = c.post("/v1/admin/source-types", headers=H, json={
            "source_type": "vendor_review", "title": "Vendor reviews", "department_id": "operations",
            "kind": "document", "system": "ops_docs", "data_class": "confidential", "records": "document",
            "file_glob": "*.docx", "decision_record": "SYN-OPS-1 (synthetic)"})
        assert r.status_code == 200
    with mk("outsider") as c:
        r = c.post("/v1/submissions", headers=H, json={"department_id": "operations", "source_type": "vendor_review",
                   "title": "x", "files": [{"file_name": "Vendor_review.docx",
                                            "content_base64": base64.b64encode(data).decode()}]})
        assert r.status_code == 403
    with mk("contrib") as c:
        r = c.post("/v1/submissions", headers=H, json={"department_id": "operations", "source_type": "vendor_review",
                   "title": "Q3 vendor review", "data_period": "2099-Q3",
                   "files": [{"file_name": "Vendor_review.docx", "content_base64": base64.b64encode(data).decode()}]})
        assert r.status_code == 200, r.text
        sid = r.json()["submission_id"]
        assert c.post("/v1/submissions", headers=H, json={"department_id": "operations",
                      "source_type": "vendor_review", "title": "bad",
                      "files": [{"file_name": "x.docx", "content_base64": "@@not base64@@"}]}).status_code == 422
    assert _work(db, ws)["validated"] == 1
    with mk("reviewer") as c:
        d = c.get(f"/v1/submissions/{sid}", headers=H).json()
        assert d["state"] == "awaiting_review" and d["validation"]["ok"]
        assert d["validation"]["report"]["by_file"]["Vendor_review.docx"]["records"] == 1
        assert c.get("/v1/submissions", headers=H).json()["can_review"] == ["operations"]
        assert c.post(f"/v1/submissions/{sid}/decision", headers=H,
                      json={"decision": "accept", "reason": "checked"}).json()["state"] == "awaiting_authorisation"
    with mk("authoriser") as c:
        assert c.post(f"/v1/submissions/{sid}/decision", headers=H,
                      json={"decision": "authorise", "reason": "approved"}).json()["state"] == "authorised"
    assert _work(db, ws)["admitted"] == 1
    with mk("ceo") as c:
        a = c.post("/v1/ask", headers=H, json={"question": "What happened with Vendor Orchid?"}).json()
        assert a["evidence"], a["diagnostics"]
        rid = quote(a["evidence"][0]["record_id"], safe="")
        ev = c.get(f"/v1/evidence/{rid}", headers=H).json()
        assert ev["file_name"] == "Vendor_review.docx" and ev["original"]["available"]
        assert "path" not in ev["original"] and not ev["original"]["href"].startswith("/C:")
        orig = c.get(ev["original"]["href"], headers=H)
        assert orig.status_code == 200 and orig.content == data
        assert orig.headers["content-disposition"].startswith("attachment")
        assert orig.headers["x-content-sha256"] and orig.headers["cache-control"] == "no-store"
    with mk("outsider") as c:
        assert c.get(f"/v1/evidence/{rid}", headers=H).status_code == 404
        assert c.get(f"/v1/evidence/{rid}/original", headers=H).status_code == 404
        assert c.get(f"/v1/submissions/{sid}", headers=H).status_code == 404
    with connect(db.admin_dsn) as a:
        n = a.execute("SELECT count(*) AS n FROM audit.event WHERE tenant_id = %s"
                      " AND kind = 'evidence.original_downloaded'", (ws["tenant"],)).fetchone()["n"]
    assert n == 1


def test_operator_held_originals_are_never_pretended(db, monkeypatch, tmp_path):
    """Hackathon packs keep their originals with the operator: the API says so instead of a dead link."""
    from conftest import dev_fixture, ensure_configured_d1

    ensure_configured_d1(db, tmp_path)
    with api_client(db, monkeypatch, **dev_fixture()) as c:
        a = c.post("/v1/ask", headers=H, json={"question": "What is our headcount?"}).json()
        rid = quote(a["evidence"][0]["record_id"], safe="")
        ev = c.get(f"/v1/evidence/{rid}", headers=H).json()
        assert ev["original"]["available"] is False and ev["original"]["href"] is None
        assert c.get(f"/v1/evidence/{rid}/original", headers=H).status_code == 409
