"""Versioned parsers: one source file -> source-record envelopes with exact locators (ARCHITECTURE.md §7.2).

Parsers extract what the file says. They do not interpret, normalise meanings, resolve identities or
decide between sources: those are later, human-governed steps (§7.5, §7.6).
"""

from __future__ import annotations

import datetime as dt
import email
import io
from dataclasses import dataclass
from decimal import Decimal
from email import policy

PARSER_VERSIONS = {
    "xlsx_rows": "xlsx-rows/1",
    "docx": "docx/1",
    "pdf": "pdf/1",
    "eml": "eml/1",
    "text": "text/1",
    "jsonl_rows": "jsonl-rows/1",
}


@dataclass(frozen=True)
class Envelope:
    locator: dict
    payload: dict
    parser_version: str

    def locator_key(self) -> str:
        loc = self.locator
        if "sheet" in loc:
            return f"{loc['sheet']}!r{loc['row']}"
        if "line" in loc:
            return f"line{loc['line']}"
        return "document"


def _jsonable(value):
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    return str(value)


def parse_xlsx_rows(data: bytes) -> list[Envelope]:
    """One envelope per data row. The header row is the first row with at least three non-empty cells;
    rows above it (titles) are kept as file context in each row's locator, not interpreted."""
    import openpyxl

    # Not read-only mode: row numbering must start at sheet row 1 so locators match the file exactly.
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    out: list[Envelope] = []
    try:
        for ws in wb.worksheets:
            rows = list(ws.iter_rows(min_row=1, max_row=ws.max_row, values_only=True))
            header_idx = next(
                (i for i, r in enumerate(rows) if sum(c is not None and str(c).strip() != "" for c in r) >= 3),
                None,
            )
            if header_idx is None:
                continue
            header = [str(c).strip() if c is not None else f"column_{j + 1}" for j, c in enumerate(rows[header_idx])]
            if len(set(header)) != len(header):
                raise ValueError(f"duplicate column names in sheet {ws.title!r}")
            for i in range(header_idx + 1, len(rows)):
                cells = rows[i]
                if all(c is None or str(c).strip() == "" for c in cells):
                    continue
                payload = {header[j]: _jsonable(cells[j]) if j < len(cells) else None for j in range(len(header))}
                out.append(Envelope({"sheet": ws.title, "row": i + 1, "header_row": header_idx + 1},
                                    payload, PARSER_VERSIONS["xlsx_rows"]))
    finally:
        wb.close()
    return out


def parse_docx(data: bytes) -> list[Envelope]:
    import docx

    d = docx.Document(io.BytesIO(data))
    paragraphs = [p.text for p in d.paragraphs]
    tables = [[[c.text for c in row.cells] for row in t.rows] for t in d.tables]
    cp = d.core_properties
    payload = {
        "paragraphs": paragraphs,  # index i is paragraph ¶(i+1), counting the title as ¶1
        "tables": tables,
        "properties": {"created": _jsonable(cp.created), "modified": _jsonable(cp.modified)},
    }
    return [Envelope({"paragraphs": len(paragraphs), "tables": len(tables)}, payload, PARSER_VERSIONS["docx"])]


def parse_pdf(data: bytes) -> list[Envelope]:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = [pg.extract_text() or "" for pg in pdf.pages]
        meta = {k: _jsonable(v) for k, v in (pdf.metadata or {}).items() if k in ("CreationDate", "ModDate")}
    return [Envelope({"pages": len(pages)}, {"pages": pages, "metadata": meta}, PARSER_VERSIONS["pdf"])]


_EMAIL_HEADERS = ("From", "To", "Cc", "Date", "Subject", "Message-ID", "In-Reply-To", "References")


def parse_eml(data: bytes) -> list[Envelope]:
    msg = email.message_from_bytes(data, policy=policy.default)
    headers = {h: str(msg[h]) for h in _EMAIL_HEADERS if msg[h] is not None}
    body_parts, attachments = [], []
    for part in msg.walk():
        if part.is_multipart():
            continue
        if part.get_filename():
            attachments.append({"filename": part.get_filename(), "content_type": part.get_content_type()})
        elif part.get_content_type() == "text/plain":
            body_parts.append(part.get_content())
    payload = {"headers": headers, "body": "\n".join(body_parts), "attachments": attachments}
    return [Envelope({"message": 1}, payload, PARSER_VERSIONS["eml"])]


def parse_text(data: bytes) -> list[Envelope]:
    lines = data.decode("utf-8").splitlines()  # index i is raw line i+1
    return [Envelope({"lines": len(lines)}, {"lines": lines}, PARSER_VERSIONS["text"])]


def parse_jsonl_rows(data: bytes) -> list[Envelope]:
    """One envelope per non-empty line of a JSON Lines export (e.g. a connector's ACL history stream). Each line
    must be a JSON object; anything else refuses the whole file (fail closed). Values are kept as written."""
    import json

    out = []
    for i, line in enumerate(data.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        obj = json.loads(line)
        if not isinstance(obj, dict):
            raise ValueError(f"line {i} is not a JSON object")
        out.append(Envelope({"line": i}, obj, PARSER_VERSIONS["jsonl_rows"]))
    return out


def parse(data: bytes, suffix: str, mode: str) -> list[Envelope]:
    """Parse the exact bytes that were hashed (never re-read the file)."""
    if mode == "none":
        return []
    suffix = suffix.lower()
    if mode == "rows":
        if suffix == ".jsonl":
            return parse_jsonl_rows(data)
        if suffix != ".xlsx":
            raise ValueError(f"row extraction is only defined for .xlsx and .jsonl, not {suffix}")
        return parse_xlsx_rows(data)
    if mode == "document":
        parser = {".docx": parse_docx, ".pdf": parse_pdf, ".eml": parse_eml, ".txt": parse_text}.get(suffix)
        if parser is None:
            raise ValueError(f"no document parser for {suffix}")
        return parser(data)
    raise ValueError(f"unknown record mode {mode!r}")
