"""C1 delivery exposure: evidence-backed observations about dated commitments (ARCHITECTURE.md §16.2).

What this detector establishes, with deterministic lexical rules over admitted formal documents and meeting
summaries (never a model, never e-mail):
- a commitment or milestone and the date it is stated for (precise, imprecise, or missing);
- a documented delay or missed milestone;
- the current status of a named commitment, from dated statements (a later statement supersedes an earlier one;
  undated statements cannot be ordered; same-date disagreement is a conflict);
- the C1 measure itself, which stays UNQUANTIFIED: the required-rate ratio needs open-scope facts and a supported
  throughput window, and Q7 (throughput assumptions) is not approved. No probability is computed (§16.4).

What it never does:
- treat a delay as proof of future failure, or a dependency as a quantified impact;
- read amounts: financial consequences belong to C2 (D15-25), so a C1 signal never carries currency;
- resolve an unnamed or ambiguous commitment: it stays unresolved;
- link statements by anything but an identical subject name (flagged as lexical, not entity-resolved).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

import psycopg
from psycopg import sql as psql

from ..db import tenant_tx
from ..temporal import ReadBasis, predicate
from . import timebasis
from .contract import (
    Authority,
    Criterion,
    DetectorOutput,
    Flag,
    Signal,
    SignalType,
    Status,
    Value,
    ValueKind,
    Withheld,
    timed_ref,
)

DETECTOR = "c1_delivery@3"   # @3: revised deadlines, date history, business-object references
MEASURE = "C1.required_rate_ratio@1"
SOURCE_KINDS = {"document": Authority.FORMAL_REPORTED, "transcript": Authority.TRANSCRIPT_SUMMARY}

_MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
                "november", "december"]
_MONTH_RE = (r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sept?(?:ember)?|"
             r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)")
_PREP = r"(?:on|by|before|until)"
def _precise(prep: str) -> list[re.Pattern]:
    return [
        re.compile(rf"\b{prep}\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<m>{_MONTH_RE})\b\.?(?:,?\s+(?P<y>\d{{4}}))?"),
        re.compile(rf"\b{prep}\s+(?P<m>{_MONTH_RE})\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(?P<y>\d{{4}}))?"),
        re.compile(rf"\b{prep}\s+(?P<iso>\d{{4}}-\d{{2}}-\d{{2}})\b"),
    ]


_PRECISE = _precise(_PREP)
# In a delay statement, a NEW date is introduced by "to", "for" or "now" too ("moved GA to 30 June", "GA is now
# on 1 July"). Only stated dates count: a delay without one stays "new date not stated".
_PRECISE_REVISED = _precise(r"(?:on|by|before|until|to|for|now)")
_VAGUE = re.compile(
    rf"\b{_PREP}\s+[\"“]?(?:the\s+)?(?P<v>(?:end of (?:the )?)?(?:{_MONTH_RE}(?:\s+\d{{4}})?|Q[1-4](?:\s+\d{{4}})?|"
    r"(?:festive|holiday) season|(?:this|next) (?:quarter|year|month)|year[- ]end|quarter[- ]end))\b")
_MILESTONE = re.compile(r"\bGA\b|\b(?:general availability|launch\w*|releas\w*|go[- ]live|deliver\w*|ship(?:s|ped|"
                        r"ping)?|milestone|cut-?over|roll-?out)\b", re.I)
_CUE = re.compile(r"\b(?:promis\w*|commit\w*|deadline|due|target\w*|wants|expects?|agreed)\b", re.I)
_FIRM_CUE = re.compile(r"\b(?:promis\w*|committed|commitment|agreed|deadline)\b", re.I)
_DELAY = re.compile(r"\b(?:moved|slipped|slip|delayed|postponed|pushed (?:back|out)|rescheduled|missed|deferred)\b",
                    re.I)
_ON_TRACK = re.compile(r"\bon (?:track|schedule)\b", re.I)
_CONCERN = re.compile(r"\b(?:not sure|unsure|unlikely|at risk|won't make|will not make|doubt\w*|worried)\b", re.I)
_AMBIGUOUS = re.compile(r"\b(?:not clear|unclear|ambiguous)\b", re.I)
_SUBJECT = re.compile(r"^(?P<s>[A-Z][\w-]*(?:\s+[A-Z][\w-]*){0,2})\s+(?:\(|is\b|was\b|will\b|has\b|had\b|remains\b)")
_NOT_SUBJECT = {"we", "the", "our", "she", "he", "it", "they", "this", "that", "there", "i", "you",
                "says", "said", "mentions", "notes", "thinks", "adds", "reports", "asks", "wants", "speaker"}
_SENT_DATE = re.compile(r"Sent\s+(?P<d>\d{1,2})\s+(?P<m>[A-Z][a-z]{2})\w*\s+(?P<y>\d{4})")


@dataclass(frozen=True)
class SourceText:
    record_id: str
    kind: str            # "document" | "transcript"
    title: str
    body: str
    fact_time: timebasis.FactTime | None = None   # R, V and E of this source's statements (None in pure tests)


def _month(name: str) -> int:
    n = name.lower()
    return next(i for i, full in enumerate(_MONTH_NAMES, start=1) if full.startswith(n[:3]))


def recorded_on(src: SourceText) -> date | None:
    """R for a formal document: the date it says it was sent. Meeting summaries state no date (T4)."""
    m = _SENT_DATE.search(src.body)
    if not m:
        return None
    try:
        return date(int(m["y"]), _month(m["m"]), int(m["d"]))
    except (ValueError, StopIteration):
        return None


def sentences(body: str, join_lines: bool = True) -> list[list[str]]:
    """Paragraphs of sentences. Short title-like lines without punctuation are headings (paragraph breaks), so
    wrapped PDF lines join back into sentences and headings never prefix a subject. With join_lines=False (meeting
    summaries, whose lines are independent points) every line is its own paragraph."""
    paras, cur = [], []
    for line in body.splitlines():
        s = line.strip()
        if not join_lines:
            if s:
                paras.append(s)
            continue
        heading = s and len(s.split()) <= 3 and s[-1] not in ".;:!?" and s[0].isupper()
        if not s or heading:
            if cur:
                paras.append(" ".join(cur))
            cur = []
            continue
        cur.append(s)
    if cur:
        paras.append(" ".join(cur))
    return [[x.strip() for x in re.split(r"(?<=[.!?])\s+(?=[A-Z\"“])", p) if x.strip()] for p in paras]


def _subject(sentence: str) -> str | None:
    m = _SUBJECT.match(sentence)
    if not m or m["s"].split()[0].lower() in _NOT_SUBJECT or _MILESTONE.fullmatch(m["s"]):
        return None              # a milestone term ("GA", "Launch") is what is committed to, never the subject
    return m["s"]


def _date(sentence: str, reference: date | None, revised: bool = False,
          ) -> tuple[date | None, set[Flag], str | None, str | None]:
    """(date, flags, matched text, assumption). revised: the sentence records a delay, so 'to <date>' counts."""
    for rx in _PRECISE_REVISED if revised else _PRECISE:
        m = rx.search(sentence)
        if not m:
            continue
        if m.groupdict().get("iso"):
            try:
                return date.fromisoformat(m["iso"]), set(), m.group(0), None
            except ValueError:
                return None, {Flag.DATE_IMPRECISE}, m.group(0), None
        try:
            month, day = _month(m["m"]), int(m["d"])
            if m["y"]:
                return date(int(m["y"]), month, day), set(), m.group(0), None
            if reference is None:
                return None, {Flag.DATE_IMPRECISE}, m.group(0), None
            d = date(reference.year, month, day)
            if d < reference:
                d = date(reference.year + 1, month, day)
            return d, {Flag.YEAR_INFERRED}, m.group(0), (
                f"the source states day and month only; year taken as the first {d:%d %B} on or after the "
                f"source's own date ({reference.isoformat()})")
        except (ValueError, StopIteration):
            return None, {Flag.DATE_IMPRECISE}, m.group(0), None
    v = _VAGUE.search(sentence)
    if v:
        return None, {Flag.DATE_IMPRECISE}, v.group(0), None
    return None, set(), None, None


@dataclass
class _Statement:
    src: SourceText
    authority: Authority
    recorded: date | None
    sentence: str
    locator: str
    subject: str | None
    subject_from_context: bool
    kind: str                       # "commitment" | "delay"
    stated: date | None
    flags: set[Flag]
    date_text: str | None
    assumption: str | None
    on_track: bool
    concern: bool


def extract(sources: list[SourceText]) -> list[_Statement]:
    out = []
    for src in sorted(sources, key=lambda s: s.record_id):
        authority = SOURCE_KINDS[src.kind]
        if src.fact_time is not None:      # the source date as checked against E (R <= E)
            rec = src.fact_time.recorded_day if src.kind == "document" else None
        else:
            rec = recorded_on(src) if src.kind == "document" else None
        prose = src.kind == "document"   # summary points are independent: no subject carries between them
        for pi, para in enumerate(sentences(src.body, join_lines=prose)):
            context_subject = None
            for si, s in enumerate(para):
                subj = _subject(s)
                from_ctx = False
                if subj:
                    context_subject = subj
                milestone, cue, delay = bool(_MILESTONE.search(s)), bool(_CUE.search(s)), bool(_DELAY.search(s))
                stated, flags, dtext, assumption = _date(s, rec, revised=milestone and delay)
                kind = None
                if milestone and delay:
                    kind = "delay"
                elif (milestone or cue) and dtext:
                    kind = "commitment"
                elif _FIRM_CUE.search(s) and milestone:
                    kind, flags = "commitment", flags | {Flag.DATE_MISSING}
                if kind is None:
                    continue
                if subj is None and context_subject and prose:
                    subj, from_ctx = context_subject, True
                flags = set(flags)
                if src.kind == "transcript":
                    flags |= {Flag.NON_BINDING_SOURCE, Flag.RECORDED_DATE_UNKNOWN}
                else:
                    flags.add(Flag.REPORTED_NOT_VERIFIED)
                if subj is None:
                    flags.add(Flag.SUBJECT_UNRESOLVED)
                if from_ctx:
                    flags.add(Flag.SUBJECT_FROM_CONTEXT)
                if _AMBIGUOUS.search(s):
                    flags.add(Flag.SOURCE_STATES_AMBIGUITY)
                concern = bool(_CONCERN.search(s))
                if concern:
                    flags.add(Flag.STATUS_CONCERN_REPORTED)
                if kind == "delay" and not dtext:
                    flags.add(Flag.NEW_DATE_NOT_STATED)
                if kind == "delay" and stated:
                    flags.add(Flag.REVISED_DATE_STATED)
                out.append(_Statement(src, authority, rec, s, f"paragraph {pi + 1}, sentence {si + 1}", subj,
                                      from_ctx, kind, stated, flags, dtext, assumption,
                                      bool(_ON_TRACK.search(s)), concern))
    return out


def _subject_key(st: _Statement) -> str:
    if st.subject:
        return st.subject.lower()
    return f"unresolved:{st.src.record_id}:{st.locator}"


def evaluate(sources: list[SourceText]) -> DetectorOutput:
    """Pure: the C1 signals for these sources."""
    out = DetectorOutput()
    statements = extract(sources)
    groups: dict[str, list[_Statement]] = {}
    for st in statements:
        groups.setdefault(_subject_key(st), []).append(st)
    for key in sorted(groups):
        group = groups[key]
        subject = group[0].subject or "unresolved commitment"
        delays = [g for g in group if g.kind == "delay"]
        commitments = [g for g in group if g.kind == "commitment"]
        multi_record = len({g.src.record_id for g in group}) > 1
        latest_delay = max((d.recorded for d in delays if d.recorded), default=None)
        keys = []
        for st in group:
            flags = set(st.flags)
            if st.kind == "commitment" and st.recorded and latest_delay and latest_delay > st.recorded:
                flags.add(Flag.DATE_SUPERSEDED)
            if multi_record and st.subject:
                flags.add(Flag.LINK_LEXICAL)
            firm = (st.authority == Authority.FORMAL_REPORTED and st.subject is not None
                    and not ({Flag.DATE_IMPRECISE, Flag.DATE_MISSING, Flag.SOURCE_STATES_AMBIGUITY} & flags))
            status = Status.ACTIVE if firm else Status.PROVISIONAL
            if st.kind == "commitment":
                category = ("dated" if st.stated else "date_missing" if Flag.DATE_MISSING in flags
                            else "date_imprecise")
                stype = SignalType.COMMITMENT
            else:
                category = "delay_recorded"
                stype = SignalType.DELIVERY_DELAY
            sig_key = f"{key}|{st.kind}|{st.src.record_id}|{st.locator}"
            keys.append(sig_key)
            observed = [f"{'Commitment or milestone' if st.kind == 'commitment' else 'Delay'} stated: "
                        f"\"{st.sentence}\""]
            if st.date_text:
                observed.append(f"date as written: \"{st.date_text}\"")
            if st.on_track:
                observed.append("the source reports it as on track")
            out.signals.append(Signal(
                DETECTOR, Criterion.C1, stype, status, subject, f"C1.{st.kind}@1",
                Value(ValueKind.CATEGORY, category=category), frozenset(flags), st.authority,
                (timed_ref(st.src.record_id, st.locator, st.sentence, st.src.fact_time),),
                observed=tuple(observed),
                assumptions=(st.assumption,) if st.assumption else (),
                missing=tuple(r for r in (
                    "the commitment is not named in the source" if st.subject is None else None,
                    "no precise date is stated" if st.kind == "commitment" and not st.stated else None,
                    "a new date is not stated" if Flag.NEW_DATE_NOT_STATED in flags else None,
                    "the source states that it is unclear" if Flag.SOURCE_STATES_AMBIGUITY in flags else None,
                ) if r),
                event_date=st.stated, recorded_on=st.recorded, valid_from=st.recorded,
                valid_basis="statement_date" if st.recorded else "unknown",
                calculation=(("business_object", f"commitment:{key}"),) if st.subject else (), key=sig_key))
        # Current status of a named commitment from its dated statements.
        if group[0].subject and len(group) >= 1 and (delays or len(group) > 1):
            dated = sorted((g for g in group if g.recorded), key=lambda g: g.recorded)
            undated = [g for g in group if not g.recorded]
            flags = {Flag.LINK_LEXICAL} if multi_record else set()
            if undated:
                flags.add(Flag.STATUS_ORDER_UNKNOWN)

            def label(g: _Statement) -> str:
                return "delayed" if g.kind == "delay" else "reported_on_track" if g.on_track else "date_stated"

            category = "status_unknown"
            if dated:
                last_day = dated[-1].recorded
                last = {label(g) for g in dated if g.recorded == last_day}
                if len(last) > 1:
                    flags.add(Flag.STATUS_CONFLICT)
                    category = "conflicting_statements"
                else:
                    category = last.pop()
                    if category == "delayed" and Flag.NEW_DATE_NOT_STATED in dated[-1].flags:
                        category = "delayed_new_date_not_stated"
                    elif category == "delayed" and dated[-1].stated:
                        category = "delayed_revised_date_stated"
            revisions = [g for g in sorted(dated, key=lambda g: (g.recorded, g.locator))
                         if g.kind == "delay" and g.stated]
            if len(revisions) > 1:
                flags.add(Flag.MULTIPLE_REVISIONS)
            history = [(f"{g.recorded.isoformat()} {'revised' if g.kind == 'delay' else 'stated'}",
                        g.stated.isoformat()) for g in sorted(dated, key=lambda g: (g.recorded, g.locator))
                       if g.stated]
            current_date = dated[-1].stated if dated and category in ("delayed_revised_date_stated", "date_stated",
                                                                       "reported_on_track") else None
            ev = tuple(timed_ref(g.src.record_id, g.locator, g.sentence, g.src.fact_time) for g in group)
            authority = (Authority.FORMAL_REPORTED if any(g.authority == Authority.FORMAL_REPORTED for g in dated)
                         else Authority.TRANSCRIPT_SUMMARY)
            status = (Status.ACTIVE if dated and Flag.STATUS_CONFLICT not in flags
                      and authority == Authority.FORMAL_REPORTED else Status.PROVISIONAL)
            out.signals.append(Signal(
                DETECTOR, Criterion.C1, SignalType.COMMITMENT_STATUS, status, subject, "C1.commitment_status@1",
                Value(ValueKind.CATEGORY, category=category), frozenset(flags), authority, ev,
                observed=tuple(f"{g.recorded.isoformat() if g.recorded else 'undated'}: {label(g)}"
                               for g in sorted(group, key=lambda g: (g.recorded or date.min, g.locator))),
                assumptions=("the latest dated statement describes the current status; it is not verified "
                             "against delivery records",) if dated else (),
                missing=("statements without a date cannot be ordered",) if undated else (),
                recorded_on=dated[-1].recorded if dated else None,
                valid_from=dated[-1].recorded if dated else None, event_date=current_date,
                valid_basis="statement_date" if dated else "unknown", related=tuple(keys),
                calculation=(("business_object", f"commitment:{key}"),) + tuple(history),
                key=f"{key}|status"))
        # The C1 measure: unquantified under the approved rules.
        if commitments:
            out.signals.append(Signal(
                DETECTOR, Criterion.C1, SignalType.DELIVERY_EXPOSURE, Status.UNQUANTIFIED, subject, MEASURE,
                Value(), frozenset({Flag.THROUGHPUT_NOT_APPROVED, Flag.OPEN_SCOPE_MISSING}),
                commitments[0].authority,
                tuple(timed_ref(g.src.record_id, g.locator, None, g.src.fact_time) for g in commitments),
                missing=("no admitted open-scope record (remaining work) for this commitment",
                         "no supported sustained-throughput window: Q7 throughput assumptions are not approved, "
                         "and a passing diagnostic is never proof that they hold",),
                decisions_pending=("Q7", "D15-08"), related=tuple(keys), key=f"{key}|exposure"))
    return out


def load_sources(conn: psycopg.Connection, basis: ReadBasis, tz: str) -> list[SourceText]:
    """Sources visible at K (latest version at K, T8), each with its FactTime. R and V are applied per fact by
    the caller (k_only), from what each source establishes."""
    p = predicate(basis, "d", k_only=True)
    with tenant_tx(conn, basis.tenant_id, batch_seq=basis.batch_seq):
        rows = conn.execute(psql.SQL(
            'SELECT DISTINCT ON ("d"."source_record_id") "d"."source_record_id", "d"."kind", "d"."title",'
            ' "d"."body", "s"."extracted_at" FROM projection.document_text "d"'
            ' JOIN ledger.source_record "s" ON "s"."tenant_id" = "d"."tenant_id"'
            ' AND "s"."record_id" = "d"."source_record_id" AND "s"."version" = "d"."source_version"'
            ' WHERE {} AND "d"."kind" = ANY(%(kinds)s) AND "d"."data_class" <> ALL(%(protected)s)'
            ' ORDER BY "d"."source_record_id", "d"."batch_seq" DESC').format(p.clause),
            {**p.params, "kinds": list(SOURCE_KINDS),
             "protected": ["sensitive_personal", "payroll_aggregate"]}).fetchall()
    out = []
    for r in rows:
        src = SourceText(r["source_record_id"], r["kind"], r["title"], r["body"])
        day = recorded_on(src) if src.kind == "document" else None
        out.append(SourceText(src.record_id, src.kind, src.title, src.body,
                              timebasis.statement_time(day, r["extracted_at"], tz)))
    return out


def detect(conn: psycopg.Connection, basis: ReadBasis, context: dict) -> DetectorOutput:
    tz = context["time_zone"]
    sources, withheld = [], []
    for s in load_sources(conn, basis, tz):
        reason = timebasis.excluded(s.fact_time, basis, tz)
        if reason:
            withheld.append(Withheld(DETECTOR, Criterion.C1, f"a {s.kind} is not used at this read basis: {reason}",
                                     (s.record_id,)))
        else:
            sources.append(s)
        if s.fact_time.note:
            withheld.append(Withheld(DETECTOR, Criterion.C1, s.fact_time.note, (s.record_id,)))
    out = evaluate(sources)
    out.withheld += withheld
    out.withheld.append(Withheld(DETECTOR, Criterion.C1,
                                 "e-mail is not read by detectors in this version (private communications); "
                                 "challenge brief and data-room readme are not company evidence"))
    if not out.signals:
        out.withheld.append(Withheld(DETECTOR, Criterion.C1,
                                     "no admitted formal document or meeting summary states a commitment, milestone "
                                     "or delay", tuple(s.record_id for s in sources)))
    return out
