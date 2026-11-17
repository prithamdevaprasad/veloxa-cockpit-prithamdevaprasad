"""Release schedule, clock and admission decisions (ARCHITECTURE.md §8.2, §25.1; ASSUMPTIONS.md GR-01).

Admission is decided *before* any file of a pack is opened: an unreleased pack is never read.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import config


@dataclass(frozen=True)
class Pack:
    id: str
    batch_seq: int
    folder: str
    release_at: datetime


@dataclass(frozen=True)
class Schedule:
    tenant_slug: str
    time_zone: str
    packs: tuple[Pack, ...]

    def pack(self, pack_id: str) -> Pack:
        for p in self.packs:
            if p.id == pack_id:
                return p
        raise AdmissionRefused("unknown_pack", f"pack {pack_id!r} is not in the release schedule")

    def released(self, now: datetime) -> list[Pack]:
        return [p for p in self.packs if p.release_at <= now]


class AdmissionRefused(Exception):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def load_schedule(path: Path | None = None) -> Schedule:
    path = path or (config.config_dir() / "release_schedule.toml")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    packs = []
    for raw in data["pack"]:
        release_at = datetime.fromisoformat(raw["release_at"])
        if release_at.tzinfo is None:
            raise ValueError(f"release time for {raw['id']} must carry an explicit offset")
        packs.append(Pack(raw["id"], int(raw["batch_seq"]), raw["folder"], release_at))
    packs.sort(key=lambda p: p.batch_seq)
    if [p.batch_seq for p in packs] != list(range(1, len(packs) + 1)):
        raise ValueError("pack batch_seq values must be 1..n without gaps")
    if any(a.release_at >= b.release_at for a, b in zip(packs, packs[1:], strict=False)):
        raise ValueError("pack release times must be strictly increasing")
    return Schedule(data["tenant"]["slug"], data["tenant"]["time_zone"], tuple(packs))


def current_time(env: Mapping[str, str] | None = None) -> datetime:
    """The clock used by the command line and the API.

    VELOXA_CLOCK is honoured ONLY in test mode (VELOXA_TEST_MODE=1). Outside test mode, setting it is an
    error rather than being silently ignored, so a deployment can never run on a substitute clock.
    """
    env = os.environ if env is None else env
    override = env.get("VELOXA_CLOCK")
    if not override:
        return datetime.now(UTC)
    if env.get("VELOXA_TEST_MODE") != "1":
        raise RuntimeError("VELOXA_CLOCK is only honoured in test mode (VELOXA_TEST_MODE=1); unset it")
    return effective_now(override=override)


def effective_now(real_now: datetime | None = None, override: str | None = None) -> datetime:
    """Test clock. An override may move the clock back, never forward (it is clamped to the real time)."""
    real = real_now or datetime.now(UTC)
    if not override:
        return real
    demo = datetime.fromisoformat(override)
    if demo.tzinfo is None:
        raise ValueError("VELOXA_CLOCK must carry an explicit offset")
    return min(demo, real)


def check_admissible(schedule: Schedule, pack_id: str, now: datetime, sealed: set[int]) -> Pack:
    pack = schedule.pack(pack_id)
    if now < pack.release_at:
        raise AdmissionRefused(
            "not_yet_released",
            f"pack {pack.id} is released at {pack.release_at.isoformat()}; it cannot be admitted or read before then",
        )
    missing = [p.id for p in schedule.packs if p.batch_seq < pack.batch_seq and p.batch_seq not in sealed]
    if missing:
        raise AdmissionRefused("out_of_order", f"pack {pack.id} requires earlier packs first: {', '.join(missing)}")
    return pack


@dataclass(frozen=True)
class Authorisation:
    pack_id: str
    baseline: bool
    approved_by: str
    approved_on: object
    decision_record: str


def load_authorisations(path: Path | None = None) -> dict[str, Authorisation]:
    """Owner-authorised packs. A missing file authorises nothing (fail closed); an incomplete entry is refused."""
    path = path or (config.config_dir() / "admission_authorisations.toml")
    if not path.is_file():
        return {}
    out = {}
    for raw in tomllib.loads(path.read_text(encoding="utf-8")).get("pack", []):
        for k in ("id", "approved_by", "approved_on", "decision_record"):
            if not str(raw.get(k, "")).strip():
                raise ValueError(f"admission authorisation {raw!r}: {k} is required (owner decision)")
        out[raw["id"]] = Authorisation(raw["id"], bool(raw.get("baseline", False)), raw["approved_by"],
                                       raw["approved_on"], raw["decision_record"])
    return out


def require_authorised(pack_id: str, authorisations: dict[str, Authorisation]) -> Authorisation:
    """Decided before any file of the pack is opened."""
    if pack_id not in authorisations:
        raise AdmissionRefused("not_authorised", f"pack {pack_id} is not authorised for admission "
                               "(config/admission_authorisations.toml); its catalogue rules and data classes need "
                               "an owner decision first")
    return authorisations[pack_id]
