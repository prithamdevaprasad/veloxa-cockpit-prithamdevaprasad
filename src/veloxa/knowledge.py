"""Claim-level company knowledge and the Conflict Registry (migration 0021).

Deterministic, rule-based extraction (method names are versioned; never a model) from SEALED ledger records, one
batch at a time inside the projector's transaction, so a claim is visible at K only if its batch_seq <= K.

Each claim keeps: subject, predicate, object (as written), valid time AS WRITTEN with its precision, the pack's
release time (`available_at`), the record id + version, the exact locator, the extraction method and data class.

What is deliberately NOT turned into claims:
- payroll_register (sensitive_personal) and GL payroll lines (payroll_aggregate): protected aggregates (§15);
- leave_register (leave type is protected; K-03);
- pulse comments (suppressed at source); e-mail bodies (private_comms: party visibility is enforced by the tools);
- git commits (allocation evidence only, never per-person productivity, §16.6).

Conflict Registry: claims about the same subject + predicate with incompatible values from DIFFERENT records.
A conflict is resolved automatically ONLY by an explicit precedence rule in PRECEDENCE; otherwise it is
`needs_review` and answers must say "The sources disagree". Newer is never assumed to be truer.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from decimal import Decimal

import psycopg

METHOD_ROWS = "export-rows/1"
METHOD_BOARD = "board-key-numbers/1"           # projector.parse_reported_figures
METHOD_SUMMARY = "summary-figures/1"           # signals.figures.from_summary


@dataclass(frozen=True)
class Claim:
    subject_type: str
    subject: str
    predicate: str
    object: object
    valid_at: str | None
    valid_precision: str
    record_id: str
    record_version: int
    locator: dict
    method: str
    data_class: str
    speaker: str | None = None

    @property
    def claim_id(self) -> str:
        raw = json.dumps([self.subject, self.predicate, self.object, self.record_id, self.record_version,
                          self.locator, self.method], sort_keys=True, default=str)
        return "CL-" + hashlib.sha256(raw.encode()).hexdigest()[:16]


# --- precision of a value as written --------------------------------------------------------------------------
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_INSTANT = re.compile(r"^\d{4}-\d{2}-\d{2}T")
_QUARTER = re.compile(r"^(Jan|Apr|Jul|Oct)-(Mar|Jun|Sep|Dec) \d{4}$")
_MONTH = re.compile(r"^[A-Z][a-z]{2}-\d{4}$")


def precision(v: object) -> str:
    s = str(v or "")
    if not s:
        return "unstated"
    if _INSTANT.match(s):
        return "instant"
    if _DAY.match(s):
        return "day"
    if _QUARTER.match(s):
        return "quarter"
    if _MONTH.match(s):
        return "month"
    if re.search(r"\bto\b|–|-", s) and re.search(r"\d{4}", s):
        return "range"
    return "unstated"


def _v(x):
    return None if x is None or (isinstance(x, str) and not x.strip()) else x


# --- export rows ------------------------------------------------------------------------------------------------
# system -> (subject_type, key column, valid-time column or None, {column: predicate}, valid precision override)
ROW_SPECS: dict[str, tuple[str, str, str | None, dict[str, str]]] = {
    "keka_hrms": ("person", "Emp ID", None, {
        "Full Name": "name", "Department": "department", "Title": "title", "Manager ID": "manager",
        "Join Date": "join_date", "Status": "employment_status", "Exit Date": "exit_date",
        "Work Email": "work_email", "Location": "location"}),
    "hiring_tracker": ("requisition", "Req ID", "Status Date", {
        "Role": "role", "Team": "team", "Hiring Manager": "hiring_manager_source_id", "Opened": "opened",
        "Status": "status", "In pipeline": "candidates_in_pipeline"}),
    "pulse_survey": ("team", "Team", "Quarter", {
        "Team size": "team_size", "Responses": "survey_responses", "Engagement (1-5)": "engagement_score"}),
    "jira": ("issue", "Key", None, {
        "Type": "issue_type", "Summary": "summary", "Epic Link": "epic", "Assignee": "assignee_source_id",
        "Story Points": "story_points", "Status": "status_at_export", "Created": "created", "Resolved": "resolved",
        "Fix Version": "fix_version",
        # Versions sheet
        "Release Date": "release_date", "Original Release Date": "original_release_date",
        "Last Changed": "last_changed", "Changed By": "changed_by_source_id"}),
    "oncall_rota": ("rota_week", "Week of", "Week of", {"Primary": "primary_source_id",
                                                        "Secondary": "secondary_source_id"}),
    "incident_log": ("incident", "ID", "Opened", {"Severity": "severity", "Service": "service",
                                                  "Responder": "responder_source_id", "Opened": "opened",
                                                  "Resolved": "resolved"}),
    "zoho_books": ("voucher", "Voucher", "Txn Date", {"Vendor / Payee": "payee", "Cost Centre": "cost_centre",
                                                      "Amount (Rs.)": "amount_inr", "Txn Date": "transaction_date"}),
    # Generic tracker / release-calendar / recruiting exports (e.g. the synthetic companies' source types)
    "issue_tracker": ("issue", "Issue Key", None, {
        "Summary": "summary", "Epic Key": "epic", "Project": "project", "Status": "status_at_export",
        "Points": "story_points", "Blocked by": "blocked_by", "Fix Version": "fix_version"}),
    "release_calendar": ("version", "Version", None, {
        "Project": "project", "Target date": "release_date", "Original date": "original_release_date",
        "Status": "release_status"}),
    "recruiting": ("requisition", "Requisition", None, {
        "Role": "role", "Department": "team", "Opened": "opened", "Status": "status", "Note": "note"}),
}
DATE_PREDICATES = {"join_date", "exit_date", "opened", "created", "resolved", "release_date", "original_release_date",
                   "last_changed", "transaction_date"}
NEVER_CLAIMED = {"payroll_register", "leave_register", "git", "email", "email_printouts"}


def row_claims(system: str, record: dict) -> list[Claim]:
    spec = ROW_SPECS.get(system)
    if spec is None or record["data_class"] in ("payroll_aggregate", "sensitive_personal"):
        return []
    stype, key_col, valid_col, cols = spec
    p, loc = record["payload"], record["locator"]
    if system == "jira" and loc.get("sheet") == "Versions":
        stype, key_col = "version", "Version"
    key = _v(p.get(key_col))
    if key is None:
        return []
    subject = f"{stype}:{key}"
    valid = _v(p.get(valid_col)) if valid_col else None
    out = []
    for col, pred in cols.items():
        if col not in p or col == key_col and system != "oncall_rota":
            continue
        val = _v(p.get(col))
        if val is None:
            continue                      # absent is unknown, never a value
        v_at, prec = (str(val), precision(val)) if pred in DATE_PREDICATES else (
            (str(valid), precision(valid)) if valid else (None, "unstated"))
        if pred == "manager":
            val = f"person:{val}"
        out.append(Claim(stype, subject, pred, val, v_at, prec, record["record_id"], record["version"], loc,
                         METHOD_ROWS, record["data_class"]))
    return out


# --- figures: board key numbers and figures stated in meeting summaries -----------------------------------------
def board_claims(rows: list[dict]) -> list[Claim]:
    return [Claim("metric", f"metric:{r['metric'].strip().lower()}", "reported_value",
                  {"value_text": r["value_text"], "period": r["period"],
                   "issued_on": r["issued_on"].isoformat() if r["issued_on"] else None},
                  r["period"], precision(r["period"]) if r["period"] else "unstated", r["source_record_id"],
                  r["source_version"], {"document": "key numbers", "metric": r["metric"]}, METHOD_BOARD,
                  r["data_class"]) for r in rows]


def summary_claims(rows: list[dict]) -> list[Claim]:
    from .signals import figures

    out = []
    for r in rows:
        for i, ob in enumerate(figures.from_summary(r["source_record_id"], r["body"])):
            if ob.quality != "extracted" or not ob.label:
                continue
            out.append(Claim("metric", f"metric:{ob.label.lower()}", "stated_value", {
                "span": ob.span, "number": str(ob.number) if ob.number is not None else None, "unit": ob.unit,
                "low": str(ob.low) if ob.low is not None else None,
                "high": str(ob.high) if ob.high is not None else None,
                "period": ob.period, "hedged": ob.hedged}, ob.period, precision(ob.period) if ob.period else "unstated",
                r["source_record_id"], r["source_version"], {"sentence": ob.sentence[:300], "observation": i},
                METHOD_SUMMARY, r["data_class"], speaker=r["title"]))
    return out


# --- conflicts --------------------------------------------------------------------------------------------------
# Explicit precedence rules (documented in private/SOURCE_RECONCILIATION_REGISTER.md). A rule applies ONLY to the
# predicates it names; anything else stays needs_review.
PRECEDENCE: dict[str, tuple[str, str]] = {
    # Employment facts: the HR system of record (keka_hrms) outranks any other mention (ARCHITECTURE.md §6, A-024).
    "employment": ("R-HR-SOR-01", "keka_hrms"),
}
EMPLOYMENT_PREDICATES = {"name", "department", "title", "manager", "join_date", "employment_status", "exit_date"}


def _interval(obj: dict) -> tuple[Decimal, Decimal] | None:
    try:
        if obj.get("low") is not None and obj.get("high") is not None:
            return Decimal(obj["low"]), Decimal(obj["high"])
    except Exception:  # noqa: BLE001
        return None
    return None


def _board_number(value_text: str) -> tuple[str | None, Decimal | None]:
    """A board key number's unit and value as written (INR crore/lakh, %, months, plain count). None if unclear."""
    t = value_text.replace(",", "").strip()
    m = re.match(r"^(?:Rs\.?|INR|\u20b9)?\s*([\d.]+)\s*(crore|cr|lakh|L)?\b", t, re.I)
    if not m:
        return None, None
    n = Decimal(m.group(1))
    scale = {"crore": Decimal(10) ** 7, "cr": Decimal(10) ** 7, "lakh": Decimal(10) ** 5, "l": Decimal(10) ** 5}
    if m.group(2):
        return "INR", n * scale[m.group(2).lower()]
    if "%" in t:
        return "percent", n
    if "month" in t.lower():
        return "months", n
    return None, n


def family(subject: str) -> str:
    """Metric label family: the label before any parenthetical ("arr (recurring)" -> "arr"). Label identity is
    LABEL-ONLY (D15-03a): the registry says the definitions are unconfirmed."""
    return re.sub(r"\s*\(.*\)$", "", subject)


def detect_conflicts(claims: list[dict]) -> list[dict]:
    """claims: rows of knowledge.claim visible at one K. Returns conflict dicts (deterministic ids).

    Values for different valid times (another quarter, another period) are a time series, not a disagreement;
    a corrected record version supersedes (the caller passes latest versions only)."""
    by: dict[tuple, list[dict]] = {}
    metrics: dict[str, list[dict]] = {}
    for c in claims:
        if c["subject_type"] == "metric":
            metrics.setdefault(family(c["subject"]), []).append(c)
        else:
            by.setdefault((c["subject"], c["predicate"], c["valid_at"]), []).append(c)
    out = []
    for (subject, pred, _valid), cs in by.items():
        if len({c["record_id"] for c in cs}) < 2:
            continue
        vals = {json.dumps(c["object"], sort_keys=True) for c in cs}
        if len(vals) < 2:
            continue
        ids = sorted(c["claim_id"] for c in cs)
        rule = preferred = None
        if pred in EMPLOYMENT_PREDICATES:
            rid, system = PRECEDENCE["employment"]
            sor = [c for c in cs if c["record_id"].startswith(system + ":")]
            if sor and len({json.dumps(c["object"]) for c in sor}) == 1:
                rule, preferred = rid, sorted(sor, key=lambda c: c["claim_id"])[0]["claim_id"]
        out.append(_conflict(subject, pred, ids, rule, preferred,
                             "Different records state different values for the same fact at the same valid time."))
    for fam, cs in metrics.items():
        out += _metric_conflicts(fam, cs)
    return out


def _metric_conflicts(fam: str, cs: list[dict]) -> list[dict]:
    """Within one label family: (a) two formal figures for the SAME period with different values; (b) a spoken
    figure with no stated period that matches NO formal figure of the family (same unit, rounding intervals
    disjoint from every one). Different periods never conflict. No precedence rule covers board vs spoken figures,
    so these are always needs_review."""
    board, spoken = [], []
    for c in cs:
        o = c["object"]
        if c["predicate"] == "reported_value":
            unit, n = _board_number(o.get("value_text", ""))
            if n is not None:
                board.append((c, unit, n, n, o.get("period")))
        elif c["predicate"] == "stated_value":
            iv = _interval(o)
            if iv:
                spoken.append((c, o.get("unit"), iv[0], iv[1], o.get("period")))
    groups: list[list[str]] = []
    for i, (a, ua, la, ha, pa) in enumerate(board):
        for b, ub, lb, hb, pb in board[i + 1:]:
            if a["record_id"] != b["record_id"] and pa and pa == pb and ua == ub and (ha < lb or hb < la):
                groups.append(sorted([a["claim_id"], b["claim_id"]]))
    for s, us, ls, hs, ps in spoken:
        same_unit = [b for b in board if b[1] == us and us and (ps is None or b[4] == ps)]
        if same_unit and all(hb < ls or hs < lb for _, _, lb, hb, _ in same_unit):
            groups.append(sorted([s["claim_id"]] + [b[0]["claim_id"] for b in same_unit]))
    out = []
    for ids in groups:
        out.append(_conflict(fam, "value", ids, None, None,
                             "A figure for this metric label cannot be reconciled with the formal figure(s): same "
                             "unit, rounding intervals that do not overlap for any reported period. Identity is by "
                             "label only (the definitions are not confirmed to match)."))
    return out


def _conflict(subject: str, pred: str, ids: list[str], rule: str | None, preferred: str | None, why: str) -> dict:
    cid = "CF-" + hashlib.sha256(json.dumps([subject, pred, ids]).encode()).hexdigest()[:12]
    if rule:
        rationale = (f"{why} Precedence rule {rule} applies: the HR system of record states the employment fact; "
                     "the other statements are kept and cited as disagreeing.")
        uncertainty = "Low for the employment fact; the disagreeing mention may describe a different time or role."
    else:
        rationale = f"{why} No approved precedence rule covers these sources, so no value is chosen."
        uncertainty = "Unresolved: an authorised reviewer must decide, or the sources must be clarified."
    return {"conflict_id": cid, "subject": subject, "predicate": pred, "claim_ids": ids,
            "resolution": "rule" if rule else "needs_review", "rule": rule, "preferred_claim_id": preferred,
            "rationale": rationale, "uncertainty": uncertainty}


# --- build (inside the projector's per-batch transaction) -------------------------------------------------------
def build_batch(conn: psycopg.Connection, tenant_id: uuid.UUID, k: int) -> int:
    release = conn.execute("SELECT release_at FROM ledger.admission_batch WHERE tenant_id = %s AND batch_seq = %s",
                           (tenant_id, k)).fetchone()["release_at"]
    records = conn.execute(
        "SELECT record_id, version, system, data_class, locator, payload FROM ledger.source_record"
        " WHERE tenant_id = %s AND batch_seq = %s AND kind = 'export'", (tenant_id, k)).fetchall()
    claims: list[Claim] = []
    for r in records:
        if r["system"] not in NEVER_CLAIMED:
            claims += row_claims(r["system"], r)
    claims += board_claims(conn.execute(
        "SELECT * FROM projection.reported_figure WHERE tenant_id = %s AND batch_seq = %s", (tenant_id, k)).fetchall())
    claims += summary_claims(conn.execute(
        "SELECT source_record_id, source_version, title, body, data_class FROM projection.document_text"
        " WHERE tenant_id = %s AND batch_seq = %s AND kind = 'transcript'", (tenant_id, k)).fetchall())
    n = 0
    with conn.cursor() as cur:
        for c in claims:
            cur.execute(
                "INSERT INTO knowledge.claim (tenant_id, claim_id, batch_seq, subject_type, subject, predicate, object,"
                " valid_at, valid_precision, available_at, record_id, record_version, locator, extraction_method,"
                " data_class, speaker) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT DO NOTHING",
                (tenant_id, c.claim_id, k, c.subject_type, c.subject, c.predicate, json.dumps(c.object, default=str),
                 c.valid_at, c.valid_precision, release, c.record_id, c.record_version, json.dumps(c.locator),
                 c.method, c.data_class, c.speaker))
            n += cur.rowcount
    # Conflicts visible at K (latest record versions only; a corrected record supersedes, it does not disagree).
    visible = conn.execute(
        "SELECT c.* FROM knowledge.claim c WHERE c.tenant_id = %s AND c.batch_seq <= %s AND c.record_version = ("
        " SELECT max(s.version) FROM ledger.source_record s WHERE s.tenant_id = c.tenant_id"
        " AND s.record_id = c.record_id AND s.batch_seq <= %s)", (tenant_id, k, k)).fetchall()
    for cf in detect_conflicts(visible):
        conn.execute(
            "INSERT INTO knowledge.conflict (tenant_id, conflict_id, batch_seq, subject, predicate, claim_ids,"
            " resolution, rule, preferred_claim_id, rationale, uncertainty) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT DO NOTHING",
            (tenant_id, cf["conflict_id"], k, cf["subject"], cf["predicate"], cf["claim_ids"], cf["resolution"],
             cf["rule"], cf["preferred_claim_id"], cf["rationale"], cf["uncertainty"]))
    return n


def _day(text) -> str | None:
    from datetime import date as _date

    try:
        d = _date.fromisoformat(str(text)[:10])
    except (TypeError, ValueError):
        return None
    return f"{d.day} {d.strftime('%B %Y')}"


def display_value(obj) -> str:
    """Human-readable text for a claim's structured value (reported figure, spoken figure, plain value)."""
    if not isinstance(obj, dict):
        return "" if obj is None else str(obj)
    if obj.get("value_text"):
        bits = [b for b in (obj.get("period"), f"issued {_day(obj['issued_on'])}" if _day(obj.get("issued_on"))
                            else None) if b]
        return f"{obj['value_text']}" + (f" ({'; '.join(bits)})" if bits else "") + ", as reported"
    if obj.get("span") or obj.get("number"):
        said = obj.get("span") or obj.get("number")
        return f"\u201c{said}\u201d, as spoken" + (" (approximate)" if obj.get("hedged") else "") + \
            (f" ({obj['period']})" if obj.get("period") else "") + "; not a recorded figure"
    shown = {k: v for k, v in obj.items() if v not in (None, "", [], {}) and k not in ("unit",)}
    return "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in shown.items()) or "no value recorded"


def conflicts_at(conn: psycopg.Connection, tenant_id: uuid.UUID, k: int) -> list[dict]:
    """Registry entries visible at K, each with its claims and its latest human decision (if any)."""
    rows = conn.execute(
        "SELECT * FROM knowledge.conflict WHERE tenant_id = %s AND batch_seq <= %s ORDER BY batch_seq, conflict_id",
        (tenant_id, k)).fetchall()
    out = []
    for r in rows:
        cl = conn.execute("SELECT * FROM knowledge.claim WHERE tenant_id = %s AND claim_id = ANY(%s)"
                          " ORDER BY claim_id", (tenant_id, r["claim_ids"])).fetchall()
        dec = conn.execute("SELECT * FROM knowledge.conflict_decision WHERE tenant_id = %s AND conflict_id = %s"
                           " ORDER BY at DESC LIMIT 1", (tenant_id, r["conflict_id"])).fetchone()
        out.append({**dict(r), "claims": [dict(c) | {"display": display_value(c["object"])} for c in cl],
                    "decision": dict(dec) if dec else None})
    return out


def backfill(conn: psycopg.Connection, tenant_id: uuid.UUID) -> list[tuple[int, int]]:
    """Claims for sealed batches projected before migration 0021 (idempotent: deterministic ids, append-only)."""
    from .db import tenant_tx

    out = []
    with tenant_tx(conn, tenant_id):
        sealed = [r["batch_seq"] for r in conn.execute(
            "SELECT batch_seq FROM ledger.admission_batch WHERE tenant_id = %s AND sealed ORDER BY 1", (tenant_id,))]
    for k in sealed:
        with tenant_tx(conn, tenant_id, principal_id="projection-worker"):
            out.append((k, build_batch(conn, tenant_id, k)))
    return out
