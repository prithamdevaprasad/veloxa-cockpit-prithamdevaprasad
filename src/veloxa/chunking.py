"""Retrieval units for the text projection (migration 0019, `projection.text_chunk`).

**Prose records** are split into paragraph, page or message units, each with its exact locator inside the
source record:
- e-mail: blank-line paragraphs;
- documents: paragraphs, plus table rows;
- PDFs: groups of lines per page;
- text files: groups of lines.

**Row exports** contribute ONE descriptive unit per row: string fields with their column names. Figures stay
with governed metrics. Only rows of class `internal` or `confidential` contribute; personal records, private
communications and protected classes never do. Exception: messages imported by the Gmail connector are indexed
as e-mail (paragraph units, subject as title) and as `private_comms` with their parties, so only the sender and
recipients can retrieve them.
"""

from __future__ import annotations

import json
import re
import uuid

import psycopg

ROW_TEXT_CLASSES = ("internal", "confidential")
LINES_PER_CHUNK = 8
_NUMERIC = re.compile(r"[\s\d.,:/+-]+")
_BLANK = re.compile(r"\n\s*\n")


def chunks(kind: str, record_id: str, payload: dict, title: str) -> list[tuple[str, dict, str]]:
    """[(unit, chunk locator, body)] of one prose record, in source order."""
    out: list[tuple[str, dict, str]] = []
    if kind == "email":
        parts = [p.strip() for p in _BLANK.split(payload.get("body", "")) if p.strip()]
        return [("message", {"paragraph": i + 1}, p) for i, p in enumerate(parts)]
    if "paragraphs" in payload:
        for i, para in enumerate(payload["paragraphs"]):
            if para.strip():
                out.append(("paragraph", {"paragraph": i + 1}, para.strip()))
        for ti, t in enumerate(payload.get("tables", [])):
            for ri, cells in enumerate(t):
                out.append(("section", {"table": ti + 1, "row": ri + 1}, " | ".join(cells)))
        return out
    if "pages" in payload:
        for pi, page in enumerate(payload["pages"]):
            lines = [ln for ln in page.splitlines() if ln.strip()]
            for start in range(0, len(lines), LINES_PER_CHUNK):
                end = min(start + LINES_PER_CHUNK, len(lines))
                out.append(("page", {"page": pi + 1, "lines": [start + 1, end]}, "\n".join(lines[start:end])))
        return out
    if "lines" in payload:
        lines = payload["lines"]
        for start in range(0, len(lines), LINES_PER_CHUNK):
            end = min(start + LINES_PER_CHUNK, len(lines))
            body = "\n".join(ln for ln in lines[start:end] if ln.strip())
            if body:
                out.append(("section", {"lines": [start + 1, end]}, body))
    return out


def gmail_parties(payload: dict) -> list[str]:
    from email.utils import getaddresses

    return sorted({a.lower() for _, a in getaddresses([payload.get(x) or "" for x in ("from", "to", "cc")]) if a})


def gmail_units(payload: dict) -> tuple[str, object, list[str], list[tuple[str, dict, str]]]:
    """(title, event_at, parties, units) of one message imported by the Gmail connector. Indexed as a private
    communication: the units carry the sender and recipients, and search only returns them to those parties."""
    from email.utils import parsedate_to_datetime

    title = (payload.get("subject") or "(no subject)").strip()
    try:
        event_at = parsedate_to_datetime(payload["date"]) if payload.get("date") else None
    except (TypeError, ValueError):
        event_at = None
    text = payload.get("body") or payload.get("snippet") or ""
    paras = [p.strip() for p in _BLANK.split(text) if p.strip()]
    units = [("message", {"paragraph": i + 1}, p) for i, p in enumerate(paras)]
    return title, event_at, gmail_parties(payload), units


def row_text(payload: dict) -> str:
    parts = [f"{k}: {v}" for k, v in payload.items()
             if isinstance(v, str) and v.strip() and not _NUMERIC.fullmatch(v)]
    return "; ".join(parts)[:1000]


def project_batch(conn: psycopg.Connection, tenant_id: uuid.UUID, k: int, prose_kinds, prose_fn) -> int:
    """Insert the chunks of batch K (inside the projector's batch transaction). Returns the row count."""
    rows = 0
    recs = conn.execute(
        "SELECT record_id, version, kind, system, data_class, payload, source_path FROM ledger.source_record"
        " WHERE tenant_id = %(t)s AND batch_seq = %(k)s AND (kind = ANY(%(kinds)s)"
        " OR (kind = 'export' AND data_class = ANY(%(rc)s)))",
        {"t": tenant_id, "k": k, "kinds": list(prose_kinds), "rc": list(ROW_TEXT_CLASSES)}).fetchall()
    for r in recs:
        if r["kind"] == "export" and r["system"] == "gmail_oauth":
            title, event_at, parties, units = gmail_units(r["payload"])
            for n, (unit, loc, body) in enumerate(units):       # PRIVATE: only the message's parties may find it
                conn.execute(
                    "INSERT INTO projection.text_chunk (tenant_id, batch_seq, source_record_id, source_version,"
                    " chunk_no, chunk_locator, unit, kind, system, data_class, title, body, event_at, parties)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'private_comms',%s,%s,%s,%s)",
                    (tenant_id, k, r["record_id"], r["version"], n, json.dumps(loc), unit, r["kind"], r["system"],
                     title[:300], body, event_at, parties))
                rows += 1
            continue
        if r["kind"] == "export":
            body = row_text(r["payload"])
            title = r["source_path"].rsplit("/", 1)[-1]
            units = [("row", {}, body)] if body else []
            event_at, parties = None, []
        else:
            title, _, event_at, parties = prose_fn(r["kind"], r["record_id"], r["payload"])
            units = chunks(r["kind"], r["record_id"], r["payload"], title)
        for n, (unit, loc, body) in enumerate(units):
            conn.execute(
                "INSERT INTO projection.text_chunk (tenant_id, batch_seq, source_record_id, source_version, chunk_no,"
                " chunk_locator, unit, kind, system, data_class, title, body, event_at, parties)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant_id, k, r["record_id"], r["version"], n, json.dumps(loc), unit, r["kind"], r["system"],
                 r["data_class"], title[:300], body, event_at, parties))
            rows += 1
    return rows
