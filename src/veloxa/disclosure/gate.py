"""C19 disclosure gate: the ONE service through which any protected payroll aggregate may leave (ARCHITECTURE.md §15).

Every channel (metrics, Ask, briefing, evidence views, exports, APIs, caches, and future agent interviews) must call
`DisclosureGate.request` with the REQUESTER's validated principal (for an agent interview: the interviewer, never
the represented employee). There is no other path: the application role cannot read payroll records, and the
only database function that computes a payroll value (`policy.release_payroll_sum`) refuses unless this gate has
recorded an `allowed` decision for that principal and the owner has enabled release for the tenant and measure.

One request, one transaction, serialized per (tenant, principal, measure) by a transaction-scoped advisory lock:
  1. validated tenant and principal context;  2. lock the ledger scope;  3. read the ledger;
  4. background rows;  5. the request's coefficient rows (M2 person-segments, exact pro-rata fractions);
  6. the exact core decision (structure only, never values);  7. append the ledger entry;  8. if allowed, compute the
  value through the database function;  9. commit; only then is the value returned.
A failure anywhere rolls the whole transaction back: no ledger entry, no value (fail closed).

Background rows (§15.3), each with its basis, all decided by CURRENT authorization (C20 ReBAC AND C21 ABAC;
never C32 history, titles or e-mail participation):
- own values: the principal's own person-segments (unit rows);
- individually authorised values: a unit row for each person whose `pay:<person>` resource the principal may
  `read_value` now (an explicit relationship tuple, purpose disclosure_background);
- readable documents with payroll aggregates: for each payroll-aggregate posting the principal may `read_value`
  now (`pay_document:<record id>`), a row over the register cells of the posting's month (ASSUMPTION A-C19-01: a
  monthly payroll posting totals that month's register; listed for owner review);
- public values: none, because no public-value source is approved (PUBLIC_VALUE_SOURCES is empty).
Individually authorised segments are also ALLOWED variables (knowing them is not a violation).
Segments: one per person (M2 default). A segment splits only on a recorded pay change the principal may know; no
pay-change source is admitted, so none splits. Pay values are never used to infer a change.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

import psycopg

from .. import tools
from ..db import tenant_tx
from ..temporal import Mode, ReadBasis
from . import core

MEASURE = "payroll.gross"
PUBLIC_VALUE_SOURCES: tuple[str, ...] = ()   # owner approval required to add any (none approved)
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
ENGINE_VERSION = "c19-gate/1"


@dataclass(frozen=True)
class PayrollRequest:
    request_key: str                        # client idempotency key (a retry reuses it)
    months: tuple[str, ...] | None = None   # register month labels, e.g. "Jan-2099"; None = every month at K
    department: str | None = None           # cohort filter from the HR record at K; None = everyone
    statistic: str = "SUM"
    measure: str = MEASURE

    def fingerprint(self, basis: ReadBasis) -> str:
        body = {"measure": self.measure, "statistic": self.statistic, "department": self.department,
                "months": sorted(self.months) if self.months is not None else None, "k": basis.batch_seq}
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class Release:
    released: bool
    value: Decimal | None                   # set only when released
    decision: str                           # allowed | denied | refused
    reason: str                             # generic; never names a person or a value
    entry_id: str | None = None


class IdempotencyConflict(ValueError):
    pass


def _canon(r: core.Row) -> dict[str, str]:
    return {v: f"{c.numerator}/{c.denominator}" for v, c in sorted(r.items())}


def _parse(d: dict[str, str]) -> core.Row:
    return {v: Fraction(s) for v, s in d.items()}


def segment_var(person: str) -> str:
    return f"seg:{person}:0"


def build_rows(structure: list[dict], cohort: set[str], months: tuple[str, ...] | None,
               ) -> tuple[core.Row, list[str], int]:
    """(coefficient row, covered cells, cohort size). Raises ValueError on ambiguous linkage. No extrapolation: a
    person contributes only the months the register records for them."""
    seen: dict[tuple[str, str], str] = {}
    coeff: dict[str, Fraction] = {}
    cells = []
    for s in structure:
        key = (s["person"], s["month"])
        if key in seen and seen[key] != s["record_id"]:
            raise ValueError("register_ambiguous")          # two records for one person-month: never merged
        seen[key] = s["record_id"]
        if s["person"] not in cohort or (months is not None and s["month"] not in months):
            continue
        if not s["days_in_month"] or s["days_payable"] is None or not 0 <= s["days_payable"] <= s["days_in_month"]:
            raise ValueError("register_incomplete")
        if s["days_payable"]:
            var = segment_var(s["person"])
            coeff[var] = coeff.get(var, Fraction(0)) + Fraction(s["days_payable"], s["days_in_month"])
            cells.append(f"{s['person']}|{s['month']}")
    return coeff, sorted(cells), len(coeff)


class DisclosureGate:
    def __init__(self, k: int = core.DEFAULT_K):
        self.k = k                                    # D-14a architecture default; not changed here

    @staticmethod
    def _background(conn, tenant_id, me: str, structure: list[dict], k_batch: int):
        """(rows, allowed segment variables, provenance) from current authorization only."""
        from ..authz.current import Resource, authorize

        own = {segment_var(me)} if any(s["person"] == me for s in structure) else set()
        rows = [{v: Fraction(1)} for v in own]
        allowed, basis = set(own), [("own_value", me)] if own else []
        for person in sorted({s["person"] for s in structure} - {me}):
            d = authorize(conn, me, tenant_id, "read_value", Resource("pay", person, tenant_id, "sensitive_personal"),
                          "disclosure_background")
            if d.allowed:
                rows.append({segment_var(person): Fraction(1)})
                allowed.add(segment_var(person))
                basis.append(("individually_authorised", person))
        docs = conn.execute("SELECT * FROM policy.payroll_aggregate_documents(%s)", (k_batch,)).fetchall()
        for doc in docs:
            d = authorize(conn, me, tenant_id, "read_value",
                          Resource("pay_document", doc["record_id"], tenant_id, "payroll_aggregate"),
                          "disclosure_background")
            if not d.allowed:
                continue
            month = f"{_MONTHS[doc['txn_date'].month - 1]}-{doc['txn_date'].year}"
            r, _, _ = build_rows(structure, {s["person"] for s in structure}, (month,))
            if r:
                rows.append(r)
                basis.append(("readable_document", doc["record_id"]))
        return rows, allowed, basis

    def request(self, conn: psycopg.Connection, principal: tools.Principal, basis: ReadBasis,
                req: PayrollRequest) -> Release:
        try:
            return self._request(conn, principal, basis, req)
        except IdempotencyConflict:
            return Release(False, None, "refused", "idempotency_key_reused_for_a_different_request")
        except Exception:  # noqa: BLE001 - any fault fails closed, with nothing recorded and nothing released
            return Release(False, None, "refused", "disclosure_auditor_unavailable")

    def _request(self, conn, principal, basis, req) -> Release:
        if principal is None or not str(principal.principal_id).startswith("person:"):
            return Release(False, None, "refused", "principal_unresolved")
        if basis.batch_seq < 1 or basis.mode not in (Mode.CURRENT, Mode.KNOWN_AT):
            return Release(False, None, "refused", "read_basis_not_supported")
        if req.statistic in core.REFUSED or req.statistic not in core.PERMITTED:
            return Release(False, None, "refused", "statistic_not_released")
        me = principal.principal_id
        fp = req.fingerprint(basis)
        with tenant_tx(conn, basis.tenant_id, me, basis.batch_seq):
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                         (f"disclosure|{basis.tenant_id}|{me}|{req.measure}",))
            prior = conn.execute(
                "SELECT entry_id, query_sha256, decision, reason FROM policy.disclosure_ledger WHERE tenant_id = %s"
                " AND principal_id = %s AND measure = %s AND request_key = %s",
                (basis.tenant_id, me, req.measure, req.request_key)).fetchone()
            if prior is not None:                     # a retry: the same decision, no new equation
                if prior["query_sha256"] != fp:
                    raise IdempotencyConflict(req.request_key)
                return self._finish(conn, prior["entry_id"], prior["decision"], prior["reason"])
            history = conn.execute(
                "SELECT seq, rows, decision FROM policy.disclosure_ledger WHERE tenant_id = %s AND principal_id = %s"
                " AND measure = %s ORDER BY seq", (basis.tenant_id, me, req.measure)).fetchall()
            seq = (history[-1]["seq"] if history else 0) + 1
            enabled = conn.execute("SELECT 1 FROM policy.disclosure_release WHERE tenant_id = %s AND measure = %s",
                                   (basis.tenant_id, req.measure)).fetchone()
            if not enabled:
                return self._record(conn, basis, me, req, fp, seq, {}, [], 0, "refused", "release_not_enabled")
            structure = conn.execute("SELECT * FROM policy.payroll_structure(%s)", (basis.batch_seq,)).fetchall()
            roster = {p["entity_id"]: p for p in conn.execute(      # the HR snapshot at K, in THIS transaction
                "SELECT entity_id, department FROM projection.person WHERE batch_seq = (SELECT max(batch_seq)"
                " FROM projection.person WHERE batch_seq <= %s)", (basis.batch_seq,)).fetchall()}
            if any(s["person"] not in roster for s in structure):
                return self._record(conn, basis, me, req, fp, seq, {}, [], 0, "refused", "identity_linkage_incomplete")
            cohort = {pid for pid, p in roster.items() if req.department in (None, p["department"])}
            try:
                q_row, cells, size = build_rows(structure, cohort, req.months)
            except ValueError as exc:
                return self._record(conn, basis, me, req, fp, seq, {}, [], 0, "refused", str(exc))
            if not q_row:
                return self._record(conn, basis, me, req, fp, seq, {}, [], 0, "refused", "no_recorded_cells")
            background, allowed_vars, _ = self._background(conn, basis.tenant_id, me, structure, basis.batch_seq)
            ledger_rows = [_parse(r) for h in history if h["decision"] == "allowed" for r in h["rows"]]
            protected = {segment_var(s["person"]) for s in structure}
            decision = core.decide(background + ledger_rows, core.Query(req.statistic, (q_row,), (size,)),
                                   protected, allowed_vars, self.k)
            return self._record(conn, basis, me, req, fp, seq, q_row, cells, size,
                                "allowed" if decision.allowed else "denied", decision.reason)

    def _record(self, conn, basis, me, req, fp, seq, q_row, cells, size, decision, reason) -> Release:
        entry = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO policy.disclosure_ledger (tenant_id, principal_id, measure, seq, entry_id, request_key,"
            " query_sha256, batch_seq, statistic, cohort_sizes, rows, cells, decision, reason, k, engine_version)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (basis.tenant_id, me, req.measure, seq, entry, req.request_key, fp, basis.batch_seq, req.statistic,
             [size] if size else [], json.dumps([_canon(q_row)] if q_row else []), cells, decision, reason, self.k,
             ENGINE_VERSION))
        return self._finish(conn, entry, decision, reason)

    @staticmethod
    def _finish(conn, entry, decision, reason) -> Release:
        if decision != "allowed":
            return Release(False, None, decision, reason, str(entry))
        value = conn.execute("SELECT policy.release_payroll_sum(%s) AS v", (entry,)).fetchone()["v"]
        return Release(True, value, decision, reason, str(entry))
