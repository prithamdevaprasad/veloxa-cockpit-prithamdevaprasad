"""Deterministic verifier (ARCHITECTURE.md §13.4, C17). It can only DROP or DOWNGRADE a claim, never add one.

Per atomic claim, against ITS OWN cited evidence (never the pooled pack):
  1. citations: every cited fact and record exists in the evidence pack, and every record is visible at the read
     basis (re-checked in the database under the same K)                                     -> drop
  2. claim kind <= the weakest necessary source class                                     -> downgrade
  3. numbers: every number in the claim appears in the cited facts or records              -> drop
  4. entities: every person named in the claim appears in the cited facts or records      -> drop
  5. policy scan: no protected data class among the cited records; no leave types         -> drop + policy event
The entailment check (an independent model pass) is not run in facts-only mode: claims there are verbatim
renderings of the cited facts or excerpts, which the checks above confirm.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

SOURCE_CLASS = {"export": "system_record", "email": "message", "transcript": "transcript", "document": "document",
                "brief": "document", "readme": "document"}
# Claim kinds each source class can carry, strongest first. A claim is downgraded to the strongest kind that the
# WEAKEST cited source supports.
_KIND_ORDER = ["derived", "record", "document", "statement"]
_SUPPORTS = {"system_record": "derived", "message": "record", "document": "document", "transcript": "statement"}
PROTECTED_CLASSES = frozenset({"sensitive_personal", "payroll_aggregate"})
_LEAVE = re.compile(r"\b(sick|medical|maternity|paternity|bereavement|casual|privilege|earned|unpaid|compensatory)"
                    r"\s+leave\b|\bleave (type|balance|reason)s?\b", re.IGNORECASE)
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


@dataclass
class Claim:
    text: str
    kind: str                     # derived | record | document | statement
    evidence: list[str]           # fact ids (F1 ...) and record ids
    verification: str = "pending"  # supported | downgraded | dropped
    notes: list[str] = field(default_factory=list)


@dataclass
class Outcome:
    kept: list[Claim]
    dropped: list[Claim]
    policy_events: list[dict]     # structure only: {"check": ..., "count": ...}


def _numbers(text: str) -> set[str]:
    out = set()
    for tok in _NUMBER.findall(text):
        try:
            out.add(str(Decimal(tok.replace(",", "")).normalize()))
        except InvalidOperation:
            continue
    return out


def _sources_text(claim: Claim, facts: dict, records: dict) -> tuple[str, list[dict]]:
    parts: list[str] = []
    cited_records: list[dict] = []
    for ref in claim.evidence:
        if ref in facts:
            f = facts[ref]
            parts += [f["display"], str(f["value"]), str(f.get("unit") or ""), " ".join(f.get("qualifiers", []))]
            cited_records += [records[i] for i in f["inputs"] if i in records]
        elif ref in records:
            cited_records.append(records[ref])
    for r in cited_records:
        parts += [r["excerpt"], r["source_path"], " ".join(str(v) for v in r["locator"].values())]
    return "\n".join(parts), cited_records


def verify(claims: list[Claim], facts: dict, records: dict, visible_ids: set[str], people: list[str]) -> Outcome:
    """facts: fact_id -> fact dict; records: record_id -> record dict (both from the evidence pack);
    visible_ids: record ids visible at the read basis (from the database); people: full names at the basis."""
    kept, dropped, events = [], [], {}

    def drop(c: Claim, why: str, policy: str | None = None) -> None:
        c.verification = "dropped"
        c.notes.append(why)
        dropped.append(c)
        if policy:
            events[policy] = events.get(policy, 0) + 1

    for c in claims:
        missing = [e for e in c.evidence if e not in facts and e not in records]
        inputs = [i for e in c.evidence if e in facts for i in facts[e]["inputs"]]
        cited_ids = [e for e in c.evidence if e in records] + inputs
        if not c.evidence or missing:
            drop(c, "cites evidence that is not in the evidence pack")
            continue
        if any(i not in records or i not in visible_ids for i in cited_ids):
            drop(c, "cites a record that is not visible at the read basis")
            continue
        text, cited = _sources_text(c, facts, records)
        if any(r["data_class"] in PROTECTED_CLASSES for r in cited):
            drop(c, "cites a protected record", "protected_class_cited")
            continue
        if _LEAVE.search(c.text):
            drop(c, "mentions a leave type", "leave_type_mentioned")
            continue
        if not _numbers(c.text) <= _numbers(text):
            drop(c, "states a number that its cited evidence does not contain")
            continue
        low_c, low_s = c.text.lower(), text.lower()
        if any(re.search(rf"\b{re.escape(n.lower())}\b", low_c) and n.lower() not in low_s for n in people):
            drop(c, "names a person that its cited evidence does not name")
            continue
        weakest = max((_KIND_ORDER.index(_SUPPORTS[SOURCE_CLASS.get(r["kind"], "document")]) for r in cited),
                      default=_KIND_ORDER.index("statement"))
        if _KIND_ORDER.index(c.kind) < weakest:
            c.notes.append(f"downgraded from {c.kind}: the weakest cited source supports {_KIND_ORDER[weakest]}")
            c.kind = _KIND_ORDER[weakest]
            c.verification = "downgraded"
        else:
            c.verification = "supported"
        kept.append(c)
    return Outcome(kept, dropped, [{"check": k, "count": v} for k, v in sorted(events.items())])
