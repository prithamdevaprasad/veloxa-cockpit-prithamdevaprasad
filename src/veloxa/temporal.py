"""Temporal model (ARCHITECTURE.md §8): read basis, query modes and SQL predicate builder.

Axes: K (admission: a sealed batch_seq), R (source-recorded time) and V (valid time).
Modes (§8.3):
  CURRENT        K = latest batch released and sealed by now; R = V = now
  KNOWN_AT(X)    K = last batch whose release time is at or before the end of X; R = V = that batch's release time
  RECORDED_BY(X) K = current; R = end of X
  VALID_AT(X)    K = current; V = end of X; R unbounded (only material admitted by K)
A date-only X means the END of that day in the tenant's time zone (§8.3, T6). A KNOWN_AT date before any
admission yields K = 0 with `nothing_admitted` set; the caller says so and offers RECORDED_BY instead of
silently re-interpreting the question.

Predicates are built with psycopg.sql identifiers and named bind parameters only: no value is ever
interpolated into SQL text (§11.3).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from psycopg import sql


class Mode(StrEnum):
    CURRENT = "CURRENT"
    KNOWN_AT = "KNOWN_AT"
    RECORDED_BY = "RECORDED_BY"
    VALID_AT = "VALID_AT"


@dataclass(frozen=True)
class ReadBasis:
    tenant_id: uuid.UUID
    batch_seq: int                       # K: sealed admission point (0 = nothing admitted)
    mode: Mode
    recorded_cutoff: datetime | None     # R: material is usable only if recorded no later than this
    valid_at: datetime | None            # V: the instant at which facts must hold
    as_of: str | None = None             # the date or instant the user asked about, echoed in answers
    nothing_admitted: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)

    def banner(self) -> str:
        if self.mode == Mode.CURRENT:
            return f"Answering with current knowledge (batch K={self.batch_seq})"
        if self.nothing_admitted:
            return f"Nothing had been admitted as of {self.as_of}"
        if self.mode == Mode.KNOWN_AT:
            return f"Answering as known at {self.as_of} (batch K={self.batch_seq})"
        if self.mode == Mode.RECORDED_BY:
            return f"Answering from what had been recorded by {self.as_of}, as admitted by batch K={self.batch_seq}"
        return f"Answering what was true on {self.as_of}, by best current knowledge (batch K={self.batch_seq})"


def end_of_day(d: date, tz: str) -> datetime:
    """The exclusive end of day d in the tenant time zone: [d 00:00, d+1 00:00)."""
    return datetime.combine(d + timedelta(days=1), time.min, tzinfo=ZoneInfo(tz))


def _instant(as_of: date | datetime, tz: str) -> datetime:
    if isinstance(as_of, datetime):
        if as_of.tzinfo is None:
            raise ValueError("an instant must carry an explicit offset")
        return as_of
    return end_of_day(as_of, tz)


def resolve_basis(
    tenant_id: uuid.UUID,
    sealed_batches: list[tuple[int, datetime]],
    mode: Mode,
    as_of: date | datetime | None,
    tz: str,
    now: datetime,
) -> ReadBasis:
    """Pure resolution of a read basis from the sealed batches [(batch_seq, release_at)] and the clock."""
    released = sorted((k, r) for k, r in sealed_batches if r <= now)  # clamp to the clock
    current_k = released[-1][0] if released else 0
    if mode == Mode.CURRENT:
        if as_of is not None:
            raise ValueError("CURRENT takes no date")
        return ReadBasis(tenant_id, current_k, mode, now, now, nothing_admitted=current_k == 0)
    if as_of is None:
        raise ValueError(f"{mode} requires a date or instant")
    cutoff = _instant(as_of, tz)
    is_day = isinstance(as_of, date) and not isinstance(as_of, datetime)
    starts_at = cutoff - timedelta(days=1) if is_day else cutoff  # a date is in the future only if it has not begun
    if starts_at > now:
        raise ValueError("a date in the future cannot be answered")
    label = as_of.isoformat()
    if mode == Mode.KNOWN_AT:
        # A date means "by the end of that day" (exclusive end); an instant means "at or before that instant".
        known = [(k, r) for k, r in released if (r < cutoff if is_day else r <= cutoff)]
        if not known:
            return ReadBasis(tenant_id, 0, mode, None, None, label, nothing_admitted=True,
                             notes=("No material had been admitted by then. Ask instead what had been recorded by "
                                    "that date (RECORDED_BY), using everything admitted since.",))
        k, release_at = known[-1]
        return ReadBasis(tenant_id, k, mode, release_at, release_at, label)
    if mode == Mode.RECORDED_BY:
        return ReadBasis(tenant_id, current_k, mode, cutoff, None, label, nothing_admitted=current_k == 0)
    if mode == Mode.VALID_AT:
        v = cutoff - timedelta(microseconds=1) if is_day else cutoff  # last instant of the day
        return ReadBasis(tenant_id, current_k, mode, None, v, label, nothing_admitted=current_k == 0)
    raise ValueError(f"unknown mode {mode!r}")


@dataclass(frozen=True)
class Predicate:
    clause: sql.Composable
    params: dict


def predicate(
    basis: ReadBasis,
    alias: str,
    *,
    batch_column: str = "batch_seq",
    recorded_column: str | None = None,
    valid_column: str | None = None,
    latest_version: bool = True,
    k_only: bool = False,
) -> Predicate:
    """Visibility predicate for one table alias (§8.4 T1-T3).

    T1: batch_seq <= K (the table must hold only sealed material, or be joined to sealed batches).
    T2: if the basis has a recorded cutoff and the table records R, upper(recorded) <= R.
    T3: if the basis has a valid instant and the table records V, valid @> V.
    A table that lacks the R or V column the basis requires is refused rather than silently unfiltered.
    T8: with latest_version (the default; every projection carries source_record_id and source_version), a row is
    visible only if no LATER version of its source record is admitted at or below K, so a correction replaces
    the corrected row from its own batch onward and earlier knowledge points still see the original.
    """
    a = sql.Identifier(alias)
    parts = [sql.SQL("{}.{} <= %(k)s").format(a, sql.Identifier(batch_column))]
    params: dict = {"k": basis.batch_seq}
    # k_only: the caller applies R and V itself, per FACT (T2: recorded time belongs to the fact), e.g. the
    # evidence detectors, whose facts carry their own recorded and valid intervals. K is still enforced here.
    needs_r = basis.mode == Mode.RECORDED_BY and not k_only
    needs_v = basis.mode == Mode.VALID_AT and not k_only
    if needs_r and recorded_column is None:
        raise ValueError("RECORDED_BY requires a recorded-time column on every queried table")
    if needs_v and valid_column is None:
        raise ValueError("VALID_AT requires a valid-time column on every queried table")
    if recorded_column is not None and basis.recorded_cutoff is not None and needs_r:
        parts.append(sql.SQL("upper({}.{}) <= %(r)s").format(a, sql.Identifier(recorded_column)))
        params["r"] = basis.recorded_cutoff
    if valid_column is not None and basis.valid_at is not None and not k_only:
        parts.append(sql.SQL("{}.{} @> %(v)s::timestamptz").format(a, sql.Identifier(valid_column)))
        params["v"] = basis.valid_at
    if latest_version:
        parts.append(sql.SQL(
            "NOT EXISTS (SELECT 1 FROM ledger.source_record {lv} WHERE {lv}.tenant_id = {a}.tenant_id"
            " AND {lv}.record_id = {a}.source_record_id AND {lv}.version > {a}.source_version"
            " AND {lv}.batch_seq <= %(k)s)").format(lv=sql.Identifier(f"{alias}_later"), a=a))
    return Predicate(sql.SQL(" AND ").join(parts), params)
