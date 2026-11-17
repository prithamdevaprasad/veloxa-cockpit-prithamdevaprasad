"""Projection of admitted ACL history into typed items (ARCHITECTURE.md §10.2, §10.3; C32 input).

Source records of kind "acl_export" (one JSON object per row) are projected into projection.acl_history. Each row
states one history item:

  identity_link  {"type", "person": "person:<id>", "principal": "user:<id>" | "ext_user:<source>:<id>",
                  "valid_from", "valid_to"?, "recorded_at"?}
  membership     {"type", "member": <principal or group key>, "group": "group:<id>" | "ext_group:<src>:<name>",
                  "valid_from", "valid_to"?, "recorded_at"?}
  acl            {"type", "resource", "principal", "permission", "effect": "allow" | "deny", "relation",
                  "valid_from", "valid_to"?, "recorded_at"?}
  resource       {"type", "resource", "created_at"?, "ended_at"?, "observed_first"?, "observed_last"?,
                  "recorded_at"?}
  acl_state      {"type", "resource", "history_from"? , "snapshot_extracted_at"?, "acl_source_version",
                  "acl_synced_at", "recorded_at"?}

Instants are ISO 8601 with an explicit offset. R: a stated `recorded_at` no later than extraction (R <= E), else the
extraction time bounds it (T4). A row that breaks these rules is projected as `invalid` with its reason, so the
evaluator fails closed on anything it touches instead of silently dropping it.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

import psycopg

PRINCIPAL_PREFIXES = ("user:", "ext_user:", "group:", "ext_group:", "domain:")
GROUP_PREFIXES = ("group:", "ext_group:")
RELATIONS = {"acl", "author", "sender", "recipient", "cc", "assignee", "responder", "role_entitlement"}
TYPES = {"identity_link", "membership", "acl", "resource", "acl_state"}


class InvalidItem(ValueError):
    pass


def _instant(payload: dict, key: str, required: bool) -> datetime | None:
    raw = payload.get(key)
    if raw is None:
        if required:
            raise InvalidItem(f"{key} is required")
        return None
    try:
        value = datetime.fromisoformat(str(raw))
    except ValueError as exc:
        raise InvalidItem(f"{key} is not an ISO instant") from exc
    if value.tzinfo is None:
        raise InvalidItem(f"{key} must carry an explicit offset")
    return value


def _text(payload: dict, key: str, prefixes: tuple[str, ...] | None = None) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InvalidItem(f"{key} is required")
    if prefixes and not value.startswith(prefixes):
        raise InvalidItem(f"{key} must be a typed key ({', '.join(prefixes)})")
    return value


def validate(payload: dict) -> tuple[str, tuple[datetime, datetime | None] | None, dict]:
    """(item_type, valid interval or None, typed fields). Raises InvalidItem."""
    kind = payload.get("type")
    if kind not in TYPES:
        raise InvalidItem(f"unknown item type {kind!r}")
    fields: dict = {}
    if kind == "identity_link":
        fields["person"] = _text(payload, "person", ("person:",))
        fields["principal"] = _text(payload, "principal", ("user:", "ext_user:"))
    elif kind == "membership":
        fields["member"] = _text(payload, "member", PRINCIPAL_PREFIXES)
        fields["group"] = _text(payload, "group", GROUP_PREFIXES)
    elif kind == "acl":
        fields["resource"] = _text(payload, "resource")
        principal = payload.get("principal")
        if principal != "is_public":
            principal = _text(payload, "principal", PRINCIPAL_PREFIXES)
        fields["principal"] = principal
        fields["permission"] = _text(payload, "permission")
        if payload.get("effect") not in ("allow", "deny"):
            raise InvalidItem("effect must be allow or deny")
        fields["effect"] = payload["effect"]
        if payload.get("relation") not in RELATIONS:
            raise InvalidItem(f"relation must be one of {sorted(RELATIONS)}")
        fields["relation"] = payload["relation"]
    elif kind == "resource":
        fields["resource"] = _text(payload, "resource")
        for k in ("created_at", "ended_at", "observed_first", "observed_last"):
            v = _instant(payload, k, False)
            fields[k] = v.isoformat() if v else None
        of, ol = _instant(payload, "observed_first", False), _instant(payload, "observed_last", False)
        if (of is None) != (ol is None) or (of and ol and of > ol):
            raise InvalidItem("observed_first and observed_last go together, first <= last")
    else:  # acl_state
        fields["resource"] = _text(payload, "resource")
        for k in ("history_from", "snapshot_extracted_at"):
            v = _instant(payload, k, False)
            fields[k] = v.isoformat() if v else None
        synced = _instant(payload, "acl_synced_at", True)
        fields["acl_synced_at"] = synced.isoformat()
        fields["acl_source_version"] = _text(payload, "acl_source_version")
        if fields["history_from"] is None and fields["snapshot_extracted_at"] is None:
            raise InvalidItem("acl_state needs history_from or snapshot_extracted_at")
    valid = None
    if kind in ("identity_link", "membership", "acl"):
        start, end = _instant(payload, "valid_from", True), _instant(payload, "valid_to", False)
        if end is not None and end <= start:
            raise InvalidItem("valid_to must be after valid_from")
        valid = (start, end)
    return kind, valid, fields


def project_batch(conn: psycopg.Connection, tenant_id: uuid.UUID, k: int) -> int:
    rows = conn.execute(
        "SELECT record_id, version, data_class, extracted_at, payload FROM ledger.source_record"
        " WHERE tenant_id = %s AND batch_seq = %s AND kind = 'acl_export'", (tenant_id, k)).fetchall()
    for r in rows:
        extracted = r["extracted_at"]
        try:
            kind, valid, fields = validate(r["payload"])
            stated = _instant(r["payload"], "recorded_at", False)
        except InvalidItem as exc:
            kind, valid, fields, stated = "invalid", None, {"reason": str(exc), "row": r["payload"]}, None
        if stated is not None and stated <= extracted:
            recorded_hi, basis = stated, "source_recorded"
        else:
            recorded_hi, basis = extracted, "extraction_upper_bound"
        conn.execute(
            "INSERT INTO projection.acl_history (tenant_id, batch_seq, source_record_id, source_version, data_class,"
            " item_type, valid, recorded_hi, recorded_basis, extracted_at, payload)"
            " VALUES (%s, %s, %s, %s, %s, %s, CASE WHEN %s::timestamptz IS NULL THEN NULL"
            " ELSE tstzrange(%s::timestamptz, %s::timestamptz, '[)') END, %s, %s, %s, %s)",
            (tenant_id, k, r["record_id"], r["version"], r["data_class"], kind,
             valid[0] if valid else None, valid[0] if valid else None, valid[1] if valid else None,
             recorded_hi, basis, extracted, json.dumps(fields, default=str)))
    return len(rows)
