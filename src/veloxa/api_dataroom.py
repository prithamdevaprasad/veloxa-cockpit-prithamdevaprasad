"""Data room and evidence API (migrations 0018; C05, C06; §7.2, §19).

**Submissions:**
- **Upload** (`POST /v1/submissions`): base64 JSON. No new multipart dependency is used. C06 runs before storage.
- **Read:** listed and shown to the people concerned: workspace admins, admission authorisers, company readers,
  and members of the submission's department.
- **Decisions** (`POST /v1/submissions/{id}/decision`): accept or reject (reviewer), authorise or decline
  (admission authoriser). The database enforces the relations and separation of duties.

**Evidence:**
- **Detail** (`GET /v1/evidence/{record_id}`): provenance, locator, version, batch, data class, admission state, and
  the extracted content, within the requester's access.
- **Original** (`GET /v1/evidence/{record_id}/original`): the stored original, streamed as an attachment.
  - Only when the record is readable now.
  - Only when its original is in Veloxa's immutable store. Hackathon packs are not: their originals stay with the
    operator.
  - Every download is audited.
  - A filesystem path is never exposed.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from typing import Literal
from urllib.parse import quote

from fastapi import Header, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from . import audit, intake, tools
from .db import connect, tenant_tx
from .storage import LocalContentStore, StoreError

PROTECTED = ("payroll_aggregate", "sensitive_personal")


class FileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_name: str = Field(min_length=1, max_length=150)
    content_base64: str = Field(min_length=1)


class SubmissionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    department_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    source_type: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    title: str = Field(min_length=1, max_length=200)
    data_period: str | None = Field(default=None, max_length=60)
    files: list[FileIn] = Field(min_length=1, max_length=50)


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["accept", "reject", "authorise", "decline"]
    reason: str = Field(min_length=1, max_length=1000)


class SourceTypeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_type: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    title: str = Field(min_length=1, max_length=120)
    department_id: str
    kind: Literal["export", "document", "email", "transcript"]
    system: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    data_class: Literal["internal", "confidential", "personal", "private_comms"]
    records: Literal["rows", "document"]
    file_glob: str = Field(pattern=r"^\*\.[a-z0-9]{2,5}$")
    contract: dict | None = None
    key_columns: list[str] = Field(default_factory=list, max_length=5)
    decision_record: str = Field(min_length=3, max_length=500)


def _roles(conn, tid, pid) -> dict:
    sid = pid.split(":", 1)[1] if ":" in pid else pid
    with tenant_tx(conn, tid, pid):
        rows = conn.execute("SELECT resource_type, resource_id, relation FROM policy.live_tuple WHERE tenant_id = %s"
                            " AND subject_type = 'person' AND subject_id = %s AND subject_relation IS NULL",
                            (tid, sid)).fetchall()
    tenant_rel = {r["relation"] for r in rows if r["resource_type"] == "tenant"}
    depts = {r["resource_id"] for r in rows if r["resource_type"] == "department"}
    return {"tenant": tenant_rel, "departments": depts,
            "dept_roles": {(r["resource_id"], r["relation"]) for r in rows if r["resource_type"] == "department"}}


def _sees(roles: dict, department_id: str) -> bool:
    return bool(roles["tenant"] & {"admin", "admission_authoriser", "company_reader"}) or \
        department_id in roles["departments"]


def _jsonable(d):
    if isinstance(d, dict):
        return {k: _jsonable(v) for k, v in d.items()}
    if isinstance(d, list):
        return [_jsonable(v) for v in d]
    if isinstance(d, uuid.UUID):
        return str(d)
    if hasattr(d, "isoformat"):
        return d.isoformat()
    return d


def register(app, *, state, principal_of, now_fn, store_factory=LocalContentStore.from_env) -> None:
    def _who(conn, authorization):
        return principal_of(conn, authorization, now_fn())

    def _store():
        s = store_factory()
        if s is None:
            raise StoreError("no original store is configured (VELOXA_ORIGINALS_DIR)")
        return s

    @app.get("/v1/source-types")
    def source_types(authorization: str | None = Header(default=None)):
        with connect(state["dsn"]) as conn:
            p = _who(conn, authorization)
            if isinstance(p, JSONResponse):
                return p
            with tenant_tx(conn, p.tenant_id, p.principal_id):
                rows = conn.execute("SELECT source_type, version, title, department_id, kind, system, data_class,"
                                    " records, file_glob, key_columns, contract, decision_record, approved_by,"
                                    " created_at FROM policy.catalogue_rule_current WHERE tenant_id = %s AND active"
                                    " ORDER BY department_id, title", (p.tenant_id,)).fetchall()
        return {"source_types": [_jsonable(dict(r)) for r in rows]}

    @app.post("/v1/admin/source-types")
    def approve_source_type(body: SourceTypeIn, authorization: str | None = Header(default=None)):
        with connect(state["dsn"]) as conn:
            p = _who(conn, authorization)
            if isinstance(p, JSONResponse):
                return p
            try:
                v = intake.authorise_source_type(conn, p.tenant_id, p.principal_id, **body.model_dump())
            except intake.NotPermitted as e:
                return JSONResponse(status_code=403, content={"error": "not_permitted", "detail": str(e)})
            except intake.IntakeError as e:
                return JSONResponse(status_code=422, content={"error": "invalid_request", "detail": str(e)})
        return {"source_type": body.source_type, "version": v}

    @app.post("/v1/submissions")
    def submit(body: SubmissionIn, authorization: str | None = Header(default=None)):
        """Store originals and record a candidate submission. NOTHING becomes evidence here."""
        with connect(state["dsn"]) as conn:
            p = _who(conn, authorization)
            if isinstance(p, JSONResponse):
                return p
            try:
                files = [intake.Upload(f.file_name, base64.b64decode(f.content_base64, validate=True))
                         for f in body.files]
            except (binascii.Error, ValueError):
                return JSONResponse(status_code=422, content={"error": "invalid_request",
                                                              "detail": "file content must be base64"})
            try:
                sid = intake.upload(conn, _store(), p.tenant_id, p.principal_id, body.department_id,
                                    body.source_type, body.title, files, body.data_period)
            except StoreError as e:
                return JSONResponse(status_code=503, content={"error": "store_unavailable", "detail": str(e)})
            except intake.NotPermitted as e:
                return JSONResponse(status_code=403, content={"error": "not_permitted", "detail": str(e)})
            except intake.IntakeError as e:
                return JSONResponse(status_code=422, content={"error": e.reason, "detail": str(e)})
            except Exception as e:  # noqa: BLE001 - C06 refusals carry a reason code
                if getattr(e, "reason", None):
                    return JSONResponse(status_code=422, content={"error": e.reason, "detail": str(e)})
                raise
        return {"submission_id": str(sid), "state": "uploaded",
                "next": "the system validates it; then a department reviewer and an admission authoriser decide"}

    @app.get("/v1/submissions")
    def submissions(state_: str | None = Query(default=None, alias="state"),
                    authorization: str | None = Header(default=None)):
        with connect(state["dsn"]) as conn:
            p = _who(conn, authorization)
            if isinstance(p, JSONResponse):
                return p
            roles = _roles(conn, p.tenant_id, p.principal_id)
            rows = [r for r in intake.list_submissions(conn, p.tenant_id, p.principal_id, state_)
                    if _sees(roles, r["department_id"])]
        return {"submissions": [_jsonable(r) for r in rows],
                "can_review": sorted(d for d, rel in roles["dept_roles"] if rel == "reviewer"),
                "can_authorise": "admission_authoriser" in roles["tenant"]}

    @app.get("/v1/submissions/{submission_id}")
    def submission(submission_id: uuid.UUID, authorization: str | None = Header(default=None)):
        with connect(state["dsn"]) as conn:
            p = _who(conn, authorization)
            if isinstance(p, JSONResponse):
                return p
            d = intake.detail(conn, p.tenant_id, p.principal_id, submission_id)
            if d is None or not _sees(_roles(conn, p.tenant_id, p.principal_id), d["department_id"]):
                return JSONResponse(status_code=404, content={"error": "not_found", "detail": "no such submission"})
        return _jsonable(d)

    @app.post("/v1/submissions/{submission_id}/decision")
    def decision(submission_id: uuid.UUID, body: DecisionIn, authorization: str | None = Header(default=None)):
        with connect(state["dsn"]) as conn:
            p = _who(conn, authorization)
            if isinstance(p, JSONResponse):
                return p
            try:
                st = intake.decide(conn, p.tenant_id, p.principal_id, submission_id, body.decision, body.reason)
            except intake.NotPermitted as e:
                return JSONResponse(status_code=403, content={"error": "not_permitted", "detail": str(e)})
            except intake.IntakeError as e:
                return JSONResponse(status_code=422, content={"error": "invalid_request", "detail": str(e)})
        return {"submission_id": str(submission_id), "state": st}

    def _readable_record(conn, p, record_id):
        """The latest visible version of a record, if the principal may read it now; else None."""
        from . import ledger
        from .temporal import Mode, resolve_basis

        tz_row = conn.execute("SELECT time_zone FROM directory.tenant WHERE tenant_id = %s", (p.tenant_id,)).fetchone()
        basis = resolve_basis(p.tenant_id, ledger.sealed_releases(conn, p.tenant_id), Mode.CURRENT, None,
                              tz_row["time_zone"], now_fn())
        if not basis.batch_seq:
            return None, None
        with tenant_tx(conn, p.tenant_id, p.principal_id, basis.batch_seq):
            r = conn.execute("SELECT DISTINCT ON (record_id) * FROM ledger.visible_source_record"
                             " WHERE record_id = %s ORDER BY record_id, version DESC", (record_id,)).fetchone()
            if r is None or r["data_class"] in PROTECTED:
                return None, None
            if p.access is not None and not p.access.allows(r["system"], r["data_class"]):
                return None, None
            if r["data_class"] == "private_comms":
                d = conn.execute("SELECT parties FROM projection.document_text WHERE source_record_id = %s"
                                 " ORDER BY batch_seq DESC LIMIT 1", (record_id,)).fetchone()
                if not d or not p.email or p.email.lower() not in (d["parties"] or []):
                    return None, None
            versions = conn.execute("SELECT version, batch_seq, extracted_at FROM ledger.visible_source_record"
                                    " WHERE record_id = %s ORDER BY version", (record_id,)).fetchall()
            stored = conn.execute("SELECT 1 FROM intake.stored_object WHERE tenant_id = %s AND sha256 = %s",
                                  (p.tenant_id, r["raw_object"])).fetchone() is not None
        return r, {"versions": versions, "original_stored": stored}

    @app.get("/v1/evidence/{record_id:path}/original")
    def original(record_id: str, download: bool = True, authorization: str | None = Header(default=None)):
        with connect(state["dsn"]) as conn:
            p = _who(conn, authorization)
            if isinstance(p, JSONResponse):
                return p
            r, meta = _readable_record(conn, p, record_id)
            if r is None:
                return JSONResponse(status_code=404, content={"error": "not_found",
                                                              "detail": "no readable record with this id"})
            if not meta["original_stored"]:
                return JSONResponse(status_code=409, content={
                    "error": "original_not_stored",
                    "detail": "this record's original file is not held in Veloxa's store (operator-held pack)"})
            try:
                data = _store().get(p.tenant_id, r["raw_object"])
            except StoreError as e:
                return JSONResponse(status_code=503, content={"error": "store_unavailable", "detail": str(e)})
            with tenant_tx(conn, p.tenant_id, p.principal_id):
                audit.append(conn, p.tenant_id, "evidence.original_downloaded",
                             {"record": record_id, "sha256": r["raw_object"], "by": p.principal_id})
        name = r["source_path"].rsplit("/", 1)[-1]
        media = intake.MEDIA.get("." + name.rsplit(".", 1)[-1].lower(), "application/octet-stream")
        disposition = "attachment" if download or media not in ("application/pdf", "text/plain") else "inline"
        return Response(content=data, media_type=media, headers={
            "Content-Disposition": f'{disposition}; filename="{name}"', "X-Content-SHA256": r["raw_object"],
            "Cache-Control": "no-store"})

    @app.get("/v1/evidence/{record_id:path}")
    def evidence_detail(record_id: str, authorization: str | None = Header(default=None)):
        with connect(state["dsn"]) as conn:
            p = _who(conn, authorization)
            if isinstance(p, JSONResponse):
                return p
            r, meta = _readable_record(conn, p, record_id)
            if r is None:
                return JSONResponse(status_code=404, content={"error": "not_found",
                                                              "detail": "no readable record with this id"})
            ev = tools.Evidence(r["record_id"], r["kind"], r["system"], r["data_class"], r["batch_seq"],
                                r["source_path"], r["locator"], "")
        return _jsonable({
            "record_id": ev.record_id, "file_name": r["source_path"].rsplit("/", 1)[-1], "kind": r["kind"],
            "system": r["system"], "data_class": r["data_class"], "locator": r["locator"], "version": r["version"],
            "batch_seq": r["batch_seq"], "extracted_at": r["extracted_at"], "parser_version": r["parser_version"],
            "content_sha256": r["raw_object"], "content": r["payload"],
            "versions": [dict(v) for v in meta["versions"]], "admission_state": "admitted and sealed",
            "access": "authorised for you now",
            "original": {"available": meta["original_stored"],
                         "href": f"/v1/evidence/{quote(record_id, safe='')}/original"
                         if meta["original_stored"] else None,
                         "note": None if meta["original_stored"] else
                         "the original file is held by the operator, not in Veloxa's store"}})
