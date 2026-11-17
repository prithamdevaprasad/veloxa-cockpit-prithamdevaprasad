"""Seeded fuzzing of the C19 exact core (SYNTHETIC structures only; no values exist in these tests).

This is NOT the §15.6 test oracle: the architecture's oracle (sdcTable, run out of process) needs an R runtime
that is not available in this environment, so the oracle gate stays explicitly incomplete. What this file adds is
a cross-check against a structurally different algorithm written separately here (fraction-free Bareiss
elimination over integers, rank comparison), plus invariance and monotonicity properties.
"""

import random
from fractions import Fraction

from veloxa.disclosure import core

VARS = [f"v{i}" for i in range(6)]


def bareiss_rank(matrix: list[list[int]]) -> int:
    m = [r[:] for r in matrix if any(r)]
    if not m:
        return 0
    rows, cols, rank, prev = len(m), len(m[0]), 0, 1
    for c in range(cols):
        pivot = next((r for r in range(rank, rows) if m[r][c]), None)
        if pivot is None:
            continue
        m[rank], m[pivot] = m[pivot], m[rank]
        for r in range(rank + 1, rows):
            m[r] = [(m[rank][c] * m[r][j] - m[r][c] * m[rank][j]) // prev for j in range(cols)]
        prev = m[rank][c]
        rank += 1
        if rank == rows:
            break
    return rank


def as_matrix(rows):
    return [[int(r.get(v, 0)) for v in VARS] for r in rows]


def random_rows(rng, n):
    out = []
    while len(out) < n:
        r = {v: rng.randint(-2, 3) for v in VARS if rng.random() < 0.5}
        r = {v: c for v, c in r.items() if c}
        if r:
            out.append(core.row(r))
    return out


def test_determined_variables_agree_with_an_independent_rank_criterion():
    rng = random.Random(20261117)
    for _ in range(400):
        rows = random_rows(rng, rng.randint(1, 7))
        mine = core.determined(core.basis_of(rows), VARS)
        base = bareiss_rank(as_matrix(rows))
        theirs = {v for v in VARS if bareiss_rank(as_matrix(rows + [{v: 1}])) == base}
        assert mine == theirs, rows


def test_determination_is_invariant_under_scaling_and_order_and_monotone_in_rows():
    rng = random.Random(7)
    for _ in range(200):
        rows = random_rows(rng, rng.randint(1, 6))
        d = core.determined(core.basis_of(rows), VARS)
        factors = [Fraction(rng.choice([-3, 2, 5]), 7) for _ in rows]          # ONE nonzero factor per row
        scaled = [{v: c * f for v, c in r.items()} for r, f in zip(rows, factors, strict=True)]
        assert core.determined(core.basis_of(list(reversed(scaled))), VARS) == d
        more = rows + random_rows(rng, 1)
        assert d <= core.determined(core.basis_of(more), VARS)     # knowledge never shrinks


def test_an_allowed_decision_never_newly_determines_a_protected_variable():
    rng = random.Random(99)
    protected = set(VARS)
    for _ in range(300):
        ledger = random_rows(rng, rng.randint(0, 4))
        q = random_rows(rng, 1)[0]
        d = core.decide(ledger, core.Query("SUM", (q,), (len(q),)), protected, set(), k=2)
        if d.allowed:
            before = core.determined(core.basis_of(ledger), protected)
            after = core.determined(core.basis_of(ledger + [q]), protected)
            assert after == before
