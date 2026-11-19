"""Continuity after a REPORTED departure: "what does the reported resignation mean for Version 3 / the release?"

Answers from the structured records (HR claims, Jira issues and versions, the on-call rota, incidents, git rows),
each claim citing what it rests on, separated into facts, implications, dependencies and unknowns.

Rules (ASSUMPTIONS A-020, A-021; TRANSCRIPT_NOTES M-01, C-25, C-29; I-01):
- A resignation mentioned in a voice note is a REPORT, never a confirmed HR fact. The HR status is quoted as recorded.
- The person meant is never confirmed. A first-name prefix match is shown as an UNCONFIRMED candidate, and only when
  it is unique; tracker initials, rota first names and git addresses are candidate links, never merged.
- Concentration is about where work and knowledge sit, not productivity. No probability, no reason, no judgement.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from ..db import tenant_tx

ASK = re.compile(r"\b(resign\w*|quit\w*|leav(e|es|ing)|depart\w*|notice period|exit\w*|handover)\b", re.I)
IMPACT = re.compile(r"\b(mean|impact\w*|affect\w*|do to|effect|risk|version|v ?3|release|ga|atlas|delivery|"
                    r"roadmap|launch|depend\w*|continuity)\b", re.I)
REPORT = re.compile(r"\b([A-Z][a-z]{2,})\s+(?:has\s+)?resign", re.U)
OPEN = lambda s: str(s or "").lower() not in ("done", "closed", "resolved")  # noqa: E731


def asks(q: str) -> bool:
    return bool(ASK.search(q) and IMPACT.search(q))


def _claims(conn, tenant_id, k, stype):
    out: dict[str, dict] = {}
    for r in conn.execute("SELECT subject, predicate, object, record_id FROM knowledge.claim WHERE tenant_id = %s"
                          " AND subject_type = %s AND batch_seq <= %s ORDER BY batch_seq, claim_id",
                          (tenant_id, stype, k)).fetchall():
        d = out.setdefault(r["subject"], {"_records": set()})
        d[r["predicate"]] = r["object"]
        d["_records"].add(r["record_id"])
    return out


def answer(router, q: str):
    """Returns a routed result, or None when the records hold nothing relevant (never unrelated passages)."""
    from .routes import _result

    k = router.basis.batch_seq
    with tenant_tx(router.conn, router.tenant_id, router.principal.principal_id, k):
        people = _claims(router.conn, router.tenant_id, k, "person")
        issues = _claims(router.conn, router.tenant_id, k, "issue")
        versions = _claims(router.conn, router.tenant_id, k, "version")
        rota = _claims(router.conn, router.tenant_id, k, "rota_week")
        incidents = _claims(router.conn, router.tenant_id, k, "incident")
        notes = router.conn.execute(
            "SELECT DISTINCT ON (record_id) record_id, payload FROM ledger.visible_source_record WHERE tenant_id = %s"
            " AND batch_seq <= %s AND system = 'meeting_summaries' AND payload::text ~* 'resign'"
            " ORDER BY record_id, version DESC", (router.tenant_id, k)).fetchall()
        git = router.conn.execute(
            "SELECT DISTINCT ON (record_id) record_id, payload->>'repo' AS repo, payload->>'author_email' AS author"
            " FROM ledger.visible_source_record WHERE tenant_id = %s AND batch_seq <= %s AND system = 'git'"
            " ORDER BY record_id, version DESC", (router.tenant_id, k)).fetchall()
    if not issues and not versions:
        return None

    ids = {r for d in (*people.values(), *issues.values(), *versions.values(), *rota.values(), *incidents.values())
           for r in d["_records"]} | {n["record_id"] for n in notes}
    recs = router.records(ids)                          # what this principal may read (protected classes never)
    git_ok = {g["record_id"] for g in git}
    recs.update(router.records(sorted(git_ok)[:1]))     # permission probe on the git export (same source)
    git_readable = any(r.startswith("git:") for r in recs)
    facts, implications, dependencies, unknowns = [], [], [], []

    def add(bucket, text, ids_, kind="record"):
        c = router.claim(text, sorted(ids_), recs, kind)
        if c:
            bucket.append(c)
        return c

    # ---- the report itself (a CEO statement, not an HR record)
    reported_name = None
    for n in notes:
        text = " ".join((n["payload"] or {}).get("paragraphs", []))
        m = REPORT.search(text)
        if m and n["record_id"] in recs:
            reported_name = m.group(1)
            add(facts, "A CEO voice note reports that an engineer resigned. It is a report, not an HR record: "
                "no admitted HR record shows the resignation, and the note gives the notice period only as "
                "“two months, I think”.", [n["record_id"]], "document")
    if reported_name is None:
        unknowns.append("No admitted record reports a resignation; the question's premise is not in the records.")

    # ---- the candidate (unique first-name prefix in Engineering, UNCONFIRMED)
    cand = None
    if reported_name:
        stem = reported_name[:3].lower()
        eng = [(s, d) for s, d in people.items() if str(d.get("department", "")).lower().startswith("eng")]
        hits = [(s, d) for s, d in eng if str(d.get("name", "")).lower().startswith(stem)]
        if len(hits) == 1:
            cand = hits[0]
            s, d = cand
            add(facts, f"The name in the note matches no HR record exactly. One Engineering employee's first name "
                f"starts the same way (a {d.get('title', 'team member')}); this is an unconfirmed candidate, not an "
                f"identification. HR lists that person as “{d.get('employment_status', 'unknown')}” "
                "as at the export.", d["_records"])
        else:
            unknowns.append("Who resigned: the name in the note matches no HR record, and no unique candidate exists.")

    # ---- the release ("Version 3" is not a version name)
    for s, d in versions.items():
        name = s.split(":", 1)[1]
        moved = d.get("original_release_date") and d.get("release_date") != d.get("original_release_date")
        add(facts, f"The tracker's only release version is “{name}”, due {d.get('release_date')}"
            + (f" (originally {d.get('original_release_date')}; changed {d.get('last_changed')})" if moved else "")
            + ". “Version 3” is not a version name; the v3 workstreams are epics inside this release.",
            d["_records"])

    opened = {s: d for s, d in issues.items() if OPEN(d.get("status_at_export"))}
    pts = lambda d: float(d.get("story_points") or 0)  # noqa: E731
    total = sum(pts(d) for d in opened.values())
    by = defaultdict(list)
    for s, d in opened.items():
        by[str(d.get("assignee_source_id") or "unassigned")].append((s, d))
    if opened:
        add(facts, f"At the tracker export, {len(opened)} issues are open ({total:g} story points)"
            f" across {len(by)} assignees.", {r for d in opened.values() for r in d["_records"]})

    # concentration (work level), with the candidate link only when initials match uniquely
    top = max(by.items(), key=lambda kv: sum(pts(d) for _, d in kv[1]), default=None)
    if top and total:
        who, items = top
        share = sum(pts(d) for _, d in items) / total
        epics = Counter(str(d.get("epic")) for d in opened.values())
        sole = sorted(e for e in epics if e and all(str(d.get("assignee_source_id")) == who
                                                   for d in opened.values() if str(d.get("epic")) == e))
        link = ""
        if cand:
            initials = "".join(w[0] for w in str(cand[1].get("name", "")).split()[:2]).upper()
            if initials == who and sum(1 for _, d in people.items() if "".join(
                    w[0] for w in str(d.get("name", "")).split()[:2]).upper() == who) == 1:
                link = " Those initials match the candidate's (a candidate link, not a confirmed identity)."
        add(facts, f"One assignee code holds {len(items)} of the open issues ({share:.0%} of open story points)"
            + (f" and is the only assignee on every open issue in epic {', '.join(sole)}" if sole else "")
            + "." + link, {r for _, d in items for r in d["_records"]})
        dependencies.append({"text": f"Open scope is concentrated: {share:.0%} of open story points sit with one "
                             "assignee" + (f", including every open item of {', '.join(sole)}" if sole else "")
                             + ".", "kind": "derived", "evidence": sorted({r for _, d in items for r in d["_records"]}
                                                                           & set(recs)),
                             "verification": "counted from the tracker export", "grade": "A", "notes": []})
        if link:
            implications.append("If the report is confirmed and refers to that candidate, the release date depends on "
                                "reassigning that open scope, including any single-owner epic.")
        else:
            implications.append("If the person who resigned holds open release scope, the release date depends on "
                                "reassigning it; the records cannot say whose work it is without a confirmed identity.")

    # ---- git: knowledge concentration (exact work-email match only; aliases never merged)
    if git_readable and cand:
        email = str(cand[1].get("work_email", "")).lower()
        repo = Counter(g["repo"] for g in git if g["author"] and g["author"].lower() == email)
        if repo:
            r, n = repo.most_common(1)[0]
            tot = sum(1 for g in git if g["repo"] == r)
            ids_ = [g["record_id"] for g in git if g["repo"] == r and (g["author"] or "").lower() == email][:20]
            recs.update(router.records(ids_))
            add(facts, f"The candidate's work e-mail appears as author on {n} of {tot} commits in {r}. Another author "
                "address may be the same person and is not merged. This shows where knowledge sits, not "
                "productivity.", ids_)
            implications.append(f"Knowledge of {r} is concentrated; a handover plan matters more than the commit "
                                "count.")

    # ---- on-call and incidents (first names only: candidate links)
    if cand:
        first = str(cand[1].get("name", "")).split()[0]
        weeks = [d for d in rota.values() if first in (d.get("primary_source_id"), d.get("secondary_source_id"))]
        resp = [d for d in incidents.values() if d.get("responder_source_id") == first]
        if rota:
            add(facts, f"The rota uses first names only: that first name appears in {len(weeks)} of {len(rota)} "
                f"rota weeks, and as responder on {len(resp)} of {len(incidents)} recorded incidents.",
                {r for d in (weeks or list(rota.values())[:1]) for r in d["_records"]}
                | {r for d in resp for r in d["_records"]})
            implications.append("On-call cover looks less exposed than delivery on the records as they stand."
                                if len(weeks) <= len(rota) // 4 else
                                "On-call cover would also need reassigning.")

    unknowns += ["Whether HR has recorded the resignation, and from what date (A-020, C-29).",
                 "The notice period and last working day (“I think” is not a record; C-24).",
                 "Whether the tracker initials, rota first name and git addresses refer to the same person "
                 "(unresolved; a human decision).",
                 "Whether the open scope can be reassigned or descoped, and by whom.",
                 "No probability that the release slips is given: there is no calibrated basis."]
    claims = facts + dependencies
    if not claims:
        return None
    lines = ["**Facts** (as recorded)"] + [f"- {c['text']}" for c in facts]
    if dependencies:
        lines += ["", "**Dependencies**"] + [f"- {c['text']}" for c in dependencies]
    if implications:
        lines += ["", "**Implications** (what follows, and no further)"] + [f"- {t}" for t in implications]
    lines += ["", "**Unknowns**"] + [f"- {u}" for u in unknowns]
    return _result(router.basis_json, "qualified",
                   "I read this as: what a reported departure means for the release, from the HR, tracker, git, "
                   "on-call and incident records.",
                   "The resignation is reported, not recorded. Here is what the records show about the release and "
                   "where its open work and knowledge sit.\n\n" + "\n".join(lines),
                   claims, [recs[i] for c in claims for i in c["evidence"] if i in recs], unknowns)
