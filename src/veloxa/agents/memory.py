"""Five-tier agent memory (ARCHITECTURE.md §14.5, §5.3 `MemoryStore`; migration 0013).

Tiers (approved) and how each is filled here:
- working: current session turns, scoped to one K. Label `execution_context`; closed with the session.
- episodic: records the person was party to, as dated events. Label `source_evidence`; only evidence that passed
  KB ∩ AZ ∩ POL.
- semantic: consolidated facts with validity intervals. Only from episodic items (provenance episodes required).
- interview: summaries of prior sessions, per interviewer. A deterministic summary of the session's cited evidence.
- reflective: model-generated summaries, always `inference`. Never evidence; short retention.

Isolation: tenant, agent and interviewer are separate columns enforced by RLS (`memory_scope`). Every call needs the
full scope; a read without it raises. Interviewer V never sees interviewer U's working, interview or reflective
memory. Agent-level tiers (episodic, semantic) are re-checked against the interviewer's CURRENT authorization at
recall, so revoked knowledge is not returned. Items from a later K are hidden at an earlier K.
Memory is expired, never deleted: every change is an event. Model output never becomes a fact: the only tier a
model may fill is `reflective`, labelled `inference`.
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

import psycopg

from .. import audit
from ..db import tenant_tx
from .authorization import EvidenceAuthorizer, policy_allows

TIERS = ("working", "episodic", "semantic", "interview", "reflective")
EVENTS = ("expired", "superseded", "invalidated", "retention_expired", "session_closed")


class ScopeError(ValueError):
    pass


@dataclass(frozen=True)
class MemoryScope:
    tenant_id: uuid.UUID
    agent_id: str
    interviewer_id: str

    def __post_init__(self):
        if not (self.tenant_id and self.agent_id and self.interviewer_id
                and self.interviewer_id.startswith("person:")):
            raise ScopeError("memory needs a full scope: tenant, agent and interviewer")


@contextmanager
def _scoped(conn: psycopg.Connection, scope: MemoryScope):
    with tenant_tx(conn, scope.tenant_id, scope.interviewer_id):
        conn.execute("SELECT set_config('app.agent_id', %s, true), set_config('app.interviewer_id', %s, true)",
                     (scope.agent_id, scope.interviewer_id))
        yield conn


def _system(record_id: str) -> str:
    return record_id.split(":", 1)[0]


class MemoryStore:
    def __init__(self, conn: psycopg.Connection, authorizer: EvidenceAuthorizer | None = None):
        self.conn = conn
        self.authorizer = authorizer

    # ------------------------------------------------------------------------------------------------ writes
    def _insert(self, scope, tier, label, content, *, interviewer=True, session_id=None, basis_label=None,
                sources=(), episodes=(), classes=(), batch_seq=0, valid_from=None, valid_to=None,
                execution_id=None) -> uuid.UUID:
        mid = uuid.uuid4()
        if any(not policy_allows(c) for c in classes):
            raise ScopeError("a protected or non-interview data class never enters agent memory")
        with _scoped(self.conn, scope):
            self.conn.execute(
                "INSERT INTO runtime.agent_memory (tenant_id, memory_id, agent_id, interviewer_id, session_id, tier,"
                " label, content, knowledge_basis, source_records, provenance_episodes, data_classes, batch_seq,"
                " valid_from, valid_to, created_by_execution) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (scope.tenant_id, mid, scope.agent_id, scope.interviewer_id if interviewer else None, session_id,
                 tier, label, json.dumps(content, default=str), basis_label, list(sources), list(episodes),
                 sorted(set(classes)), batch_seq, valid_from, valid_to, execution_id))
        return mid

    def append_working(self, scope, session_id, content: dict, batch_seq: int, execution_id=None) -> uuid.UUID:
        return self._insert(scope, "working", "execution_context", content, session_id=session_id,
                            batch_seq=batch_seq, execution_id=execution_id)

    def append_episodic(self, scope, evidence: dict, knowledge_basis: str, batch_seq: int,
                        execution_id=None) -> uuid.UUID:
        """One record the represented person was party to (or could access), as admitted at its batch. Callers
        pass ONLY evidence that passed KB ∩ AZ ∩ POL."""
        if knowledge_basis not in ("participant", "possible", "role_plausible"):
            raise ScopeError("episodic memory needs a knowledge basis from KB")
        return self._insert(scope, "episodic", "source_evidence",
                            {"record_id": evidence["record_id"], "kind": evidence["kind"],
                             "excerpt": evidence.get("excerpt", "")[:500]}, interviewer=False,
                            basis_label=knowledge_basis, sources=[evidence["record_id"]],
                            classes=[evidence["data_class"]], batch_seq=max(batch_seq, evidence["batch_seq"]),
                            execution_id=execution_id)

    def consolidate_semantic(self, scope, statement: str, episodes: list[uuid.UUID], valid_from=None,
                             valid_to=None, label: str = "derived_observation") -> uuid.UUID:
        """A consolidated fact derived DETERMINISTICALLY from current episodic items of this agent. It inherits the
        union of their sources and classes (the most restrictive access) and the latest of their batches."""
        if label not in ("derived_observation", "approved_decision") or not episodes:
            raise ScopeError("semantic memory is consolidated from episodes, as an observation or decision")
        with _scoped(self.conn, scope):
            rows = self.conn.execute(
                "SELECT memory_id, source_records, data_classes, batch_seq, knowledge_basis FROM"
                " runtime.agent_memory_current WHERE tier = 'episodic' AND memory_id = ANY(%s)",
                (list(episodes),)).fetchall()
        if len(rows) != len(set(episodes)):
            raise ScopeError("every provenance episode must be a current episodic memory of this agent")
        weakest = ("role_plausible" if any(r["knowledge_basis"] == "role_plausible" for r in rows) else
                   "possible" if any(r["knowledge_basis"] == "possible" for r in rows) else "participant")
        return self._insert(scope, "semantic", label, {"statement": statement}, interviewer=False,
                            basis_label=weakest, sources=sorted({s for r in rows for s in r["source_records"]}),
                            episodes=list(episodes), classes={c for r in rows for c in r["data_classes"]},
                            batch_seq=max(r["batch_seq"] for r in rows), valid_from=valid_from, valid_to=valid_to)

    def append_interview(self, scope, session_id, summary: dict, sources: list[str], classes: list[str],
                         batch_seq: int) -> uuid.UUID:
        return self._insert(scope, "interview", "derived_observation", summary, session_id=session_id,
                            sources=sources, classes=classes, batch_seq=batch_seq)

    def append_reflective(self, scope, text: str, model: str, prompt_version: str, batch_seq: int,
                          session_id=None) -> uuid.UUID:
        return self._insert(scope, "reflective", "inference",
                            {"text": text, "model": model, "prompt_version": prompt_version,
                             "note": "model-generated inference; never evidence"},
                            session_id=session_id, batch_seq=batch_seq)

    # ------------------------------------------------------------------------------------------------ events
    def event(self, scope, memory_id, event: str, reason: str, superseded_by=None, decision=None) -> None:
        if event not in EVENTS:
            raise ScopeError(f"unknown memory event {event!r}")
        with _scoped(self.conn, scope):
            row = self.conn.execute("SELECT interviewer_id FROM runtime.agent_memory WHERE memory_id = %s",
                                    (memory_id,)).fetchone()
            if row is None:
                raise ScopeError("no such memory in this scope")
            self.conn.execute(
                "INSERT INTO runtime.agent_memory_event (tenant_id, event_id, memory_id, agent_id, interviewer_id,"
                " event, superseded_by, reason, decision) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (scope.tenant_id, uuid.uuid4(), memory_id, scope.agent_id, row["interviewer_id"], event,
                 superseded_by, reason, json.dumps(decision) if decision else None))
            audit.append(self.conn, scope.tenant_id, "agent.memory_event",
                         {"agent_id": scope.agent_id, "memory_id": str(memory_id), "event": event})

    def close_session(self, scope, session_id) -> int:
        with _scoped(self.conn, scope):
            ids = [r["memory_id"] for r in self.conn.execute(
                "SELECT memory_id FROM runtime.agent_memory_current WHERE tier = 'working' AND session_id = %s",
                (session_id,)).fetchall()]
        for m in ids:
            self.event(scope, m, "session_closed", "working memory ends with its session")
        return len(ids)

    def sweep_retention(self, scope, config: dict, now: datetime) -> int:
        n = 0
        with _scoped(self.conn, scope):
            rows = self.conn.execute("SELECT memory_id, tier, created_at FROM runtime.agent_memory_current"
                                     ).fetchall()
        for r in rows:
            days = (config.get(r["tier"]) or {}).get("retention_days")
            if days is not None and r["created_at"] < now - timedelta(days=days):
                self.event(scope, r["memory_id"], "retention_expired", f"older than {days} days")
                n += 1
        return n

    # ------------------------------------------------------------------------------------------------ reads
    def recall(self, scope, batch_seq: int, tiers: tuple[str, ...] = TIERS,
               known_at: datetime | None = None) -> tuple[list[dict], int]:
        """Items in scope, admitted by K, current as of `known_at` (default now). Returns (items, withheld):
        agent-level items whose sources the interviewer is no longer authorized to see are withheld."""
        with _scoped(self.conn, scope):
            rows = self.conn.execute(
                "SELECT m.* FROM runtime.agent_memory m WHERE m.tier = ANY(%(t)s) AND m.batch_seq <= %(k)s"
                " AND (%(at)s::timestamptz IS NULL OR m.created_at <= %(at)s::timestamptz)"
                " AND NOT EXISTS (SELECT 1 FROM runtime.agent_memory_event e WHERE e.tenant_id = m.tenant_id"
                "  AND e.memory_id = m.memory_id AND (%(at)s::timestamptz IS NULL OR e.occurred_at <= %(at)s))"
                " ORDER BY m.created_at, m.memory_id", {"t": list(tiers), "k": batch_seq, "at": known_at}).fetchall()
        out, withheld = [], 0
        for r in rows:
            if r["source_records"] and self.authorizer is not None:
                ok = all(self.authorizer.allowed(scope.tenant_id, scope.interviewer_id, _system(s), c)[0]
                         for s in r["source_records"] for c in (r["data_classes"] or ["internal"]))
                if not ok:
                    withheld += 1
                    continue
            elif r["source_records"] and self.authorizer is None:
                withheld += 1                                    # no authorizer: fail closed
                continue
            out.append(dict(r))
        return out, withheld
