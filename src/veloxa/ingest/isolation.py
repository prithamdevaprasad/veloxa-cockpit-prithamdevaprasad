"""C06 ingestion isolation (ARCHITECTURE.md §7.2): limits, type checks, archive guards and a time-boxed parser.

**Limits.** Every check refuses the WHOLE pack with a reason code. A failed admission commits nothing (DEC-24),
and the audit records the pack and reason code only. This is "quarantine by refusal":
- no copy of a refused file is made anywhere;
- the file stays where it was dropped;
- the operator sees the file path on the console only.

A quarantine STORE (§7.2) is still not built.

| Check | Default | Setting |
|---|---|---|
| Per-file size, checked from `stat` BEFORE the file is read | 50 MiB | `VELOXA_INGEST_MAX_FILE_BYTES` |
| Pack total | 500 MiB | `VELOXA_INGEST_MAX_PACK_BYTES` |
| File count | 5,000 | `VELOXA_INGEST_MAX_FILES` |
| Type: content must match the suffix (magic bytes) | always | — |
| Zip archives (`.xlsx`, `.docx`) | 10,000 members; 200 MiB; ratio 200 | `VELOXA_INGEST_MAX_UNZIPPED_BYTES` |
| Separate parser process, killed on timeout | 120 s per pack | `VELOXA_INGEST_PARSE_TIMEOUT_S` |

The type check rejects a `.pdf` that is not a PDF, an `.xlsx` that is not a zip, and text that is not UTF-8.
Encrypted zip members are refused too.

Set `VELOXA_INGEST_PARSE_ISOLATION=inline` only for debugging. It keeps every other check.
"""

from __future__ import annotations

import io
import os
import zipfile
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from concurrent.futures.process import BrokenProcessPool

MAGIC = {".pdf": (b"%PDF-",), ".xlsx": (b"PK\x03\x04",), ".docx": (b"PK\x03\x04",)}
TEXT = {".eml", ".txt", ".jsonl", ".csv", ".md"}
ZIPPED = {".xlsx", ".docx"}
MAX_MEMBERS = 10_000
MAX_RATIO = 200


class Quarantined(Exception):
    """The pack is refused; `reason` is the audited code, `relpath` is for the operator console only."""

    def __init__(self, reason: str, relpath: str, detail: str = ""):
        super().__init__(f"{reason}: {relpath}" + (f" ({detail})" if detail else ""))
        self.reason, self.relpath = reason, relpath


def _int(name: str, default: int) -> int:
    v = os.environ.get(name, "").strip()
    return int(v) if v.isdigit() and int(v) > 0 else default


def limits() -> dict:
    return {"file": _int("VELOXA_INGEST_MAX_FILE_BYTES", 50 * 2**20),
            "pack": _int("VELOXA_INGEST_MAX_PACK_BYTES", 500 * 2**20),
            "files": _int("VELOXA_INGEST_MAX_FILES", 5000),
            "unzipped": _int("VELOXA_INGEST_MAX_UNZIPPED_BYTES", 200 * 2**20),
            "timeout": _int("VELOXA_INGEST_PARSE_TIMEOUT_S", 120)}


def check_before_read(relpath: str, size: int, seen_files: int, seen_bytes: int, lim: dict) -> None:
    if seen_files + 1 > lim["files"]:
        raise Quarantined("too_many_files", relpath, f"limit {lim['files']}")
    if size > lim["file"]:
        raise Quarantined("file_too_large", relpath, f"limit {lim['file']} bytes")
    if seen_bytes + size > lim["pack"]:
        raise Quarantined("pack_too_large", relpath, f"limit {lim['pack']} bytes")


def check_content(relpath: str, suffix: str, data: bytes, lim: dict) -> None:
    suffix = suffix.lower()
    magic = MAGIC.get(suffix)
    if magic and not data.startswith(magic):
        raise Quarantined("type_mismatch", relpath, f"content is not {suffix}")
    if suffix in TEXT:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise Quarantined("type_mismatch", relpath, "text is not UTF-8") from None
    if suffix in ZIPPED:
        try:
            infos = zipfile.ZipFile(io.BytesIO(data)).infolist()
        except zipfile.BadZipFile:
            raise Quarantined("type_mismatch", relpath, "not a readable archive") from None
        total = sum(i.file_size for i in infos)
        if len(infos) > MAX_MEMBERS or total > lim["unzipped"]:
            raise Quarantined("archive_limits", relpath, f"{len(infos)} members, {total} bytes uncompressed")
        for i in infos:
            if i.flag_bits & 0x1:
                raise Quarantined("archive_encrypted", relpath)
            if i.compress_size and i.file_size / i.compress_size > MAX_RATIO:
                raise Quarantined("archive_limits", relpath, "compression ratio too high")


def _parse_all(items: list[tuple[bytes, str, str]]):
    from .parsers import parse
    return [parse(data, suffix, mode) for data, suffix, mode in items]


def parse_all(items: list[tuple[str, bytes, str, str]], lim: dict | None = None) -> list:
    """Parse (relpath, data, suffix, mode) items; returns envelope lists in order. Parser errors propagate as
    before; a timeout or a crashed parser process refuses the pack."""
    lim = lim or limits()
    payload = [(d, s, m) for _, d, s, m in items]
    if os.environ.get("VELOXA_INGEST_PARSE_ISOLATION", "process").strip() == "inline":
        return _parse_all(payload)
    pool = ProcessPoolExecutor(max_workers=1)
    try:
        return pool.submit(_parse_all, payload).result(timeout=lim["timeout"])
    except FutureTimeout:
        raise Quarantined("parse_timeout", "(pack)", f"over {lim['timeout']} s") from None
    except BrokenProcessPool:
        raise Quarantined("parser_crashed", "(pack)") from None
    finally:
        procs = list(getattr(pool, "_processes", {}).values())
        for p in procs:                                  # a hung parser is killed, not waited for
            p.kill()
        pool.shutdown(wait=False, cancel_futures=True)
        for p in procs:                                  # reap: no parser process outlives the call (it would hold
            p.join(10)                                   # inherited handles, e.g. a caller's output pipe, open)
