"""Generic CSV row reading for exports (values as written; 1-based data-row numbers; blank lines skipped).

Source-specific validation lives in per-source contracts. Nothing here interprets a value or resolves identity.
"""

from __future__ import annotations

import csv
import io


def read(data: bytes) -> tuple[list[str], list[dict]]:
    """(header, rows). Each row is a dict of the header's columns plus `__row__` (1-based data row) and
    `__width__` (how many values the row actually had, so a contract can reject short or long rows)."""
    reader = csv.reader(io.StringIO(data.decode("utf-8-sig")))
    header = next(reader, [])
    rows = []
    for i, values in enumerate(reader, start=1):
        if not any(v.strip() for v in values):
            continue
        rows.append({"__row__": i, "__width__": len(values), **dict(zip(header, values, strict=False))})
    return header, rows
