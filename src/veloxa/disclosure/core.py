"""C19 disclosure auditor: the exact decision core ONLY (ARCHITECTURE.md §15.2-§15.6; ADR-13).

This module is a standalone mathematical core. It is NOT wired into any application path: protected payroll
aggregates remain withheld everywhere (metrics, Ask, briefing, evidence views, exports, APIs) until the full
auditor exists, i.e. the append-only per-principal ledger, serialized decisions per (tenant, scope, measure),
background rows, M2 segment construction from admitted records, and cross-channel integration tests. See
`DEPENDENCIES` below. Calling the core does not release anything.

What it decides (§15.4), from row STRUCTURE only (values are never an input, so decisions are simulatable):
1. policy gates: the statistic is permitted (SUM / AVG with derivable headcount / declared derived metrics; never
   MAX, MIN, MEDIAN, percentiles or variance), and every cohort has at least k members;
2. full-disclosure test: with A = L ∪ Q, a violation exists if any protected variable outside the principal's
   allowed set has its unit vector in the row space of A;
3. small-group test: variables with identical columns in A form atoms; a violation exists if the indicator of any
   union of atoms with fewer than k members lies in the row space;
4. outcome: allow (rows to append) or deny with a generic reason; variables already determined by L alone are
   reported as pre-existing exposure.
Exact arithmetic throughout (fractions.Fraction; floats are refused). Approximate (interval) disclosure is NOT
addressed (D-22 deferred). k and the ledger scope are D-14a parameters: the architecture default (k = 5,
per-principal ledgers) is used unless a tenant policy states otherwise; it is not changed here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from fractions import Fraction
from itertools import combinations

DEFAULT_K = 5   # D-14a default (architecture §26.1); owner confirmation pending, not changed here
PERMITTED = frozenset({"SUM", "AVG", "DERIVED"})
REFUSED = frozenset({"MAX", "MIN", "MEDIAN", "PERCENTILE", "VARIANCE"})

DEPENDENCIES = {
    "append_only_ledger": "policy.disclosure_ledger per (tenant, principal scope, measure); NOT IMPLEMENTED",
    "serialization": "audit and append in one transaction, serialized per (tenant, scope, measure); NOT IMPLEMENTED",
    "background_rows": "own values, individually authorized values, readable documents, public values; NOT "
                       "IMPLEMENTED (needs C32/current authorization to know what a principal may read)",
    "m2_segments": "person-segments from admitted payroll records with recorded pay changes; core helper only",
    "channels": "metrics, Ask, briefing, evidence views, agents (interviewer ledger), exports, APIs, caches; "
                "NOT INTEGRATED (every channel still refuses protected aggregates)",
    "test_oracle": "out-of-process statistical disclosure-control fuzzing (§15.6); NOT IMPLEMENTED",
}


class AuditorError(ValueError):
    """Malformed input. The caller must fail closed (deny)."""


Row = dict[str, Fraction]   # variable id (a person-segment) -> exact coefficient


def _exact(x) -> Fraction:
    if isinstance(x, bool) or not isinstance(x, (int, Fraction)):
        raise AuditorError(f"coefficients must be int or Fraction, never {type(x).__name__} (exact arithmetic)")
    return Fraction(x)


def row(coefficients: dict[str, int | Fraction]) -> Row:
    r = {v: _exact(c) for v, c in coefficients.items() if _exact(c) != 0}
    if not r:
        raise AuditorError("an empty coefficient row")
    return r


@dataclass
class Basis:
    """Incremental reduced row-echelon basis over exact rationals (§15.6)."""
    rows: list[tuple[str, Row]] = field(default_factory=list)   # (pivot variable, row normalised on its pivot)

    def reduce(self, r: Row) -> Row:
        out = dict(r)
        for pivot, b in self.rows:
            c = out.get(pivot, Fraction(0))
            if c:
                for v, bc in b.items():
                    nv = out.get(v, Fraction(0)) - c * bc
                    if nv:
                        out[v] = nv
                    else:
                        out.pop(v, None)
        return out

    def add(self, r: Row) -> bool:
        """Add a row; False if it was already in the row space."""
        red = self.reduce(r)
        if not red:
            return False
        pivot = min(red)
        p = red[pivot]
        new = {v: c / p for v, c in red.items()}
        reduced_rows = []
        for piv, b in self.rows:                       # keep the basis fully reduced on the new pivot
            c = b.get(pivot, Fraction(0))
            if c:
                b = {v: b.get(v, Fraction(0)) - c * new.get(v, Fraction(0)) for v in set(b) | set(new)}
                b = {v: x for v, x in b.items() if x}
            reduced_rows.append((piv, b))
        self.rows = reduced_rows + [(pivot, new)]
        return True

    def contains(self, r: Row) -> bool:
        return not self.reduce(r)

    def copy(self) -> Basis:
        return Basis(list(self.rows))


def basis_of(rows: Iterable[Row]) -> Basis:
    b = Basis()
    for r in rows:
        b.add(r)
    return b


def determined(b: Basis, variables: Iterable[str]) -> set[str]:
    """Variables whose unit vector lies in the row space (exactly determined)."""
    return {v for v in variables if b.contains({v: Fraction(1)})}


def atoms(rows: list[Row], variables: Iterable[str]) -> list[frozenset[str]]:
    """Variables with identical column vectors across the rows (§15.4 step 3)."""
    cols: dict[tuple, set[str]] = {}
    for v in variables:
        cols.setdefault(tuple(r.get(v, Fraction(0)) for r in rows), set()).add(v)
    return [frozenset(s) for s in cols.values()]


@dataclass(frozen=True)
class Query:
    statistic: str              # SUM | AVG | DERIVED | (refused: MAX, MIN, MEDIAN, PERCENTILE, VARIANCE)
    rows: tuple[Row, ...]       # the coefficient rows this release adds
    cohort_sizes: tuple[int, ...]
    cohort_membership_known: bool = True   # the principal may know each cohort's membership (§15.4 step 1)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str                       # generic: never mentions which variable or value
    append: tuple[Row, ...] = ()      # rows to append to the ledger when allowed
    pre_existing_exposure: frozenset[str] = frozenset()   # determined by L alone: for the policy administrator


def decide(ledger: list[Row], query: Query, protected: set[str], allowed_vars: set[str], k: int = DEFAULT_K,
           ) -> Decision:
    """§15.4, from structure only. `ledger` includes background rows. No value is ever an input."""
    if k < 2:
        raise AuditorError("k must be at least 2")
    pre = frozenset(determined(basis_of(ledger), protected - allowed_vars))
    if query.statistic in REFUSED or query.statistic not in PERMITTED:
        return Decision(False, "statistic_not_released", pre_existing_exposure=pre)
    if not query.cohort_membership_known:
        return Decision(False, "cohort_membership_not_known", pre_existing_exposure=pre)
    if any(n < k for n in query.cohort_sizes):
        return Decision(False, "cohort_below_k", pre_existing_exposure=pre)
    a_rows = list(ledger) + list(query.rows)
    b = basis_of(a_rows)
    newly = determined(b, protected - allowed_vars) - pre
    if newly:
        return Decision(False, "would_disclose", pre_existing_exposure=pre)
    vs = set().union(*a_rows) if a_rows else set()
    ats = [a for a in atoms(a_rows, vs) if a & protected]
    for size in range(1, len(ats) + 1):
        for combo in combinations(ats, size):
            members = frozenset().union(*combo)
            if len(members) >= k or members <= allowed_vars:
                continue
            if b.contains({v: Fraction(1) for v in members}):
                already = basis_of(ledger).contains({v: Fraction(1) for v in members})
                if not already:
                    return Decision(False, "small_group", pre_existing_exposure=pre)
    return Decision(True, "allowed", tuple(query.rows), pre)


def segment_sum_row(presence: dict[str, int | Fraction]) -> Row:
    """M2 stable-pay linkage: one variable per person-segment; a SUM over periods adds, per segment, the number of
    periods present (pro-rata for partial periods) as its coefficient (§15.2)."""
    return row(presence)
