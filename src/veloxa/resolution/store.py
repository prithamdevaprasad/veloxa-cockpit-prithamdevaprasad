"""C12 registries, candidate queue and versioned human decisions (migrations 0011, 0012).

Writes: proposals (identities, entities, aliases, candidates) through the projection role; decisions through the
application role, which can only append. The approval guard is enforced TWICE: `decide` refuses before writing, and
the database trigger refuses regardless of the caller. Only synthetic test identities can ever be approved in this
development profile; every release-pack candidate stays unresolved.

`resolve` is read-basis aware: a decision recorded after `known_at` is not seen, so a later merge never changes what
an earlier read showed (§7.5).

Every decision, refusal and request is an audit event (hash-chained; ids and outcomes only, never identifier values).
A replayed decision with the same `request_key` returns the original decision and writes nothing.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

import psycopg

from .. import audit
from ..db import tenant_tx
from . import rules

ORIGINS = ("synthetic_test", "release_pack")
OUTCOMES = ("approved", "rejected", "deferred")
SYNTHETIC_PREFIX = "syn-"


class NotPermitted(Exception):
    """A decision the guard refuses (e.g. approving a non-synthetic identity)."""


class Invalid(ValueError):
    """A malformed request (missing rationale, unknown outcome or candidate)."""


def register_identity(conn: psycopg.Connection, tenant_id: uuid.UUID, system: str, context: str, value: str,
                      origin: str, source_ref: tuple[str, int, int] | None = None,
                      data_class: str = "personal", observed_at: datetime | None = None) -> uuid.UUID:
    """Idempotent per (context, value). `source_ref` = (record_id, version, batch_seq) for release-pack identities.
    `observed_at` is the source's own observation time; None when the source does not state one (never "now")."""
    if origin not in ORIGINS:
        raise Invalid(f"origin must be one of {ORIGINS}")
    if origin == "synthetic_test" and (not value.startswith(SYNTHETIC_PREFIX) or source_ref is not None):
        raise Invalid("a synthetic test identity uses the reserved 'syn-' namespace and cites no ledger record")
    if origin == "release_pack" and source_ref is None:
        raise Invalid("a release-pack identity must cite its admitted ledger record")
    rec, ver, k = source_ref or (None, None, None)
    with tenant_tx(conn, tenant_id):
        row = conn.execute(
            "INSERT INTO judgement.source_identity (tenant_id, identity_id, system, context, source_value, origin,"
            " data_class, source_record_id, source_version, batch_seq, observed_at)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (tenant_id, context, source_value) DO NOTHING RETURNING identity_id",
            (tenant_id, uuid.uuid4(), system, context, value, origin, data_class, rec, ver, k, observed_at)).fetchone()
        if row is None:
            row = conn.execute("SELECT identity_id, origin FROM judgement.source_identity WHERE tenant_id = %s"
                               " AND context = %s AND source_value = %s", (tenant_id, context, value)).fetchone()
            if row["origin"] != origin:
                raise Invalid("the identity is already registered with a different origin")
    return row["identity_id"]


def register_entity(conn: psycopg.Connection, tenant_id: uuid.UUID, entity_type: str,
                    anchor_identity_id: uuid.UUID) -> uuid.UUID:
    with tenant_tx(conn, tenant_id):
        row = conn.execute(
            "INSERT INTO judgement.entity (tenant_id, entity_id, entity_type, anchor_identity_id) VALUES (%s,%s,%s,%s)"
            " ON CONFLICT (tenant_id, anchor_identity_id) DO NOTHING RETURNING entity_id",
            (tenant_id, uuid.uuid4(), entity_type, anchor_identity_id)).fetchone()
        if row is None:
            row = conn.execute("SELECT entity_id FROM judgement.entity WHERE tenant_id = %s AND anchor_identity_id"
                               " = %s", (tenant_id, anchor_identity_id)).fetchone()
    return row["entity_id"]


def add_alias(conn: psycopg.Connection, tenant_id: uuid.UUID, identity_id: uuid.UUID, context: str,
              alias_text: str, source_ref: tuple[str, int] | None = None) -> dict:
    """Record an alias valid only in `context`. Idempotent (a duplicate returns the existing alias). Reports whether
    the same text is active on ANOTHER identity in the context: that is a conflict to review, never a link."""
    rec, ver = source_ref or (None, None)
    with tenant_tx(conn, tenant_id):
        row = conn.execute(
            "INSERT INTO judgement.alias (tenant_id, alias_id, identity_id, context, alias_text, source_record_id,"
            " source_version) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (tenant_id, identity_id, context, alias_text)"
            " DO NOTHING RETURNING alias_id", (tenant_id, uuid.uuid4(), identity_id, context, alias_text, rec, ver)
        ).fetchone()
        duplicate = row is None
        if duplicate:
            row = conn.execute("SELECT alias_id FROM judgement.alias WHERE tenant_id = %s AND identity_id = %s AND"
                               " context = %s AND alias_text = %s",
                               (tenant_id, identity_id, context, alias_text)).fetchone()
        state = conn.execute("SELECT active, conflicting FROM judgement.alias_state WHERE tenant_id = %s AND"
                             " alias_id = %s", (tenant_id, row["alias_id"])).fetchone()
    return {"alias_id": row["alias_id"], "duplicate": duplicate, "active": state["active"],
            "conflicting": state["conflicting"]}


def retire_alias(conn: psycopg.Connection, tenant_id: uuid.UUID, alias_id: uuid.UUID, retired_by: str,
                 reason: str) -> None:
    """A historical change: the alias stops being valid from now; the row and its history stay."""
    if not reason or not reason.strip():
        raise Invalid("reason is required")
    with tenant_tx(conn, tenant_id):
        conn.execute("INSERT INTO judgement.alias_retirement (tenant_id, alias_id, retired_by, reason)"
                     " VALUES (%s,%s,%s,%s)", (tenant_id, alias_id, retired_by, reason.strip()))


def identity_state(conn: psycopg.Connection, tenant_id: uuid.UUID, identity_id: uuid.UUID) -> str | None:
    """unresolved | candidate | conflicting | approved | rejected (derived, never stored)."""
    with tenant_tx(conn, tenant_id):
        row = conn.execute("SELECT resolution_state FROM judgement.identity_state WHERE tenant_id = %s AND"
                           " identity_id = %s", (tenant_id, identity_id)).fetchone()
    return row["resolution_state"] if row else None


def propose(conn: psycopg.Connection, tenant_id: uuid.UUID, identity_id: uuid.UUID,
            found: list[rules.Candidate]) -> list[uuid.UUID]:
    """Record rule candidates for one identity. Recording is not deciding: each waits in the queue."""
    out = []
    with tenant_tx(conn, tenant_id):
        for c in found:
            if c.classification not in rules.CLASSIFICATIONS:
                raise Invalid(f"unknown classification {c.classification!r}")
            row = conn.execute(
                "INSERT INTO judgement.match_candidate (tenant_id, candidate_id, identity_id, entity_id,"
                " classification, rule_version, evidence) VALUES (%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (tenant_id, identity_id, entity_id, rule_version) DO NOTHING RETURNING candidate_id",
                (tenant_id, uuid.uuid4(), identity_id, c.entity_id, c.classification, rules.RULE_VERSION,
                 json.dumps(list(c.evidence)))).fetchone()
            if row:
                out.append(row["candidate_id"])
    return out


def _candidate(conn, tenant_id, candidate_id):
    return conn.execute(
        "SELECT mc.candidate_id, mc.classification, si.origin AS identity_origin, ai.origin AS anchor_origin,"
        " cs.latest_version AS latest, cs.latest_decision_id AS latest_id"
        " FROM judgement.match_candidate mc"
        " JOIN judgement.source_identity si ON si.tenant_id = mc.tenant_id AND si.identity_id = mc.identity_id"
        " JOIN judgement.entity e ON e.tenant_id = mc.tenant_id AND e.entity_id = mc.entity_id"
        " JOIN judgement.source_identity ai ON ai.tenant_id = e.tenant_id AND ai.identity_id = e.anchor_identity_id"
        " JOIN judgement.candidate_state cs ON cs.tenant_id = mc.tenant_id AND cs.candidate_id = mc.candidate_id"
        " WHERE mc.tenant_id = %s AND mc.candidate_id = %s", (tenant_id, candidate_id)).fetchone()


def request_decision(conn: psycopg.Connection, tenant_id: uuid.UUID, requested_by: str, candidate_id: uuid.UUID,
                     reason: str) -> uuid.UUID:
    """Ask for a human decision on a candidate. A request is not a decision and changes no state."""
    if not reason or not reason.strip() or not requested_by or not requested_by.strip():
        raise Invalid("requested_by and reason are required")
    request_id = uuid.uuid4()
    with tenant_tx(conn, tenant_id, requested_by):
        if _candidate(conn, tenant_id, candidate_id) is None:
            raise Invalid("unknown candidate")
        conn.execute("INSERT INTO judgement.decision_request (tenant_id, request_id, candidate_id, requested_by,"
                     " reason) VALUES (%s,%s,%s,%s,%s)",
                     (tenant_id, request_id, candidate_id, requested_by, reason.strip()))
        audit.append(conn, tenant_id, "identity.decision_requested",
                     {"request_id": str(request_id), "candidate_id": str(candidate_id)})
    return request_id


def _refused(conn, tenant_id, candidate_id, outcome, reason) -> NotPermitted:
    with tenant_tx(conn, tenant_id):                     # recorded although the decision itself is not written
        audit.append(conn, tenant_id, "identity.decision_refused",
                     {"candidate_id": str(candidate_id), "outcome": outcome, "reason": reason})
    return NotPermitted(reason)


def decide(conn: psycopg.Connection, tenant_id: uuid.UUID, decided_by: str, candidate_id: uuid.UUID, outcome: str,
           rationale: str, decision_record: str, evidence_refs: list[str] | None = None,
           request_key: str | None = None) -> dict:
    """Append the next decision version (referencing the one it supersedes). Refuses (NotPermitted, audited) any
    approval the guard forbids. With `request_key`, a replay returns the original decision and writes nothing."""
    if outcome not in OUTCOMES:
        raise Invalid(f"outcome must be one of {OUTCOMES}")
    for name, v in (("decided_by", decided_by), ("rationale", rationale), ("decision_record", decision_record)):
        if not v or not v.strip():
            raise Invalid(f"{name} is required")
    refs = [str(r) for r in (evidence_refs or [])]
    if request_key is not None and not 8 <= len(request_key) <= 200:
        raise Invalid("request_key must be 8 to 200 characters")
    refusal = None
    with tenant_tx(conn, tenant_id, decided_by):
        c = _candidate(conn, tenant_id, candidate_id)
        if c is None:
            raise Invalid("unknown candidate")
        if request_key is not None:
            prior = conn.execute(
                "SELECT decision_id, version, outcome, decided_at, decided_by FROM judgement.resolution_decision"
                " WHERE tenant_id = %s AND candidate_id = %s AND request_key = %s",
                (tenant_id, candidate_id, request_key)).fetchone()
            if prior is not None:
                if (prior["outcome"], prior["decided_by"]) != (outcome, decided_by):
                    raise Invalid("request_key was already used for a different decision")
                return {"decision_id": str(prior["decision_id"]), "candidate_id": str(candidate_id),
                        "version": prior["version"], "outcome": outcome,
                        "decided_at": prior["decided_at"].isoformat(), "replayed": True}
        if outcome == "approved":
            if c["identity_origin"] != "synthetic_test" or c["anchor_origin"] != "synthetic_test":
                refusal = "only synthetic test identities may be approved; real candidates stay unresolved"
            elif c["classification"] not in rules.APPROVABLE:
                refusal = f"a {c['classification']} candidate cannot be approved"
        if refusal is None:
            version = (c["latest"] or 0) + 1
            decision_id = uuid.uuid4()
            try:
                with conn.transaction():
                    row = conn.execute(
                        "INSERT INTO judgement.resolution_decision (tenant_id, candidate_id, version, decision_id,"
                        " outcome, decided_by, rationale, decision_record, previous_decision_id, evidence_refs,"
                        " request_key) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING decided_at",
                        (tenant_id, candidate_id, version, decision_id, outcome, decided_by, rationale.strip(),
                         decision_record.strip(), c["latest_id"], json.dumps(refs), request_key)).fetchone()
            except (psycopg.errors.InsufficientPrivilege, psycopg.errors.CheckViolation) as e:
                if not str(e).startswith("resolution decision:"):
                    raise                     # e.g. a role without INSERT: never disguised as a guard refusal
                refusal = str(e).splitlines()[0]
            else:
                audit.append(conn, tenant_id, "identity.decided", {
                    "decision_id": str(decision_id), "candidate_id": str(candidate_id), "version": version,
                    "outcome": outcome, "previous_decision_id": str(c["latest_id"]) if c["latest_id"] else None,
                    "evidence_refs": len(refs)})
    if refusal is not None:
        raise _refused(conn, tenant_id, candidate_id, outcome, refusal)
    return {"decision_id": str(decision_id), "candidate_id": str(candidate_id), "version": version,
            "outcome": outcome, "decided_at": row["decided_at"].isoformat(), "replayed": False}


_LATEST = (" FROM judgement.match_candidate mc"
           " LEFT JOIN LATERAL (SELECT d.outcome, d.version FROM judgement.resolution_decision d"
           "  WHERE d.tenant_id = mc.tenant_id AND d.candidate_id = mc.candidate_id"
           "  AND (%(known_at)s::timestamptz IS NULL OR d.decided_at <= %(known_at)s::timestamptz)"
           "  ORDER BY d.version DESC LIMIT 1) last ON true")


def queue(conn: psycopg.Connection, tenant_id: uuid.UUID) -> list[dict]:
    """Candidates awaiting a human: no decision yet, or deferred. Classification, never a score. A candidate whose
    identity the caller cannot see (a protected class, under RLS) is not listed."""
    with tenant_tx(conn, tenant_id):
        return conn.execute(
            "SELECT mc.candidate_id, mc.identity_id, mc.entity_id, mc.classification, mc.rule_version, mc.evidence,"
            " si.context, si.source_value, last.outcome AS latest_outcome" + _LATEST +
            " JOIN judgement.source_identity si ON si.tenant_id = mc.tenant_id AND si.identity_id = mc.identity_id"
            " WHERE mc.tenant_id = %(t)s AND (last.outcome IS NULL OR last.outcome = 'deferred')"
            " ORDER BY mc.created_at, mc.candidate_id", {"t": tenant_id, "known_at": None}).fetchall()


def resolve(conn: psycopg.Connection, tenant_id: uuid.UUID, context: str, value: str,
            known_at: datetime | None = None) -> dict:
    """The entity for an identifier as decided by `known_at`, else 'unresolved' with the raw value kept."""
    with tenant_tx(conn, tenant_id):
        rows = conn.execute(
            "SELECT mc.entity_id" + _LATEST +
            " JOIN judgement.source_identity si ON si.tenant_id = mc.tenant_id AND si.identity_id = mc.identity_id"
            " WHERE mc.tenant_id = %(t)s AND si.context = %(c)s AND si.source_value = %(v)s"
            " AND last.outcome = 'approved'",
            {"t": tenant_id, "c": context, "v": value, "known_at": known_at}).fetchall()
    if len(rows) == 1:
        return {"status": "resolved", "entity_id": str(rows[0]["entity_id"]), "raw": value, "context": context}
    return {"status": "unresolved", "raw": value, "context": context}
