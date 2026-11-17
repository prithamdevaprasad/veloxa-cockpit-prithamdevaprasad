"""C32 Historical ACL evaluator (ARCHITECTURE.md §5.2 C32, §5.3 contract, §10.3, §10.7).

Answers ONE question: could a source principal (a Veloxa person) access object O at the historical instant t_h
implied by a ReadBasis (POINT_IN_TIME), or at any instant of a bounded window before it
(ELIGIBILITY_DURING_WINDOW)? It reads only Veloxa-owned ACL, identity and membership history admitted by K
(projection.acl_history over the ledger). It never reads, writes or falls back to a current authorization engine.

Normative rules implemented here (§10.7):
- outcomes `allowed` / `denied` / `unknown`; callers treat `unknown` exactly like `denied` (`effective_decision`);
- the effective decision is current authorization AND historical `allowed`, never OR, so a historical `allowed`
  can never restore access revoked since;
- `allowed` is eligibility, not reading: `grant_relations` lets callers tell participation (author, sender) from
  access alone, mapped to knowledge labels by `knowledge_basis`;
- the same-instant rule: identity link, every membership on the path and the grant must all hold at one instant,
  with no applicable explicit deny (deny overrides allow);
- resource existence (both modes) with three states; a first observation is never treated as creation;
- every missing, ambiguous, stale or unevaluable input fails closed with a reason code.

Staleness limits (D-10a) and the maximum window lookback (D-10b) are owner parameters that are not set. Until
they are, no staleness is tolerated: an instant after the latest observation of a resource, or after the ACL's
last sync, is not evaluable (`unknown`), and the window may start at the subject's earliest identity link.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Literal

import psycopg
from psycopg import sql as psql

from ..db import tenant_tx
from ..temporal import Mode, ReadBasis, predicate

HistoricalAclMode = Literal["POINT_IN_TIME", "ELIGIBILITY_DURING_WINDOW"]
Outcome = Literal["allowed", "denied", "unknown"]
MAX_GROUP_DEPTH = 8          # an engineering bound on transitive expansion (cycle-safe); exceeding it is `unknown`
_EPS = timedelta(microseconds=1)
PARTICIPATION = {"author", "sender", "responder", "assignee"}


@dataclass(frozen=True)
class AclWindow:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class RecordVersionRef:
    record_id: str
    version: int


@dataclass(frozen=True)
class ResourceExistence:
    created_at: datetime | None
    ended_at: datetime | None
    observed: tuple[datetime, datetime] | None


@dataclass(frozen=True)
class EvaluationConfig:
    resource_staleness: timedelta | None = None   # D-10a: not set (None = nothing beyond the latest observation)
    acl_staleness: timedelta | None = None        # D-10a: not set (None = nothing beyond the last ACL sync)
    max_lookback: timedelta | None = None         # D-10b: not set


@dataclass(frozen=True)
class HistoricalAclResult:
    outcome: Outcome
    mode: HistoricalAclMode
    basis: ReadBasis
    historical_at: datetime
    evaluated_at: datetime            # audit only; never an input to the decision
    window: AclWindow | None
    resource_existence: ResourceExistence | None
    eligible_intervals: list[tuple[datetime, datetime]]
    grant_relations: list[str]
    acl_records: list[RecordVersionRef]
    membership_records: list[RecordVersionRef]
    acl_source_version: str | None
    acl_synced_at: datetime | None
    reason: str

    def replay_view(self) -> dict:
        """Everything except evaluated_at: a replay from the same provenance must be identical (ACL-H6)."""
        d = asdict(self)
        d.pop("evaluated_at")
        d["basis"] = repr(self.basis)
        return json.loads(json.dumps(d, default=str, sort_keys=True))


@dataclass(frozen=True)
class Item:
    item_type: str
    ref: RecordVersionRef
    valid_from: datetime | None
    valid_to: datetime | None
    recorded_hi: datetime
    data: dict

    def holds(self, t: datetime) -> bool:
        return self.valid_from <= t and (self.valid_to is None or t < self.valid_to)

    def get_t(self, key: str) -> datetime | None:
        v = self.data.get(key)
        return datetime.fromisoformat(v) if v else None


@dataclass
class History:
    items: list[Item] = field(default_factory=list)

    def of(self, kind: str) -> list[Item]:
        return [i for i in self.items if i.item_type == kind]


# ------------------------------------------------------------------------------------------------- point evaluation


@dataclass
class _Point:
    outcome: Outcome
    reason: str
    relations: set[str] = field(default_factory=set)
    acl: set[RecordVersionRef] = field(default_factory=set)
    memberships: set[RecordVersionRef] = field(default_factory=set)


def existence_of(history: History, resource: str) -> ResourceExistence | None:
    rs = [i for i in history.of("resource") if i.data["resource"] == resource]
    if not rs:
        return None
    created = min((t for i in rs if (t := i.get_t("created_at"))), default=None)
    ended = min((t for i in rs if (t := i.get_t("ended_at"))), default=None)
    firsts = [t for i in rs if (t := i.get_t("observed_first"))]
    lasts = [t for i in rs if (t := i.get_t("observed_last"))]
    return ResourceExistence(created, ended, (min(firsts), max(lasts)) if firsts else None)


def existence_state(ex: ResourceExistence | None, t: datetime, cfg: EvaluationConfig) -> str:
    """'exists' | 'not' | 'unknown' (§10.7 resource-existence rule)."""
    if ex is None:
        return "unknown"
    if (ex.created_at and t < ex.created_at) or (ex.ended_at and ex.ended_at <= t):
        return "not"
    started = (ex.created_at and t >= ex.created_at) or (ex.observed and t >= ex.observed[0])
    if not started:
        return "unknown"            # before the first observation, with no recorded creation: never inferred
    if ex.observed and t > ex.observed[1]:
        if cfg.resource_staleness is None or t - ex.observed[1] > cfg.resource_staleness:
            return "unknown"        # beyond the staleness limit after the latest observation
    return "exists"


def _acl_state(history: History, resource: str):
    states = [i for i in history.of("acl_state") if i.data["resource"] == resource]
    return max(states, key=lambda i: (i.get_t("acl_synced_at"), i.ref.record_id)) if states else None


def _invalid_touches(history: History, *needles: str) -> bool:
    for i in history.of("invalid"):
        text = json.dumps(i.data, default=str)
        if any(n and n in text for n in needles):
            return True
    return False


def evaluate_at(history: History, person: str, permission: str, resource: str, t: datetime,
                cfg: EvaluationConfig) -> _Point:
    """The §10.7 decision at ONE instant, with the same-instant rule."""
    if _invalid_touches(history, person, resource):
        return _Point("unknown", "partial_history")
    ex = existence_of(history, resource)
    state = existence_state(ex, t, cfg)
    if state == "not":
        return _Point("denied", "resource_not_available")
    if state == "unknown":
        return _Point("unknown", "resource_availability_unknown")
    st = _acl_state(history, resource)
    if st is None:
        return _Point("unknown", "no_acl_history")
    snap, hist_from, synced = st.get_t("snapshot_extracted_at"), st.get_t("history_from"), st.get_t("acl_synced_at")
    covered = (hist_from is not None and t >= hist_from) or (snap is not None and t >= snap)
    if not covered:
        if snap is not None and hist_from is None:
            return _Point("unknown", "snapshot_only_before_extraction", acl={st.ref})   # same rule as T3
        return _Point("unknown", "no_acl_history", acl={st.ref})
    if t > synced and (cfg.acl_staleness is None or t - synced > cfg.acl_staleness):
        return _Point("unknown", "stale_beyond_limit", acl={st.ref})
    # Subject resolution: identity links valid at t; a principal shared with another person is ambiguous.
    links = [i for i in history.of("identity_link") if i.holds(t)]
    mine = [i for i in links if i.data["person"] == person]
    if not mine:
        return _Point("unknown", "identity_unresolved")
    principals = {i.data["principal"] for i in mine}
    if any(i.data["principal"] in principals and i.data["person"] != person for i in links):
        return _Point("unknown", "identity_unresolved")
    used_memberships = {i.ref for i in mine}
    # Group expansion: memberships valid at t, transitive, cycle-safe, depth-bounded; overlaps are ambiguous.
    members = [i for i in history.of("membership") if i.holds(t)]
    pairs: dict[tuple[str, str], list[Item]] = {}
    for m in members:
        pairs.setdefault((m.data["member"], m.data["group"]), []).append(m)
    reach, frontier, via_group, depth = set(principals), set(principals), set(), 0
    while frontier:
        nxt = set()
        for (member, group), recs in pairs.items():
            if member in frontier and group not in reach:
                if len(recs) > 1:
                    return _Point("unknown", "membership_ambiguous")
                nxt.add(group)
                used_memberships.add(recs[0].ref)
        if nxt:
            depth += 1
            if depth > MAX_GROUP_DEPTH:
                return _Point("unknown", "depth_limit")
        reach |= nxt
        via_group |= nxt
        frontier = nxt
    # Grants valid at t, for this resource and permission.
    acls = [i for i in history.of("acl") if i.holds(t) and i.data["resource"] == resource
            and i.data["permission"] == permission]
    applicable = [a for a in acls if a.data["principal"] in reach]
    denies = [a for a in applicable if a.data["effect"] == "deny"]
    if denies:
        return _Point("denied", "explicit_deny", acl={a.ref for a in denies} | {st.ref},
                      memberships=used_memberships)
    allows = [a for a in applicable if a.data["effect"] == "allow"]
    if allows:
        rel = {a.data["relation"] for a in allows}
        if any(a.data["principal"] in via_group for a in allows):
            rel.add("group_member")
        return _Point("allowed", "grant_valid", rel, {a.ref for a in allows} | {st.ref}, used_memberships)
    unresolvable = [a for a in acls if a.data["principal"].startswith("domain:") or a.data["principal"] == "is_public"]
    if unresolvable:   # organisation-wide or public sharing needs roster resolution that is not available
        return _Point("unknown", "partial_history", acl={a.ref for a in unresolvable} | {st.ref})
    return _Point("denied", "no_grant", acl={st.ref}, memberships=used_memberships)


# -------------------------------------------------------------------------------------------------- the evaluator


def _boundaries(history: History, person: str, resource: str, cfg: EvaluationConfig) -> set[datetime]:
    out: set[datetime] = set()
    for i in history.items:
        if i.valid_from is not None:
            out.add(i.valid_from)
            if i.valid_to is not None:
                out.add(i.valid_to)
    ex = existence_of(history, resource)
    if ex:
        out |= {t for t in (ex.created_at, ex.ended_at) if t}
        if ex.observed:
            out.add(ex.observed[0])
            out.add(ex.observed[1] + _EPS)
            if cfg.resource_staleness is not None:
                out.add(ex.observed[1] + cfg.resource_staleness + _EPS)
    for st in (i for i in history.of("acl_state") if i.data["resource"] == resource):
        for k in ("history_from", "snapshot_extracted_at"):
            if (t := st.get_t(k)) is not None:
                out.add(t)
        synced = st.get_t("acl_synced_at")
        out.add(synced + _EPS)
        if cfg.acl_staleness is not None:
            out.add(synced + cfg.acl_staleness + _EPS)
    return out


def _merge(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    out: list[tuple[datetime, datetime]] = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _state_meta(history: History, resource: str):
    st = _acl_state(history, resource)
    return (st.data["acl_source_version"], st.get_t("acl_synced_at")) if st else (None, None)


def decide(history: History, person: str, permission: str, resource: str, basis: ReadBasis, historical_at: datetime,
           mode: HistoricalAclMode, window: AclWindow | None, evaluated_at: datetime,
           knowledge_cutoff: datetime | None = None, cfg: EvaluationConfig = EvaluationConfig(),
           ) -> HistoricalAclResult:
    """Pure decision over admitted history (already limited to K, latest versions, and R for RECORDED_BY)."""
    ex = existence_of(history, resource)
    version, synced = _state_meta(history, resource)

    def result(outcome, reason, intervals=(), rel=(), acl=(), mem=()):
        return HistoricalAclResult(outcome, mode, basis, historical_at, evaluated_at, window, ex,
                                   [tuple(x) for x in intervals], sorted(rel), sorted(acl, key=repr),
                                   sorted(mem, key=repr), version, synced, reason)

    if mode == "POINT_IN_TIME":
        if window is not None:
            return result("unknown", "window_invalid")
        p = evaluate_at(history, person, permission, resource, historical_at, cfg)
        return result(p.outcome, p.reason, rel=p.relations, acl=p.acl, mem=p.memberships)
    if mode != "ELIGIBILITY_DURING_WINDOW":
        return result("unknown", "evaluator_error")
    # Window rules.
    links = [i for i in history.of("identity_link") if i.data["person"] == person]
    earliest = min((i.valid_from for i in links), default=None)
    limit = historical_at if knowledge_cutoff is None else min(historical_at, knowledge_cutoff)
    if (window is None or window.start.tzinfo is None or window.end.tzinfo is None or window.start >= window.end
            or window.end > limit or earliest is None or window.start < earliest
            or (cfg.max_lookback is not None and window.start < window.end - cfg.max_lookback)):
        return result("unknown", "identity_unresolved" if window and earliest is None else "window_invalid")
    cuts = sorted({window.start, window.end} | {t for t in _boundaries(history, person, resource, cfg)
                                                 if window.start < t < window.end})
    allowed, unknown_reasons, denied_reasons = [], set(), set()
    rel, acl, mem = set(), set(), set()
    for a, b in zip(cuts, cuts[1:], strict=False):
        p = evaluate_at(history, person, permission, resource, a, cfg)   # state is constant on [a, b)
        if p.outcome == "allowed":
            allowed.append((a, b))
            rel |= p.relations
            acl |= p.acl
            mem |= p.memberships
        elif p.outcome == "unknown":
            unknown_reasons.add(p.reason)
        else:
            denied_reasons.add(p.reason)
            acl |= p.acl
    if allowed:
        return result("allowed", "grant_valid", _merge(allowed), rel, acl, mem)
    if unknown_reasons:
        reason = (unknown_reasons.pop() if len(unknown_reasons) == 1 else
                  "resource_availability_unknown" if "resource_availability_unknown" in unknown_reasons
                  else "partial_history")
        return result("unknown", reason, acl=acl)
    if denied_reasons == {"resource_not_available"}:
        return result("denied", "resource_not_available", acl=acl)
    return result("denied", "explicit_deny" if "explicit_deny" in denied_reasons else "no_grant", acl=acl)


def historical_instant(conn: psycopg.Connection, basis: ReadBasis) -> datetime:
    """t_h (§10.7): KNOWN_AT -> sealing time of batch K; RECORDED_BY -> R; VALID_AT -> V; CURRENT -> now."""
    if basis.mode == Mode.KNOWN_AT:
        with tenant_tx(conn, basis.tenant_id):
            row = conn.execute("SELECT sealed_at FROM ledger.admission_batch WHERE tenant_id = %s AND batch_seq = %s"
                               " AND sealed", (basis.tenant_id, basis.batch_seq)).fetchone()
        if row is None:
            raise ValueError("KNOWN_AT basis has no sealed batch")
        return row["sealed_at"]
    if basis.mode == Mode.RECORDED_BY:
        return basis.recorded_cutoff
    return basis.valid_at


def load_history(conn: psycopg.Connection, basis: ReadBasis) -> History:
    """ACL history admitted by K, latest version at K (T8); in RECORDED_BY, only items recorded by R (T2)."""
    p = predicate(basis, "a", k_only=True)
    with tenant_tx(conn, basis.tenant_id, batch_seq=basis.batch_seq):
        rows = conn.execute(psql.SQL(
            'SELECT "a"."source_record_id", "a"."source_version", "a"."item_type", lower("a"."valid") AS vf,'
            ' upper("a"."valid") AS vt, "a"."recorded_hi", "a"."payload" FROM projection.acl_history "a"'
            ' WHERE {} ORDER BY "a"."source_record_id"').format(p.clause), p.params).fetchall()
    items = [Item(r["item_type"], RecordVersionRef(r["source_record_id"], r["source_version"]), r["vf"], r["vt"],
                  r["recorded_hi"], r["payload"]) for r in rows]
    if basis.mode == Mode.RECORDED_BY:
        items = [i for i in items if i.recorded_hi <= basis.recorded_cutoff]
    return History(items)


class HistoricalAclEvaluator:
    """§5.3 HistoricalAclEvaluator over PostgreSQL history. The caller always states the mode."""

    def __init__(self, conn: psycopg.Connection, cfg: EvaluationConfig = EvaluationConfig(), clock=None):
        self.conn, self.cfg = conn, cfg
        self.clock = clock or (lambda: datetime.now().astimezone())

    def evaluate(self, subject: str, permission: str, resource: str, basis: ReadBasis, mode: HistoricalAclMode,
                 window: AclWindow | None = None, knowledge_cutoff: datetime | None = None) -> HistoricalAclResult:
        return self.evaluate_many(subject, permission, [resource], basis, mode, window, knowledge_cutoff)[resource]

    def evaluate_many(self, subject: str, permission: str, resources: list[str], basis: ReadBasis,
                      mode: HistoricalAclMode, window: AclWindow | None = None,
                      knowledge_cutoff: datetime | None = None) -> dict[str, HistoricalAclResult]:
        evaluated_at = self.clock()
        t_h = historical_instant(self.conn, basis)
        history = load_history(self.conn, basis) if basis.batch_seq > 0 else History()
        out = {}
        for r in resources:
            try:
                out[r] = decide(history, subject, permission, r, basis, t_h, mode, window, evaluated_at,
                                knowledge_cutoff, self.cfg)
            except Exception:  # noqa: BLE001 - any evaluator fault fails closed
                out[r] = HistoricalAclResult("unknown", mode, basis, t_h, evaluated_at, window, None, [], [], [], [],
                                             None, None, "evaluator_error")
        return out


def effective_decision(current_authorized: bool, historical: HistoricalAclResult | None) -> bool:
    """§10.7 rule 1-4: current authorization of the requester AND (where a historical question applies) a historical
    `allowed`. Never OR: a historical result can only narrow. `unknown` is `denied`."""
    if not current_authorized:
        return False
    return historical is None or historical.outcome == "allowed"


def knowledge_basis(result: HistoricalAclResult) -> str | None:
    """§14.2 labels for an `allowed` result: participation vs access alone. Access is never reading."""
    if result.outcome != "allowed":
        return None
    if PARTICIPATION & set(result.grant_relations):
        return "participant"
    if result.grant_relations == ["role_entitlement"]:
        return "role_plausible"
    return "possible"
