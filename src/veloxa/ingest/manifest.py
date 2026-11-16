"""Raw landing manifest (ARCHITECTURE.md §7.2): content hash, size and classification of every file.

Each file is read exactly ONCE. The bytes held in the entry are the bytes that were hashed and the bytes
that are later parsed, so a file that changes on disk after hashing cannot be parsed under the old hash.
`read_at` is the actual extraction time E of those bytes (§8.1).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..catalogue import Rule, classify


@dataclass(frozen=True)
class ManifestEntry:
    relpath: str
    path: Path
    data: bytes          # the exact bytes hashed; parsers read these, never the file again
    sha256: str
    size: int
    read_at: datetime    # actual extraction time of these bytes
    rule: Rule

    @property
    def suffix(self) -> str:
        return self.path.suffix.lower()


class DataMissing(Exception):
    pass


def build_manifest(pack_dir: Path, rules: list[Rule]) -> list[ManifestEntry]:
    if not pack_dir.exists():
        raise DataMissing(f"release-pack folder not found: {pack_dir.name}")
    if pack_dir.is_symlink() or not pack_dir.is_dir():
        raise DataMissing(f"release-pack path is not a plain folder: {pack_dir.name}")
    root = pack_dir.resolve()
    entries = []
    for path in sorted(pack_dir.rglob("*")):
        if path.is_symlink():
            raise DataMissing(f"symbolic links are not admitted: {path.relative_to(pack_dir).as_posix()}")
        if not path.is_file():
            continue
        if root not in path.resolve().parents:
            raise DataMissing("a file resolves outside the pack folder")
        relpath = path.relative_to(pack_dir).as_posix()
        rule = classify(rules, relpath)  # classify before reading: unknown files are never read
        data = path.read_bytes()
        read_at = datetime.now(UTC)
        entries.append(ManifestEntry(relpath, path, data, hashlib.sha256(data).hexdigest(), len(data), read_at, rule))
    if not entries:
        raise DataMissing(f"release-pack folder is empty: {pack_dir.name}")
    return entries


def manifest_sha256(entries: list[ManifestEntry]) -> str:
    lines = "".join(f"{e.relpath}\t{e.sha256}\t{e.size}\n" for e in sorted(entries, key=lambda e: e.relpath))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()
