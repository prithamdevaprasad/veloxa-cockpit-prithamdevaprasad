"""C12 candidate rules (ARCHITECTURE.md §7.5): deterministic, explicit classifications, NO numeric score.

The approved architecture names Fellegi-Sunter scoring as a later integration but defines no score meaning or
auto-accept threshold for this profile, so none is invented: a rule either fits or it does not, and every candidate
waits for a human decision. String similarity is never used (it may suggest; it never merges, and here it does not
even suggest).

Rules, in order of strength:
  exact_identifier             the observed value equals an identifier of the entity (e-mail, case-insensitive;
                               a typed ID within the same ID namespace)
  unique_initials_in_context   observed initials equal the initials of exactly ONE entity in the context population
  surname_initial              "J Smith", "J. Smith" or "Smith, J" equals exactly ONE entity's first initial + surname
  ambiguous                    a name rule fits more than one entity (one candidate per fitting entity, none approvable)
  conflicting                  an exact identifier and a name rule point at different entities
Opaque identifiers (anything else) produce NO candidate: they stay raw and are reported as unresolved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

RULE_VERSION = "c12-rules/1"
CLASSIFICATIONS = ("exact_identifier", "unique_initials_in_context", "surname_initial", "ambiguous", "conflicting")
APPROVABLE = frozenset({"exact_identifier", "unique_initials_in_context", "surname_initial"})

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_INITIALS = re.compile(r"^[A-Z]{2,3}$")
_INITIAL_SURNAME = re.compile(r"^([A-Za-z])\.?\s+([A-Za-z][A-Za-z'\-]+)$")
_SURNAME_INITIAL = re.compile(r"^([A-Za-z][A-Za-z'\-]+),\s*([A-Za-z])\.?$")


@dataclass(frozen=True)
class Profile:
    """What is known about one entity, from its anchor and approved identities (never from guesses)."""
    entity_id: str
    full_name: str | None = None
    emails: frozenset[str] = frozenset()
    ids: frozenset[tuple[str, str]] = frozenset()     # (namespace, value), e.g. ("hr.emp_id", "T001")


@dataclass(frozen=True)
class Candidate:
    entity_id: str
    classification: str
    evidence: tuple[str, ...] = field(default_factory=tuple)


def shape(value: str) -> str:
    v = value.strip()
    if _EMAIL.match(v):
        return "email"
    if _INITIALS.match(v):
        return "initials"
    if _INITIAL_SURNAME.match(v) or _SURNAME_INITIAL.match(v):
        return "initial_surname"
    return "opaque"


def _initials(name: str | None) -> str | None:
    parts = (name or "").split()
    return "".join(p[0] for p in parts).upper() if len(parts) >= 2 else None


def _initial_surname(name: str | None) -> tuple[str, str] | None:
    parts = (name or "").split()
    return (parts[0][0].lower(), parts[-1].lower()) if len(parts) >= 2 else None


def candidates(value: str, population: list[Profile], id_namespace: str | None = None) -> list[Candidate]:
    """Candidates for one observed value against the context's population. Empty means: stays unresolved."""
    v = value.strip()
    exact: list[Candidate] = []
    if shape(v) == "email":
        exact = [Candidate(p.entity_id, "exact_identifier", ("email equal (case-insensitive)",))
                 for p in population if v.lower() in {e.lower() for e in p.emails}]
    elif id_namespace:
        exact = [Candidate(p.entity_id, "exact_identifier", (f"{id_namespace} equal",))
                 for p in population if (id_namespace, v) in p.ids]
    named: list[Candidate] = []
    kind = shape(v)
    if kind == "initials":
        named = [Candidate(p.entity_id, "unique_initials_in_context", ("initials equal within the context",))
                 for p in population if _initials(p.full_name) == v]
    elif kind == "initial_surname":
        m = _INITIAL_SURNAME.match(v)
        key = (m.group(1).lower(), m.group(2).lower()) if m else None
        if key is None:
            m2 = _SURNAME_INITIAL.match(v)
            key = (m2.group(2).lower(), m2.group(1).lower())
        named = [Candidate(p.entity_id, "surname_initial", ("first initial and surname equal",))
                 for p in population if _initial_surname(p.full_name) == key]
    if len(exact) > 1:
        return [Candidate(c.entity_id, "ambiguous", (f"{len(exact)} entities share the identifier",)) for c in exact]
    if len(named) > 1:
        named = [Candidate(c.entity_id, "ambiguous", (f"{len(named)} entities fit the name rule",)) for c in named]
    if exact and named and {c.entity_id for c in named} != {exact[0].entity_id}:
        ids = {exact[0].entity_id} | {c.entity_id for c in named}
        return [Candidate(i, "conflicting", ("exact identifier and name rule disagree",)) for i in sorted(ids)]
    return exact or named
