"""Leakage canary harness (ARCHITECTURE.md §8.5, §21.2).

A *surface* is any function that reads data for a read basis and returns the identifiers it exposes.
For every knowledge point K, a surface must expose exactly the canary items admitted in batches <= K:
nothing from a later batch (leakage) and nothing missing from an earlier one (loss).

New surfaces (metrics, query tools, answers, briefings, caches) register here as they are built, so every
surface is checked at every batch boundary.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

Surface = Callable[[object, object], set[str]]  # (conn, ReadBasis) -> identifiers exposed


@dataclass
class CanaryReport:
    leaks: list[tuple[str, int, set[str]]]     # (surface, K, identifiers from batches > K)
    losses: list[tuple[str, int, set[str]]]    # (surface, K, identifiers from batches <= K that are missing)

    @property
    def clean(self) -> bool:
        return not self.leaks and not self.losses


def run(surfaces: dict[str, Surface], conn, bases_by_k: dict[int, object],
        canaries_by_batch: dict[int, set[str]]) -> CanaryReport:
    """bases_by_k: a read basis for each K to test. canaries_by_batch: canary identifiers admitted per batch.
    Each surface's output is intersected with the canary universe, so unrelated items do not count."""
    universe = set().union(*canaries_by_batch.values()) if canaries_by_batch else set()
    leaks, losses = [], []
    for name, surface in surfaces.items():
        for k, basis in sorted(bases_by_k.items()):
            seen = surface(conn, basis) & universe
            allowed = set().union(*(ids for b, ids in canaries_by_batch.items() if b <= k)) if k else set()
            later = seen - allowed
            missing = allowed - seen
            if later:
                leaks.append((name, k, later))
            if missing:
                losses.append((name, k, missing))
    return CanaryReport(leaks, losses)
