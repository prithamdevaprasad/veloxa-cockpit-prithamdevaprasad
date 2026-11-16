"""Governed metrics engine and policy compiler (ARCHITECTURE.md C13, §11).

- Only DECLARED, versioned metric definitions run (config/metrics.toml).
- Sources, columns, dimensions, filters and measures come from the registry below; the compiler emits SQL from
  registry identifiers and named bind parameters only. Nothing from a request or a model is ever spliced
  into SQL text, and no SQL is model-authored.
- Every query carries the tenant context (RLS) and the read basis's temporal predicate (§8.4).
- Snapshot sources read the latest snapshot at or before K; event sources read every sealed batch <= K.
- A definition with protected inputs is an aggregate over protected measures (§15). Until the disclosure
  auditor exists, the engine refuses to release it (fail closed).
- Results carry lineage: the source records that produced each value.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
from psycopg import sql

from . import config
from .db import tenant_tx
from .temporal import Mode, ReadBasis, predicate


@dataclass(frozen=True)
class Source:
    table: str
    snapshot: bool                          # True: use the latest snapshot <= K; False: union of batches <= K
    numeric: frozenset[str]
    text: frozenset[str]
    dimensions: dict[str, str]              # dimension -> SQL expression template over alias "s"
    filters: dict[str, tuple[str, str]]     # filter -> (column, operator)


SOURCES: dict[str, Source] = {
    "person": Source(
        "person", True, frozenset(), frozenset(),
        {"department": '"s"."department"', "status": '"s"."status"'},
        {"department": ("department", "="), "status": ("status", "=")},
    ),
    "ledger_line": Source(
        "ledger_line", False, frozenset({"amount_inr"}), frozenset(),
        {"cost_centre": '"s"."cost_centre"', "vendor": '"s"."vendor"',
         "month": "to_char(\"s\".\"txn_date\", 'YYYY-MM')"},
        {"cost_centre": ("cost_centre", "="), "vendor": ("vendor", "="),
         "from_date": ("txn_date", ">="), "to_date": ("txn_date", "<=")},
    ),
    "reported_figure": Source(
        "reported_figure", False, frozenset(), frozenset({"value_text"}),
        {"metric": '"s"."metric"', "period": '"s"."period"'},
        {"metric": ("metric", "="), "period": ("period", "=")},
    ),
}

SUPPORTED_MODES = {Mode.CURRENT, Mode.KNOWN_AT}  # projections carry no R/V columns yet (§8.3)
PROTECTED_CLASSES = ("sensitive_personal", "payroll_aggregate")


class MetricError(Exception):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason  # unknown_metric | invalid_request | unsupported_mode | disclosure_auditor_unavailable


@dataclass(frozen=True)
class Definition:
    name: str
    version: int
    description: str
    unit: str
    source: str
    measure: str
    dimensions: tuple[str, ...]
    filters: tuple[str, ...]
    qualifiers: tuple[str, ...]
    protected_inputs: tuple[str, ...]

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"


@dataclass
class MetricRow:
    dims: dict
    value: object
    inputs: list[str]  # source record ids


@dataclass
class MetricResult:
    definition: Definition
    basis: ReadBasis
    rows: list[MetricRow]
    qualifiers: list[str] = field(default_factory=list)


def load_definitions(path: Path | None = None) -> dict[str, Definition]:
    path = path or (config.config_dir() / "metrics.toml")
    defs: dict[str, Definition] = {}
    for raw in tomllib.loads(path.read_text(encoding="utf-8"))["metric"]:
        d = Definition(raw["name"], int(raw["version"]), raw["description"], raw["unit"], raw["source"],
                       raw["measure"], tuple(raw.get("dimensions", ())), tuple(raw.get("filters", ())),
                       tuple(raw.get("qualifiers", ())), tuple(raw.get("protected_inputs", ())))
        _validate(d)
        if d.name in defs:
            raise ValueError(f"metric {d.name} declared twice")
        defs[d.name] = d
    return defs


def _validate(d: Definition) -> None:
    src = SOURCES.get(d.source)
    if src is None:
        raise ValueError(f"{d.ref}: unregistered source {d.source!r}")
    kind, _, col = d.measure.partition(":")
    if kind == "count" and not col:
        pass
    elif kind == "sum" and col in src.numeric:
        pass
    elif kind == "reported" and col in src.text:
        pass
    else:
        raise ValueError(f"{d.ref}: measure {d.measure!r} is not allowed on source {d.source!r}")
    bad = [x for x in d.dimensions if x not in src.dimensions] + [x for x in d.filters if x not in src.filters]
    if bad:
        raise ValueError(f"{d.ref}: unregistered dimensions or filters {bad}")


def compile_query(d: Definition, basis: ReadBasis, dims: list[str], filters: dict) -> tuple[sql.Composed, dict]:
    if basis.mode not in SUPPORTED_MODES:
        raise MetricError("unsupported_mode", f"{d.ref} cannot be evaluated in {basis.mode} mode yet")
    src = SOURCES[d.source]
    if any(x not in d.dimensions for x in dims):
        raise MetricError("invalid_request", f"{d.ref}: dimension not allowed")
    if any(x not in d.filters for x in filters):
        raise MetricError("invalid_request", f"{d.ref}: filter not allowed")
    kind, _, col = d.measure.partition(":")
    if kind == "reported" and "metric" not in dims:
        raise MetricError("invalid_request", f"{d.ref}: reported figures must be grouped by figure name")
    table = sql.SQL("projection.{}").format(sql.Identifier(src.table))
    p = predicate(basis, "s")
    # Protected pay classes are excluded explicitly (defence in depth on top of RLS): releasing them needs the
    # disclosure auditor, whichever role runs the query.
    where = [sql.SQL('"s"."tenant_id" = %(tenant)s'), p.clause,
             sql.SQL('"s"."data_class" <> ALL(%(protected)s)')]
    params = {"tenant": basis.tenant_id, "protected": list(PROTECTED_CLASSES), **p.params}
    if src.snapshot:  # latest snapshot at or before K
        where.append(sql.SQL('"s"."batch_seq" = (SELECT max("x"."batch_seq") FROM {} "x"'
                             ' WHERE "x"."tenant_id" = %(tenant)s AND "x"."batch_seq" <= %(k)s)').format(table))
    for i, (name, value) in enumerate(sorted(filters.items())):
        column, op = src.filters[name]
        where.append(sql.SQL('"s".{} {} %({})s').format(sql.Identifier(column), sql.SQL(op), sql.SQL(f"f{i}")))
        params[f"f{i}"] = value
    if kind == "count":
        measure = sql.SQL("count(*)")
    elif kind == "sum":
        measure = sql.SQL('sum("s".{})').format(sql.Identifier(col))
    else:  # reported: the value as written; one row per group
        measure = sql.SQL('min("s".{})').format(sql.Identifier(col))
    dim_exprs = [sql.SQL(src.dimensions[x]) for x in dims]
    select = [sql.SQL("{} AS {}").format(e, sql.Identifier(x)) for e, x in zip(dim_exprs, dims, strict=True)]
    select += [sql.SQL("{} AS value").format(measure),
               sql.SQL('array_agg(DISTINCT "s"."source_record_id" ORDER BY "s"."source_record_id") AS inputs')]
    query = sql.SQL("SELECT {} FROM {} \"s\" WHERE {}").format(
        sql.SQL(", ").join(select), table, sql.SQL(" AND ").join(where))
    if dims:
        query = sql.SQL("{} GROUP BY {} ORDER BY {}").format(
            query, sql.SQL(", ").join(dim_exprs), sql.SQL(", ").join(dim_exprs))
    return query, params


def run(conn: psycopg.Connection, d: Definition, basis: ReadBasis, dims: list[str] | None = None,
        filters: dict | None = None) -> MetricResult:
    if d.protected_inputs:
        raise MetricError("disclosure_auditor_unavailable",
                          f"{d.ref} aggregates protected measures; it is withheld until the disclosure auditor exists")
    if basis.nothing_admitted or basis.batch_seq == 0:
        return MetricResult(d, basis, [], list(d.qualifiers) + ["nothing had been admitted at this point"])
    query, params = compile_query(d, basis, dims or [], filters or {})
    with tenant_tx(conn, basis.tenant_id, batch_seq=basis.batch_seq):
        rows = conn.execute(query, params).fetchall()
    out = []
    for r in rows:
        if r["value"] is None and not (dims or []):
            continue  # no matching rows
        out.append(MetricRow({x: r[x] for x in (dims or [])}, r["value"], list(r["inputs"] or [])))
    return MetricResult(d, basis, out, list(d.qualifiers))
