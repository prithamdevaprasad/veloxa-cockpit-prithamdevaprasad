"""Plain-English explanations of VERIFIED results, for an executive reader.

Each explainer turns a deterministic result (an answer, a what-if scenario, a ranked worry, a source conflict, the
executive brief) into an `Explanation`: a headline and sections whose every item says where it comes from:

    recorded     stated in admitted records (citations attached)
    calculated   arithmetic Veloxa did on recorded facts, with the calculation shown
    assumption   something the user entered; not evidence
    unknown      what the evidence does not establish, and why

The deterministic text is complete and correct on its own; nothing here calls a model. `to_model_input` and
`apply_wording` let the governed model client (synthetic tenants only) RE-WORD the items afterwards; the stricter
`check` keeps every number, keeps unknowns phrased as unknown and assumptions as assumptions, and rejects added
causes, probabilities, consequences or advice. Any rejection or failure leaves the deterministic text in place.
"""

from __future__ import annotations

import re

BASIS_LABEL = {"recorded": "From the records", "calculated": "Calculated by Veloxa",
               "assumption": "Your assumption (not evidence)", "unknown": "Not established by the evidence",
               "caveat": "How to read these figures"}

# detector and pipeline wording translated for an executive (meaning kept; the original stays in the raw view)
PLAIN = [
    ("no admitted open-scope record (remaining work) for this commitment",
     "no record shows how much work remains on this commitment"),
    ("no supported sustained-throughput window: Q7 throughput assumptions are not approved, and a passing diagnostic "
     "is never proof that they hold", "Veloxa has no approved way to measure how fast this team delivers, so it does "
                                      "not estimate a delivery pace"),
    ("open scope and throughput are not admitted: the required rate (C1 measure) is not recomputed",
     "the records do not show how much work remains or how fast the team completes it, so Veloxa cannot say whether "
     "the team could still deliver on time"),
    ("no probability is given: the evidence gates of §16.4 are not met",
     "no likelihood is given, because the records are not sufficient to estimate one"),
]
META = ("facts only:",)                    # pipeline notes about HOW the text was produced, not about the company

CRITERION = {"C1": "how far the delivery date has slipped", "C2": "revenue or contract value exposed",
             "C3": "dependence on a small number of people", "C4": "disagreement between reported figures"}


def _item(i: str, text: str, evidence=None, detail: str | None = None) -> dict:
    out = {"id": i, "text": text.strip().rstrip(".") + ".", "evidence": list(evidence or [])}
    if detail:
        out["detail"] = detail
    return out


def _section(key: str, title: str, basis: str, items: list[dict]) -> dict:
    return {"key": key, "title": title, "basis": basis, "basis_label": BASIS_LABEL[basis], "items": items}


def _cap(t: str) -> str:
    return t[:1].upper() + t[1:] if t else t


def _explanation(kind: str, headline: str, sections: list[dict], confidence: str | None, status: str = "explained",
                 **extra) -> dict:
    sections = [s for s in sections if s["items"]]
    n = 0
    for s in sections:                                            # stable ids E1..En across all sections
        for it in s["items"]:
            n += 1
            it["id"] = f"E{n}"
    return {"kind": kind, "status": status, "headline": headline, "sections": sections, "confidence": confidence,
            "summary": _summary(headline, sections, status),
            "wording": facts_only(NOT_ENABLED, "not requested"), **extra}


NOT_ENABLED = "AI-assisted wording isn't enabled for this workspace. Showing the verified explanation instead."
NOT_USED = "AI-assisted wording wasn't used here. Showing the verified explanation instead."


def facts_only(notice: str, diagnostic: str, **more) -> dict:
    """The deterministic explanation stands. `notice` is for the reader; `diagnostic` is for Admin and logs."""
    return {"mode": "facts_only", "notice": notice, "diagnostic": diagnostic, **more}


def _summary(headline: str, sections: list[dict], status: str) -> str:
    """A short plain paragraph from the verified items (the model may re-word it; it never adds to it)."""
    if status == "abstained":
        why = [it["text"] for s in sections if s["basis"] == "unknown" for it in s["items"]][:1]
        return " ".join([headline] + why)
    facts = [it["text"] for s in sections if s["basis"] in ("recorded", "calculated") for it in s["items"]][:2]
    gaps = [s for s in sections if s["basis"] == "unknown"]
    tail = ["Some points are not established by the records; they are listed below."] if gaps else []
    return " ".join([headline] + facts + tail)


# ---------------------------------------------------------------- what-if
def scenario(s: dict) -> dict:
    obj = s["business_object"]
    shift = s["assumptions"][0].split(" moves by ")[-1].split(" day")[0] if s["assumptions"] else "?"
    recorded = [_item("", _cap(o["statement"]) + (f" (stated date {o['stated_date']})" if o.get("stated_date")
                                                  and o["stated_date"] not in _dates(o["statement"])[0] else "")
                      + (f", recorded on {o['recorded_on']}" if o.get("recorded_on") else ""), o.get("evidence"))
                for o in s["observed"] if o["statement"] != o.get("signal_type", "").replace("_", " ")
                and not (o["statement"].lower().startswith("date as written") and any(
                    x is not o and x.get("stated_date") == o.get("stated_date") for x in s["observed"]))]
    calc = [_item("", _cap(re.sub(r"\(was (-?\d+)\)$", r"compared with \1 day(s) before the change",
                                  d["statement"])), d.get("evidence"), detail=f"Calculation: {d['calculation']}")
            for d in s["derived"]]
    implications = []
    if s["derived"]:
        implications.append(_item("", f"The only effect Veloxa can calculate is the new date for {obj}; the "
                                  "records do not link this date to revenue, customers or other teams"))
    unknown = [_item("", _cap(u)) for u in dict.fromkeys(_plain_unknown(x) for x in s["unknowns"])]
    headline = (f"If {obj} moves by {shift} day(s), " + (s["derived"][0]["statement"].replace("shifted date",
                "the new date would be") if s["derived"] else "Veloxa cannot calculate a new date") + ".")
    return _explanation("scenario", headline, [
        _section("commitment", "What was originally committed", "recorded", recorded),
        _section("change", "The change you are testing", "assumption",
                 [_item("", f"{obj} moves by {shift} day(s). This is your input, not a recorded plan")]),
        _section("result", "What Veloxa calculated", "calculated", calc),
        _section("implications", "Business implications the records support", "calculated", implications),
        _section("unknown", "What cannot be determined", "unknown", unknown),
    ], confidence=None, probability=None)


def _plain_unknown(u: str) -> str:
    for a, b in PLAIN:
        u = u.replace(a, b)
    return u


# ---------------------------------------------------------------- worries
def worry(item: dict, *, ranked: bool, total_ranked: int = 0) -> dict:
    sev = item.get("severity") or {}
    crit = sev.get("criteria", {})
    recorded, calc, unknown = [], [], []
    from .risk.ranking import NO_MEASURE

    for c, v in sorted(crit.items()):
        name = CRITERION.get(c, c)
        if v.get("reason") == NO_MEASURE:            # never declared for this concern: not one of ITS unknowns
            continue
        if v["status"] == "measured":
            recorded.append(_item("", f"{_cap(name)}: {v['reason']}", item.get("evidence")))
        elif v["status"] in ("missing", "unquantified", "unmapped"):
            unknown.append(_item("", f"{_cap(name)} is not known: {v['reason']}. Veloxa counts this as anywhere "
                                 "from no exposure to the maximum, never as zero"))
    if not recorded and item.get("evidence"):        # every concern cites the records it comes from
        n = len(item["evidence"])
        recorded.append(_item("", f"The concern is recorded in {n} company record{'s' if n != 1 else ''}",
                              item["evidence"]))
    if ranked:
        calc.append(_item("", f"In the {group_label(item['rank']).lower()} by the approved D-15 method"
                          + (". The evidence does not allow these concerns to be ordered individually"
                             if item.get("tied") else ""), detail=f"D-15 position {item['rank']} of {total_ranked}"))
        calc.append(_item("", f"Exposure range {sev['low']:.2f} to {sev['high']:.2f} on a 0 to 1 scale; this is an "
                          "ordering of exposure, not a probability",
                          detail=f"{round(sev.get('measured_weight', 0) * 100)}% of the method's weight is measured"))
        if item.get("sensitive"):
            calc.append(_item("", "Its position changes if the method's weights move by 5 points"))
    else:
        for r in item.get("reasons", []):
            unknown.append(_item("", f"Not ranked: {r}"))
    for u in item.get("unknowns", []):
        unknown.append(_item("", _cap(u)))
    if not ranked and not item.get("unknowns"):
        unknown.append(_item("", "The records do not establish enough information to assess this concern"))
    follow = [_item("", item["follow_up"])] if item.get("follow_up") else []
    headline = (f"{item['title']}." if ranked else f"{item['title']}: recorded, but not enough is measured to rank it.")
    return _explanation("worry", headline, [
        _section("recorded", "What the records show", "recorded", recorded),
        _section("ranking", "How Veloxa assessed this", "calculated", calc),
        _section("unknown", "What is not known", "unknown", unknown),
        _section("next", "Suggested next step (from the ranking rules)", "calculated", follow),
    ], confidence=item.get("confidence"))


def group_label(rank: int) -> str:
    """Executive wording for a D-15 rank band (the band number itself stays in the technical details)."""
    return {1: "Highest attention group", 2: "Next attention group"}.get(rank, "Further attention group")


# ---------------------------------------------------------------- conflicts
def conflict(c: dict) -> dict:
    claims = c["claims"]
    from .knowledge import display_value

    what = _subject(c)
    recorded = [_item("", f"{_source(cl)}: {what} {cl.get('display') or display_value(cl.get('object'))}",
                      [cl["record_id"]]) for cl in claims]
    calc = []
    if c["resolution"] == "rule" and c.get("preferred_claim_id"):
        pref = next((cl for cl in claims if cl["claim_id"] == c["preferred_claim_id"]), None)
        if pref:
            calc.append(_item("", f"Rule {c['rule']} prefers {_source(pref)}: {c['rationale']}", [pref["record_id"]]))
    else:
        calc.append(_item("", f"No precedence rule settles this, so it needs a person to review it. {c['rationale']}"))
    unknown = [_item("", _cap(c["uncertainty"]))]
    if c.get("decision"):
        d = c["decision"]
        chosen = next((cl for cl in claims if cl["claim_id"] == d.get("preferred_claim_id")), None)
        calc.append(_item("", f"A reviewer ({d.get('reviewer')}) recorded a decision: "
                          + (f"prefer {_source(chosen)}" if chosen else "left unresolved")
                          + f". {d.get('rationale', '')}", [chosen["record_id"]] if chosen else []))
    headline = f"The sources disagree about {what}. Each figure is shown separately; none is chosen."
    return _explanation("conflict", headline, [
        _section("recorded", "What each source says", "recorded", recorded),
        _section("resolution", "How Veloxa treats the disagreement", "calculated", calc),
        _section("unknown", "What remains uncertain", "unknown", unknown),
    ], confidence="conflicting")


def _subject(c: dict) -> str:
    subj = str(c.get("subject", ""))
    if subj.startswith("metric:"):
        return {"arr": "ARR (annual recurring revenue)"}.get(subj.split(":", 1)[1], subj.split(":", 1)[1].upper())
    return f"{c.get('predicate', '').replace('_', ' ')} for {subj}".strip()


_SOURCES = {"documents": "A board update", "meeting_summaries": "A call transcript", "email": "An e-mail",
            "keka_hrms": "The HR export", "zoho_books": "The general ledger", "jira": "The Jira export"}


def _source(cl: dict) -> str:
    rid = cl.get("record_id", "")
    system = rid.split(":", 1)[0] if ":" in rid else ""
    return _SOURCES.get(system, f"The {system.replace('_', ' ')} source" if system else "A record")


# ---------------------------------------------------------------- answers (including abstentions and refusals)
def answer(a: dict) -> dict:
    status = a.get("status")
    conf = (a.get("confidence") or {}).get("label")
    if status == "refused":
        why = (a.get("refusal") or {}).get("reason") if isinstance(a.get("refusal"), dict) else a.get("refusal")
        return _explanation("answer", "Veloxa will not answer this question.", [
            _section("why", "Why", "unknown", [_item("", _cap(str(
                why or "it asks for information this workspace protects, such as individual pay or private data")))]),
        ], confidence=conf, status="refused")
    recorded, calc, unknown = [], [], []
    facts = {f.get("fact_id"): f for f in a.get("facts", [])}
    for c in a.get("claims", []):
        kinds = {facts[e].get("kind") for e in c.get("evidence", []) if e in facts}
        target = calc if "derived" in kinds else recorded
        target.append(_item("", c["text"].replace("⟦", "").replace("⟧", ""),
                            [e for e in c.get("evidence", []) if e not in facts]))
    for u in a.get("unknowns", []):
        unknown.append(_item("", _cap(_plain_unknown(u))))
    caveats = [_item("", _cap(q)) for q in a.get("qualifiers", []) if not q.lower().startswith(META)]
    if status == "abstained":
        headline = "Veloxa cannot answer this from the admitted records."
        if not unknown:
            unknown.append(_item("", "No admitted record answers this question"))
    elif (a.get("diagnostics") or {}).get("stage") == "routed" and a.get("answer"):
        headline = a["answer"]
    else:
        headline = (a.get("interpretation") or "").replace("I read this as: ", "You asked about ").split(" Time:")[0]
        headline = headline.rstrip(".") + "."
    return _explanation("answer", headline, [
        _section("recorded", "What the records say", "recorded", recorded),
        _section("calculated", "What Veloxa calculated from them", "calculated", calc),
        _section("caveat", "How to read these figures", "caveat", caveats),
        _section("unknown", "What the records do not establish" if status != "abstained" else "Why there is no answer",
                 "unknown", unknown),
    ], confidence=conf, status="abstained" if status == "abstained" else "explained")


# ---------------------------------------------------------------- brief
def brief(lines: list[dict]) -> dict:
    return _explanation("brief", "Where the company stands today.", [
        _section("recorded", "Verified facts", "recorded",
                 [_item("", ln["text"], ln.get("evidence")) for ln in lines]),
    ], confidence="Deterministic")


# ---------------------------------------------------------------- model wording (governed, synthetic only)
NO_MODEL = {"refused"}                    # refusals never reach a model, whatever the tenant
_NUM = re.compile(r"\d+(?:[.,]\d+)*")
_MONTH = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b")
_CAP = re.compile(r"(?<![.!?:]\s)(?<!^)\b(?!Veloxa\b)(?!(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
                  r"[a-z]*\b)[A-Z][a-zA-Z]{2,}\b")
_ADDED = re.compile(r"\b(because|due to|caus\w*|lead(s)? to|result(s|ed)? in|likely|unlikely|probab\w*|chance|"
                    r"risk of|expect\w*|should|must|recommend\w*|advis\w*|suggest\w*|will (?:hurt|harm|delay|"
                    r"impact|affect|cost|lose)|I think|guess)\b", re.I)
_UNKNOWN_MARK = re.compile(r"\b(not|no|cannot|can't|unknown|isn't|aren't|doesn't|don't|unavailable|insufficient|"
                           r"missing|unclear|uncertain|without|neither|nor)\b", re.I)
_ASSUME_MARK = re.compile(r"\b(assum\w*|hypothetical\w*|if|your input|you entered|scenario|not a recorded)\b", re.I)


_MONTHS = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                            "nov", "dec"))}
_MN = r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
_DATES = [  # (pattern, (year, month, day) group order); month may be a name
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), (1, 2, 3)),
    (re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)? {_MN},? (\d{{4}})\b"), (3, 2, 1)),
    (re.compile(rf"\b{_MN} (\d{{1,2}})(?:st|nd|rd|th)?,? (\d{{4}})\b"), (3, 1, 2)),
]


def _dates(t: str) -> tuple[list[str], str]:
    """Calendar days in `t` (any common format, as ISO strings) and `t` with them removed."""
    found = []
    for rx, (y, m, d) in _DATES:
        def sub(mt, y=y, m=m, d=d):
            mon = mt.group(m)
            mon = _MONTHS[mon[:3].lower()] if not mon.isdigit() else int(mon)
            found.append(f"{int(mt.group(y)):04d}-{mon:02d}-{int(mt.group(d)):02d}")
            return " "
        t = rx.sub(sub, t)
    return sorted(found), t


def _nums(t: str) -> list[str]:
    return sorted(n.replace(",", "") for n in _NUM.findall(_dates(t)[1]))


def items_of(e: dict) -> list[tuple[dict, dict]]:
    return [(s, it) for s in e["sections"] for it in s["items"]]


def to_model_input(e: dict, evidence: list[dict] | None = None) -> dict:
    """The explanation as the wording task's input: one fact per item, labelled with its basis. `evidence` carries
    the cited records' data classes so the governed client's class policy applies."""
    return {"status": "explained", "audit_id": f"explain-{e['kind']}-{e['headline'][:40]}",
            "interpretation": e["headline"], "confidence": {"label": e.get("confidence")},
            "claims": [{"text": f"[{s['basis_label']}] {it['text']}", "evidence": it["evidence"]}
                       for s, it in items_of(e)],
            # every item carries the classes of ALL records the explanation cites (conservative); an explanation
            # citing no record at all (e.g. why there is no answer) is Veloxa's own text: class `internal`
            "evidence": list(evidence or []) or [{"record_id": None, "data_class": "internal"}],
            "unknowns": [], "qualifiers": []}


def check(output: dict, e: dict) -> list[str]:
    """Reasons to reject a model re-wording of an explanation (empty = accepted)."""
    if not isinstance(output, dict) or not isinstance(output.get("claims"), list) \
            or not isinstance(output.get("summary"), str):
        return ["output does not match the schema"]
    pairs = items_of(e)
    got = {c.get("fact_id"): str(c.get("text", "")) for c in output["claims"] if isinstance(c, dict)}
    want = {f"C{i + 1}": (s, it) for i, (s, it) in enumerate(pairs)}
    why = []
    if set(got) != set(want) or len(output["claims"]) != len(want):
        why.append("item ids changed, missing or duplicated")
    every = " ".join(it["text"] for _, it in pairs) + " " + e["headline"]
    all_nums, all_dates = set(_nums(every)), set(_dates(every)[0])
    for fid, text in got.items():
        if fid not in want:
            continue
        s, it = want[fid]
        src = it["text"]
        # every number and date of the item is kept; anything added must itself be a verified number or date
        if not set(_nums(src)) <= set(_nums(text)) or not set(_nums(text)) <= all_nums:
            why.append(f"{fid}: a number was added, changed or dropped")
        if not set(_dates(src)[0]) <= set(_dates(text)[0]) or not set(_dates(text)[0]) <= all_dates or \
                not set(_MONTH.findall(_dates(src)[1])) <= set(_MONTH.findall(_dates(text)[1])):
            why.append(f"{fid}: a date was changed or dropped")
        new = {w for w in _CAP.findall(text) if w.lower() not in src.lower()}
        if new:
            why.append(f"{fid}: new names or entities {sorted(new)[:3]}")
        added = {m.group(0).lower() for m in _ADDED.finditer(text)} - {m.group(0).lower() for m in _ADDED.finditer(src)}
        if added:
            why.append(f"{fid}: adds a cause, likelihood, consequence or advice {sorted(added)[:3]}")
        if s["basis"] == "unknown" and not _UNKNOWN_MARK.search(text):
            why.append(f"{fid}: an unknown was worded as if it were established")
        if s["basis"] == "assumption" and not _ASSUME_MARK.search(text):
            why.append(f"{fid}: an assumption was worded as if it were a recorded fact")
    summ = output["summary"]
    if not set(_nums(summ)) <= set(_nums(every)) or not set(_dates(summ)[0]) <= set(_dates(every)[0]):
        why.append("summary: a number or date not in the verified items")
    new = {w for w in _CAP.findall(summ) if w.lower() not in every.lower()}
    if new:
        why.append(f"summary: new names or entities {sorted(new)[:3]}")
    added = {m.group(0).lower() for m in _ADDED.finditer(summ)} - {m.group(0).lower() for m in _ADDED.finditer(every)}
    if added:
        why.append(f"summary: adds a cause, likelihood, consequence or advice {sorted(added)[:3]}")
    if e["status"] == "abstained" and not _UNKNOWN_MARK.search(summ):
        why.append("summary: turns a missing answer into a confident one")
    return why


def reword(e: dict, tenant: dict, client, evidence: list[dict] | None = None) -> dict:
    """Attach `wording` to the explanation. Never raises; the deterministic items are never modified."""
    from .agents.model import ModelOutputInvalid, ModelUnavailable
    from .ask import wording as answer_wording
    from .llm.gemini import QuotaExceeded, TenantNotPermitted, check_tenant

    if e["status"] in NO_MODEL or not e["sections"]:
        e["wording"] = facts_only(NOT_USED, f"not sent to a model ({e['status']})")
        return e
    try:
        check_tenant(tenant["slug"], tenant["synthetic"])
    except TenantNotPermitted as x:
        e["wording"] = facts_only(NOT_ENABLED, str(x))
        return e
    try:
        resp = client.call(answer_wording.request_for(to_model_input(e, evidence), tenant["tenant_id"]))
    except (ModelUnavailable, ModelOutputInvalid, QuotaExceeded) as x:
        e["wording"] = facts_only(NOT_USED, f"the explanation model is unavailable ({type(x).__name__})",
                                  fallback=True)
        return e
    reasons = check(resp.output, e)
    if reasons:
        e["wording"] = facts_only(NOT_USED, "the model's wording changed or added to the verified facts, so it "
                                  "was discarded", fallback=True, rejections=reasons[:5])
        return e
    by_id = {c["fact_id"]: c["text"] for c in resp.output["claims"]}
    e["wording"] = {"mode": "experimental_model", "model": resp.model, "summary": resp.output["summary"],
                    "items": {it["id"]: by_id[f"C{i + 1}"] for i, (_, it) in enumerate(items_of(e))},
                    "notice": "Worded with AI assistance from the verified facts. Numbers, dates, names, sources "
                              "and confidence are unchanged."}
    return e


def with_model(conn, tenant_id, e: dict, evidence: list[dict] | None = None) -> dict:
    """Ask the governed client to re-word `e` if, and only if, the experimental model is configured. Called only
    AFTER the deterministic result exists; any failure leaves the deterministic explanation unchanged."""
    from .db import tenant_tx
    from .llm import config as llm_config
    from .llm.client import GovernedLLMClient

    policy, provider = llm_config.policy_and_provider()
    if provider is None or getattr(provider, "name", "") != "gemini":
        e["wording"] = facts_only(NOT_ENABLED, "no explanation model is configured (DENY_ALL)")
        return e
    with tenant_tx(conn, tenant_id):
        t = conn.execute("SELECT slug, synthetic FROM directory.tenant WHERE tenant_id = %s", (tenant_id,)).fetchone()
    tenant = {"tenant_id": tenant_id, "slug": t["slug"], "synthetic": bool(t["synthetic"])}
    return reword(e, tenant, GovernedLLMClient(conn, policy, provider), evidence)
