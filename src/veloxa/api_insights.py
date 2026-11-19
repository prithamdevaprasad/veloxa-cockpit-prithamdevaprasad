"""Overview and analytics API: KPIs and chart series computed ONLY from admitted, sealed records the requester may
read now (the same per-document access as Ask). Nothing is estimated or invented.

**KPIs:**
- **Headcount:** the governed metric.
- **Board figures (ARR, customers, runway):** the latest values AS REPORTED in a formal board update, with the
  update's period and date. They are not live metrics.
- **Coverage:** sealed batches, records and sources.

**Series** (`GET /v1/analytics/series`): an aggregate over ONE admitted export system.
- `count` of rows, or `sum` of a numeric column, grouped by a column, optionally split by a second one.
- Only `internal` or `confidential` exports qualify. Personal and protected classes are refused.
- Every response names its source records and their count.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Literal

from fastapi import Header, Query
from fastapi.responses import JSONResponse

from . import ledger
from .db import connect, tenant_tx
from .temporal import Mode, resolve_basis

SERIES_CLASSES = ("internal", "confidential")
# Privacy-safe aggregates over PERSONAL sources: counts only, by columns that never identify a person. A requisition
# status or team is about a role opening, not an individual; names, notes and pipeline columns are never grouped.
# Proposed 19 Nov 2026 for owner confirmation.
PERSONAL_COUNT_COLUMNS = {"hiring_tracker": {"Status", "Team"}, "recruiting": {"Status", "Department"}}
_NUM = re.compile(r"^-?\d+(\.\d+)?$")


def _num(v):
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    if isinstance(v, str) and _NUM.match(v.replace(",", "").strip()):
        try:
            return Decimal(v.replace(",", "").strip())
        except InvalidOperation:
            return None
    return None


def register(app, *, state, principal_of, now_fn, tz_of) -> None:
    def ctx(conn, authorization):
        p = principal_of(conn, authorization, now_fn())
        if isinstance(p, JSONResponse):
            return p, None
        basis = resolve_basis(p.tenant_id, ledger.sealed_releases(conn, p.tenant_id), Mode.CURRENT, None,
                              tz_of(conn, p.tenant_id), now_fn())
        return p, basis

    def allowed(p, system, cls) -> bool:
        return cls not in ("sensitive_personal", "payroll_aggregate") and (p.access is None
                                                                            or p.access.allows(system, cls))

    @app.get("/v1/overview")
    def overview(authorization: str | None = Header(default=None)):
        from . import tools

        with connect(state["dsn"]) as conn:
            p, basis = ctx(conn, authorization)
            if basis is None:
                return p
            kpis, notes = [], []
            if basis.batch_seq:
                hc = tools.headcount(conn, basis, p)
                from .authz import evidence as ev_authz

                if p.access is not None:
                    hc, _ = ev_authz.gate(hc, p.access)
                if hc.facts:
                    kpis.append({"key": "headcount", "label": "Headcount", "value": hc.facts[0].value, "unit": None,
                                 "basis": "governed metric (HR export)", "evidence": hc.facts[0].inputs[:1]})
                with tenant_tx(conn, p.tenant_id, p.principal_id, basis.batch_seq):
                    figs = conn.execute(
                        "SELECT DISTINCT ON (metric) metric, value_text, period, issued_on, source_record_id,"
                        " data_class FROM projection.reported_figure WHERE batch_seq <= %s"
                        " ORDER BY metric, issued_on DESC NULLS LAST", (basis.batch_seq,)).fetchall()
                    systems = {r["record_id"]: r["system"] for r in conn.execute(
                        "SELECT DISTINCT ON (record_id) record_id, system FROM ledger.visible_source_record"
                        " WHERE record_id = ANY(%s) ORDER BY record_id, version DESC",
                        ([f["source_record_id"] for f in figs],)).fetchall()}
                    cov = conn.execute("SELECT count(DISTINCT record_id) AS records, count(DISTINCT system) AS sources"
                                       " FROM ledger.visible_source_record").fetchone()
                for f in figs:
                    if allowed(p, systems.get(f["source_record_id"], ""), f["data_class"]):
                        kpis.append({"key": f"reported:{f['metric'].lower()}", "label": f["metric"],
                                     "value": f["value_text"], "unit": None,
                                     "basis": f"as reported for {f['period']}"
                                              + (f", issued {f['issued_on'].isoformat()}" if f["issued_on"] else ""),
                                     "evidence": [f["source_record_id"]]})
                kpis.append({"key": "coverage", "label": "Evidence coverage",
                             "value": f"{cov['records']} records from {cov['sources']} sources", "unit": None,
                             "basis": f"admitted records up to release {basis.batch_seq}", "evidence": []})
            else:
                notes.append("nothing has been admitted yet: upload and authorise data to populate the overview")
            synthetic = conn.execute("SELECT synthetic FROM directory.tenant WHERE tenant_id = %s",
                                     (p.tenant_id,)).fetchone()["synthetic"]
        return {"kpis": kpis, "notes": notes, "synthetic": synthetic,
                "read_basis": {"batch_seq": basis.batch_seq, "banner": basis.banner()}}

    @app.get("/v1/analytics/sources")
    def sources(authorization: str | None = Header(default=None)):
        """Which export systems the requester can chart, with their columns (from the admitted rows)."""
        with connect(state["dsn"]) as conn:
            p, basis = ctx(conn, authorization)
            if basis is None:
                return p
            if not basis.batch_seq:
                return {"sources": []}
            with tenant_tx(conn, p.tenant_id, p.principal_id, basis.batch_seq):
                rows = conn.execute(
                    "SELECT system, data_class, count(DISTINCT record_id) AS n, min(source_path) AS file,"
                    " (array_agg(payload))[1] AS sample FROM ledger.visible_source_record WHERE kind = 'export'"
                    " GROUP BY system, data_class ORDER BY system").fetchall()
        out = []
        for r in rows:
            if not allowed(p, r["system"], r["data_class"]):
                continue
            if r["data_class"] in SERIES_CLASSES:
                cols = list(r["sample"].keys())
            elif r["data_class"] == "personal" and r["system"] in PERSONAL_COUNT_COLUMNS:
                cols = [c for c in r["sample"] if c in PERSONAL_COUNT_COLUMNS[r["system"]]]   # counts by these only
            else:
                continue
            out.append({"system": r["system"], "rows": r["n"], "file": r["file"].rsplit("/", 1)[-1], "columns": cols,
                        **({"counts_only": True} if r["data_class"] == "personal" else {})})
        return {"sources": out}

    @app.get("/v1/analytics/series")
    def series(system: str = Query(pattern=r"^[a-z][a-z0-9_]{1,39}$"), x: str = Query(max_length=60),
               agg: Literal["count", "sum"] = "count", y: str | None = Query(default=None, max_length=60),
               split: str | None = Query(default=None, max_length=60),
               authorization: str | None = Header(default=None)):
        with connect(state["dsn"]) as conn:
            p, basis = ctx(conn, authorization)
            if basis is None:
                return p
            if not basis.batch_seq:
                return {"points": [], "records": 0, "notes": ["nothing admitted"]}
            with tenant_tx(conn, p.tenant_id, p.principal_id, basis.batch_seq):
                rows = conn.execute("SELECT DISTINCT ON (record_id) record_id, data_class, payload, source_path"
                                    " FROM ledger.visible_source_record WHERE kind = 'export' AND system = %s"
                                    " ORDER BY record_id, version DESC", (system,)).fetchall()
        if not rows:
            return JSONResponse(status_code=404, content={"error": "not_found", "detail": "no admitted rows"})
        cls = {r["data_class"] for r in rows}
        safe_personal = (cls == {"personal"} and agg == "count" and x in PERSONAL_COUNT_COLUMNS.get(system, set())
                         and (split is None or split in PERSONAL_COUNT_COLUMNS[system]))
        if (not (cls <= set(SERIES_CLASSES) or safe_personal)) or not all(allowed(p, system, c) for c in cls):
            return JSONResponse(status_code=403, content={"error": "not_permitted",
                                                          "detail": "this source is not chartable for you"})
        if agg == "sum" and not y:
            return JSONResponse(status_code=422, content={"error": "invalid_request", "detail": "sum needs y"})
        groups: dict[tuple, Decimal] = {}
        skipped = 0
        for r in rows:
            pl = r["payload"]
            if x not in pl:
                return JSONResponse(status_code=422, content={"error": "invalid_request",
                                                              "detail": f"no column {x!r}"})
            key = (str(pl.get(x)), str(pl.get(split)) if split else None)
            if agg == "count":
                groups[key] = groups.get(key, Decimal(0)) + 1
            else:
                v = _num(pl.get(y))
                if v is None:
                    skipped += 1
                    continue
                groups[key] = groups.get(key, Decimal(0)) + v
        points = [{"x": k[0], "split": k[1], "value": float(v)} for k, v in sorted(groups.items())]
        return {"system": system, "x": x, "y": y, "agg": agg, "split": split, "points": points,
                "records": len(rows), "skipped_non_numeric": skipped,
                "sources": sorted({r["source_path"].rsplit("/", 1)[-1] for r in rows}),
                "basis": f"computed from {len(rows)} admitted rows (records up to release {basis.batch_seq})"}
