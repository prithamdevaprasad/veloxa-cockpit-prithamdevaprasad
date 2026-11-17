"""Projection workers (ARCHITECTURE.md C10, §6.2): derive read models from SEALED ledger batches.

Projections are deterministic, idempotent per batch, tagged with the producing batch_seq, and rebuildable
from the ledger at any time. They extract what records say; they never resolve identities or decide
between conflicting sources.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime
from email.utils import getaddresses, parsedate_to_datetime

import psycopg

from . import audit
from .acl import projection as acl_projection
from .db import tenant_tx

_PERSON_SQL = """
INSERT INTO projection.person (tenant_id, batch_seq, entity_id, emp_id, full_name, work_email, department, title,
  manager_emp_id, join_date, location, status, exit_date, data_class, source_record_id, source_version)
SELECT r.tenant_id, r.batch_seq, 'person:' || (r.payload->>'Emp ID'), r.payload->>'Emp ID', r.payload->>'Full Name',
       nullif(r.payload->>'Work Email', ''), r.payload->>'Department', r.payload->>'Title',
       nullif(r.payload->>'Manager ID', ''), (r.payload->>'Join Date')::date, nullif(r.payload->>'Location', ''),
       r.payload->>'Status', nullif(r.payload->>'Exit Date', '')::date, r.data_class, r.record_id, r.version
  FROM ledger.source_record r
 WHERE r.tenant_id = %(t)s AND r.batch_seq = %(k)s AND r.system = 'keka_hrms' AND r.kind = 'export'
"""

_LEDGER_SQL = """
INSERT INTO projection.ledger_line (tenant_id, batch_seq, voucher, txn_date, vendor, cost_centre, amount_inr,
  narration, data_class, source_record_id, source_version)
SELECT r.tenant_id, r.batch_seq, r.payload->>'Voucher', (r.payload->>'Txn Date')::date, r.payload->>'Vendor / Payee',
       r.payload->>'Cost Centre', (r.payload->>'Amount (Rs.)')::bigint, r.payload->>'Narration', r.data_class,
       r.record_id, r.version
  FROM ledger.source_record r
 WHERE r.tenant_id = %(t)s AND r.batch_seq = %(k)s AND r.system = 'zoho_books' AND r.kind = 'export'
"""

PROSE_KINDS = ("email", "transcript", "document", "brief", "readme")
_MONTHS = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
                                       "Nov", "Dec"), start=1)}


def _prose(kind: str, record_id: str, payload: dict) -> tuple[str, str, datetime | None, list[str]]:
    """(title, body, event_at, parties) for a prose record."""
    if kind == "email":
        h = payload.get("headers", {})
        parties = sorted({addr.lower() for _, addr in getaddresses([h.get(x, "") for x in ("From", "To", "Cc")])
                          if addr})
        try:
            event_at = parsedate_to_datetime(h["Date"]) if h.get("Date") else None
        except (TypeError, ValueError):
            event_at = None
        return h.get("Subject", "(no subject)"), payload.get("body", ""), event_at, parties
    if "paragraphs" in payload:
        paras = [p for p in payload["paragraphs"] if p.strip()]
        tables = [" | ".join(cells) for t in payload.get("tables", []) for cells in t]
        return (paras[0] if paras else record_id), "\n".join(paras[1:] + tables), None, []
    if "pages" in payload:
        text = "\n".join(payload["pages"])
        first = next((ln for ln in text.splitlines() if ln.strip()), record_id)
        return first, text, None, []
    if "lines" in payload:
        lines = [ln for ln in payload["lines"] if ln.strip()]
        return (lines[0] if lines else record_id), "\n".join(lines[1:]), None, []
    return record_id, "", None, []


_PERIOD = re.compile(r"Period:\s*(?P<period>[^.]+)\.\s*Sent\s+(?P<d>\d{1,2})\s+(?P<m>[A-Z][a-z]{2})\s+(?P<y>\d{4})")
_FIGURE = re.compile(r"^(?P<metric>[A-Za-z][^\d]*?)\s+(?P<value>(?:Rs\.\s*)?\d[\d.,]*(?:\s+\S+)?)\s*$")


def parse_reported_figures(pages: list[str]) -> tuple[str | None, date | None, list[tuple[str, str]]]:
    """Read a formal document's key-numbers table: the lines between 'Key numbers' and the next blank-free section
    heading. Values are kept exactly as written; nothing is computed."""
    text = "\n".join(pages)
    period, issued = None, None
    m = _PERIOD.search(text)
    if m and m.group("m") in _MONTHS:
        period = m.group("period").strip()
        issued = date(int(m.group("y")), _MONTHS[m.group("m")], int(m.group("d")))
    figures: list[tuple[str, str]] = []
    lines = text.splitlines()
    if "Key numbers" not in lines:
        return period, issued, figures
    for line in lines[lines.index("Key numbers") + 1:]:
        line = line.strip()
        if not line or line.startswith("---"):
            break
        if line.lower() == "metric value":
            continue
        fm = _FIGURE.match(line)
        if not fm:
            break  # the table has ended (next section heading)
        figures.append((fm.group("metric").strip(), fm.group("value").strip()))
    return period, issued, figures


def _build_batch(conn: psycopg.Connection, tenant_id: uuid.UUID, k: int) -> int:
    rows = 0
    params = {"t": tenant_id, "k": k}
    rows += conn.execute(_PERSON_SQL, params).rowcount
    rows += conn.execute(_LEDGER_SQL, params).rowcount
    rows += acl_projection.project_batch(conn, tenant_id, k)  # C32 input: admitted ACL history only
    prose = conn.execute(
        "SELECT record_id, version, kind, system, data_class, payload FROM ledger.source_record"
        " WHERE tenant_id = %(t)s AND batch_seq = %(k)s AND kind = ANY(%(kinds)s)",
        {**params, "kinds": list(PROSE_KINDS)},
    ).fetchall()
    for r in prose:
        title, body, event_at, parties = _prose(r["kind"], r["record_id"], r["payload"])
        conn.execute(
            "INSERT INTO projection.document_text (tenant_id, batch_seq, source_record_id, source_version, kind,"
            " system, data_class, title, body, event_at, parties) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (tenant_id, k, r["record_id"], r["version"], r["kind"], r["system"], r["data_class"], title, body,
             event_at, parties),
        )
        rows += 1
        if r["kind"] == "document" and "pages" in r["payload"]:
            period, issued, figures = parse_reported_figures(r["payload"]["pages"])
            for metric, value in figures:
                conn.execute(
                    "INSERT INTO projection.reported_figure (tenant_id, batch_seq, source_record_id, source_version,"
                    " period, issued_on, metric, value_text, data_class) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (tenant_id, k, r["record_id"], r["version"], period, issued, metric, value, r["data_class"]),
                )
                rows += 1
    return rows


def build(conn: psycopg.Connection, tenant_id: uuid.UUID) -> list[tuple[int, int]]:
    """Project every sealed batch not yet projected, one transaction per batch. Returns [(batch_seq, rows)]."""
    built = []
    with tenant_tx(conn, tenant_id):
        sealed = [r["batch_seq"] for r in conn.execute(
            "SELECT batch_seq FROM ledger.admission_batch WHERE tenant_id = %s AND sealed ORDER BY 1", (tenant_id,))]
        done = {r["batch_seq"] for r in conn.execute(
            "SELECT batch_seq FROM projection.build WHERE tenant_id = %s", (tenant_id,))}
    for k in sealed:
        if k in done:
            continue
        with tenant_tx(conn, tenant_id, principal_id="projection-worker"):
            conn.execute("SELECT pg_advisory_xact_lock(hashtext('projection.build:' || %s))", (str(tenant_id),))
            if conn.execute("SELECT 1 FROM projection.build WHERE tenant_id = %s AND batch_seq = %s",
                            (tenant_id, k)).fetchone():
                continue  # a concurrent worker built it
            rows = _build_batch(conn, tenant_id, k)
            conn.execute("INSERT INTO projection.build (tenant_id, batch_seq, built_at, rows_built)"
                         " VALUES (%s, %s, %s, %s)", (tenant_id, k, datetime.now(UTC), rows))
            audit.append(conn, tenant_id, "projection.built", {"batch_seq": k, "rows": rows})
        built.append((k, rows))
    return built


def rebuild(conn: psycopg.Connection, tenant_id: uuid.UUID) -> list[tuple[int, int]]:
    """Drop this tenant's projections and rebuild them from the ledger (§6.2: projections are disposable)."""
    with tenant_tx(conn, tenant_id, principal_id="projection-worker"):
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('projection.build:' || %s))", (str(tenant_id),))
        for table in ("person", "ledger_line", "reported_figure", "document_text", "acl_history", "build"):
            conn.execute(f"DELETE FROM projection.{table} WHERE tenant_id = %s", (tenant_id,))  # fixed table names
    return build(conn, tenant_id)
