"""Evidence integrity for detector output (ARCHITECTURE.md §8.4 T1, T5; §9.3; §15).

Every record a signal cites must resolve, at the signal's read basis, to an admitted record in a SEALED batch with
batch_seq <= K, inside the tenant (RLS), and outside the protected-pay classes. A signal citing anything else is a
contract violation: the whole detector run fails closed rather than emitting an untraceable signal.
"""

from __future__ import annotations

from dataclasses import replace

import psycopg

from ..db import tenant_tx
from ..temporal import ReadBasis
from .contract import Signal

PROTECTED_CLASSES = ("sensitive_personal", "payroll_aggregate")


class EvidenceError(ValueError):
    """A signal cites evidence that is not admitted, not visible at K, or protected."""


def visible_records(conn: psycopg.Connection, basis: ReadBasis, record_ids: set[str]) -> dict[str, dict]:
    if not record_ids:
        return {}
    with tenant_tx(conn, basis.tenant_id, batch_seq=basis.batch_seq):
        rows = conn.execute(
            "SELECT DISTINCT ON (record_id) record_id, batch_seq, data_class, kind, system, source_path"
            " FROM ledger.visible_source_record WHERE record_id = ANY(%s) ORDER BY record_id, version DESC",
            (sorted(record_ids),)).fetchall()
    return {r["record_id"]: r for r in rows}


def verify(conn: psycopg.Connection, basis: ReadBasis, signals: list[Signal]) -> list[Signal]:
    """Return the signals with their T5 lineage set; raise EvidenceError if any citation is not admissible."""
    wanted = {rid for s in signals for rid in s.record_ids}
    seen = visible_records(conn, basis, wanted)
    out = []
    for s in signals:
        missing = [rid for rid in s.record_ids if rid not in seen]
        if missing:
            raise EvidenceError(f"{s.detector}: cites {missing} which are not admitted and visible at "
                                f"K={basis.batch_seq}")
        protected = [rid for rid in s.record_ids if seen[rid]["data_class"] in PROTECTED_CLASSES]
        if protected:
            raise EvidenceError(f"{s.detector}: cites protected-pay records; detectors never use them")
        lineage = max((seen[rid]["batch_seq"] for rid in s.record_ids), default=0)
        if lineage > basis.batch_seq:  # cannot happen through the view; asserted anyway
            raise EvidenceError(f"{s.detector}: lineage K={lineage} exceeds the read basis K={basis.batch_seq}")
        out.append(replace(s, lineage_batch_seq=lineage))
    return out
