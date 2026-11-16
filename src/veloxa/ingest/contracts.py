"""Export validation contracts (ARCHITECTURE.md §7.2): schema and semantic checks on parsed rows.

A violation refuses the whole pack (fail closed). Messages name the file, sheet row and column only, never a
cell value, so they are safe to log and to audit. A quarantine store (§7.2) is not built yet; refusal is the
conservative substitute.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..releases import AdmissionRefused

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH = re.compile(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-\d{4}$")


@dataclass(frozen=True)
class Contract:
    required_columns: tuple[str, ...] = ()
    unique: tuple[str, ...] = ()
    iso_dates: tuple[str, ...] = ()            # 'YYYY-MM-DD' text; empty allowed only if listed in optional
    month_labels: tuple[str, ...] = ()         # 'Mon-YYYY' text
    integers: tuple[str, ...] = ()
    numbers: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()             # columns whose cells may be empty
    identities: tuple[tuple[str, str, str], ...] = ()   # (a, b, c): a - b == c (arithmetic identity)
    not_greater: tuple[tuple[str, str], ...] = ()       # (a, b): a <= b
    min_rows: int = 1
    extra: dict = field(default_factory=dict)

    @staticmethod
    def from_toml(raw: dict | None) -> Contract | None:
        if not raw:
            return None
        return Contract(
            required_columns=tuple(raw.get("required_columns", ())),
            unique=tuple(raw.get("unique", ())),
            iso_dates=tuple(raw.get("iso_dates", ())),
            month_labels=tuple(raw.get("month_labels", ())),
            integers=tuple(raw.get("integers", ())),
            numbers=tuple(raw.get("numbers", ())),
            optional=tuple(raw.get("optional", ())),
            identities=tuple(tuple(x) for x in raw.get("identities", ())),
            not_greater=tuple(tuple(x) for x in raw.get("not_greater", ())),
            min_rows=int(raw.get("min_rows", 1)),
        )


def _empty(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def validate_rows(relpath: str, contract: Contract, rows: list[tuple[dict, dict]]) -> None:
    """rows: (locator, payload) per data row. Raises AdmissionRefused('contract_violation') on the first failure."""

    def fail(locator: dict | None, column: str, problem: str):
        where = f"{relpath} row {locator['row']}" if locator else relpath
        raise AdmissionRefused("contract_violation", f"{where}, column {column!r}: {problem}")

    if len(rows) < contract.min_rows:
        fail(None, "*", f"expected at least {contract.min_rows} data rows")
    if rows:
        present = set(rows[0][1])
        for col in contract.required_columns:
            if col not in present:
                fail(None, col, "required column missing")
    seen: dict[str, set] = {c: set() for c in contract.unique}
    for loc, row in rows:
        for col in contract.required_columns:
            if col not in contract.optional and _empty(row.get(col)):
                fail(loc, col, "required value missing")
        for col in contract.unique:
            v = row.get(col)
            if v in seen[col]:
                fail(loc, col, "duplicate value in a unique column")
            seen[col].add(v)
        for col in contract.iso_dates:
            v = row.get(col)
            if not _empty(v) and not (isinstance(v, str) and _ISO_DATE.match(v)):
                fail(loc, col, "not an ISO date (YYYY-MM-DD)")
        for col in contract.month_labels:
            v = row.get(col)
            if not _empty(v) and not (isinstance(v, str) and _MONTH.match(v)):
                fail(loc, col, "not a month label (Mon-YYYY)")
        for col in contract.integers:
            v = row.get(col)
            if not _empty(v) and not (isinstance(v, int) and not isinstance(v, bool)):
                fail(loc, col, "not an integer")
        for col in contract.numbers:
            v = row.get(col)
            if not _empty(v) and not (isinstance(v, (int, float)) and not isinstance(v, bool)):
                fail(loc, col, "not a number")
        for a, b, c in contract.identities:
            if row.get(a) - row.get(b) != row.get(c):
                fail(loc, c, f"arithmetic identity {a} - {b} = {c} does not hold")
        for a, b in contract.not_greater:
            if row.get(a) > row.get(b):
                fail(loc, a, f"must not exceed {b}")
