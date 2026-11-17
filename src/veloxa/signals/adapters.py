"""Source contracts for C2 financial obligations and C3 continuity records (ARCHITECTURE.md §16.2, §16.6).

Rows arrive as admitted JSON Lines records (`records = "rows"`, parser jsonl-rows/1) of these catalogue kinds:

  financial_obligation_export  one incremental financial consequence per row (C2)
  rota_export, qualification_export, ownership_export, absence_export, continuity_event_export  (C3)

No release pack carries such a source today and the production catalogue has no rule for one: adding a rule (and
its data class) is an owner decision. These contracts make the detectors live the moment such a source is
catalogued, admitted and authorised. Each row is validated strictly; an invalid row is never partially used.

C2 rows (D15-04): {"subject", "item_id", "consequence_key", "instrument": contract | invoice | purchase_order |
system_record, "recurrence": one_time | recurring, "amount_low", "amount_high" (null = no upper bound), "due_on" |
"first_due" + "last_due" + "every_months", "outcome_group"?, "offsets"?: [{"offset_id", "low", "high", "support",
"evidence_record"?}], "recorded_on"?}. Amounts are integers in INR. Only binding instruments and system records are
accepted (meeting summaries, e-mail and verbal statements are never binding financial evidence).

C3 rows: rota {"service", "responder", "start", "end", "coverage", "version"?, "source_kind"?};
qualification {"responder", "scope", "qualification", "currency", "source_kind"?}; ownership {"system", "owner",
"currency"}; absence {"scope", "current", "applies_to_scope", "covers_window", "authoritative"}; event {"event":
departure | reassignment | system_change | rota_revision | revocation, "scope", "effective_on", "person"?}.
Currency must be stated explicitly (current | invalidated | currency_unknown): it is never derived from an age,
because Q10 (record-currency rules) is an open owner decision. Events are validated and reported, never applied.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from . import c2_financial as c2
from . import c3_continuity as c3
from .contract import Authority, EvidenceRef

C2_KIND = "financial_obligation_export"
C3_KINDS = {"rota_export", "qualification_export", "ownership_export", "absence_export", "continuity_event_export"}
INSTRUMENTS = {"contract": Authority.BINDING_DOCUMENT, "invoice": Authority.BINDING_DOCUMENT,
               "purchase_order": Authority.BINDING_DOCUMENT, "system_record": Authority.SYSTEM_RECORD}
EVENTS = {"departure", "reassignment", "system_change", "rota_revision", "revocation"}


class AdapterError(ValueError):
    """A source row that does not satisfy its contract. It is withheld whole, with this reason."""


def _req(row: dict, key: str):
    if row.get(key) in (None, ""):
        raise AdapterError(f"{key} is required")
    return row[key]


def _int(row: dict, key: str, optional: bool = False) -> int | None:
    v = row.get(key)
    if v is None and optional:
        return None
    if not isinstance(v, int) or isinstance(v, bool):
        raise AdapterError(f"{key} must be an integer amount in INR")
    return v


def _day(row: dict, key: str, optional: bool = False) -> date | None:
    v = row.get(key)
    if v is None and optional:
        return None
    try:
        return date.fromisoformat(str(v))
    except ValueError as exc:
        raise AdapterError(f"{key} must be an ISO date") from exc


def _bool(row: dict, key: str) -> bool:
    if not isinstance(row.get(key), bool):
        raise AdapterError(f"{key} must be true or false")
    return row[key]


def financial_item(record_id: str, row: dict) -> tuple[str, c2.FinancialItem]:
    """(subject, item) from one admitted obligation row. Raises AdapterError."""
    subject = str(_req(row, "subject"))
    instrument = row.get("instrument")
    if instrument not in INSTRUMENTS:
        raise AdapterError(f"instrument {instrument!r} is not a binding document or system record (D15-04)")
    ref = EvidenceRef(record_id, f"obligation {row.get('item_id')}")
    offsets = []
    for o in row.get("offsets") or []:
        support = c2.OffsetSupport(o.get("support")) if o.get("support") in {s.value for s in c2.OffsetSupport} \
            else None
        if support is None:
            raise AdapterError("offset support must be confirmed, uncertain or unsupported")
        ev = () if support == c2.OffsetSupport.UNSUPPORTED else (EvidenceRef(_req(o, "evidence_record")),)
        offsets.append(c2.Offset(str(_req(o, "offset_id")), _int(o, "low"), _int(o, "high"), support, ev))
    try:
        recurrence = c2.Recurrence(_req(row, "recurrence"))
        item = c2.FinancialItem(
            str(_req(row, "item_id")), str(_req(row, "consequence_key")), INSTRUMENTS[instrument], (ref,),
            recurrence, _int(row, "amount_low"), _int(row, "amount_high", optional=True),
            due_on=_day(row, "due_on", optional=True), first_due=_day(row, "first_due", optional=True),
            last_due=_day(row, "last_due", optional=True), every_months=int(row.get("every_months", 1)),
            outcome_group=row.get("outcome_group"), offsets=tuple(offsets))
    except AdapterError:
        raise
    except ValueError as exc:
        raise AdapterError(str(exc)) from exc
    return subject, item


@dataclass(frozen=True)
class ContinuityEvent:
    event: str
    scope: str
    effective_on: date
    person: str | None
    record: EvidenceRef


def continuity_record(kind: str, record_id: str, row: dict):
    """One typed C3 record from an admitted row. Raises AdapterError (including for leave-derived sources)."""
    ref = EvidenceRef(record_id, kind)
    try:
        if kind == "rota_export":
            return c3.RotaEntry(str(_req(row, "service")), str(_req(row, "responder")), _day(row, "start"),
                                _day(row, "end"), float(_req(row, "coverage")), ref, int(row.get("version", 1)),
                                str(row.get("source_kind", "rota")))
        if kind == "qualification_export":
            return c3.QualificationRecord(str(_req(row, "responder")), str(_req(row, "scope")),
                                          c3.Qualification(_req(row, "qualification")),
                                          c3.Currency(_req(row, "currency")), ref,
                                          str(row.get("source_kind", "qualification")))
        if kind == "ownership_export":
            return c3.OwnershipRecord(str(_req(row, "system")), str(_req(row, "owner")),
                                      c3.Currency(_req(row, "currency")), ref)
        if kind == "absence_export":
            return (str(_req(row, "scope")), c3.AbsenceEvidence(ref, _bool(row, "current"),
                                                                _bool(row, "applies_to_scope"),
                                                                _bool(row, "covers_window"),
                                                                _bool(row, "authoritative")))
        if kind == "continuity_event_export":
            if row.get("event") not in EVENTS:
                raise AdapterError(f"event must be one of {sorted(EVENTS)}")
            return ContinuityEvent(row["event"], str(_req(row, "scope")), _day(row, "effective_on"),
                                   row.get("person"), ref)
    except AdapterError:
        raise
    except c3.InvalidC3Input as exc:
        raise AdapterError(str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise AdapterError(str(exc)) from exc
    raise AdapterError(f"unknown C3 source kind {kind!r}")


# ------------------------------------------------------------------------------------------------- loading at K


@dataclass(frozen=True)
class SourceRow:
    kind: str
    record_id: str
    payload: dict
    fact_time: object       # timebasis.FactTime


def load_rows(conn, basis, kinds: set[str], tz: str):
    """Rows of these kinds admitted by K (latest version at K, T8), each with its FactTime; rows not usable at the
    basis (R after a RECORDED_BY cutoff, or not valid at a VALID_AT instant) are returned as withheld reasons."""
    from ..db import tenant_tx
    from . import timebasis

    with tenant_tx(conn, basis.tenant_id, batch_seq=basis.batch_seq):
        rows = conn.execute(
            "SELECT DISTINCT ON (record_id) record_id, kind, payload, extracted_at FROM ledger.visible_source_record"
            " WHERE kind = ANY(%s) AND data_class NOT IN ('sensitive_personal', 'payroll_aggregate')"
            " ORDER BY record_id, version DESC", (sorted(kinds),)).fetchall()
    usable, withheld = [], []
    for r in rows:
        try:
            day = date.fromisoformat(r["payload"]["recorded_on"]) if r["payload"].get("recorded_on") else None
        except (ValueError, TypeError):
            day = None
        ft = timebasis.statement_time(day, r["extracted_at"], tz)
        reason = timebasis.excluded(ft, basis, tz)
        if reason:
            withheld.append((r["record_id"], reason))
        else:
            usable.append(SourceRow(r["kind"], r["record_id"], r["payload"], ft))
    return usable, withheld


def retime(signal, times: dict):
    """Stamp each cited record's time axes (R, V) onto the signal's evidence references."""
    from dataclasses import replace

    from .contract import timed_ref

    refs = []
    for e in signal.evidence:
        ft = times.get(e.record_id)
        refs.append(replace(timed_ref(e.record_id, e.locator, e.excerpt, ft)) if ft is not None else e)
    return replace(signal, evidence=tuple(refs))
