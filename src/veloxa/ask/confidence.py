"""Deterministic confidence for verified claims, specialist routing, and answer-derived follow-up questions.

Confidence is NOT a probability and is never produced by a model. Per claim, from the existing evidence grade
(A-D: directness and source authority), corroboration, temporal validity and unresolved conflicts:

| Rule (first match wins)                                                     | Level        |
|-----------------------------------------------------------------------------|--------------|
| a cited record is part of an unresolved disagreement                       | conflicting  |
| grade D (no usable support)                                                 | unknown      |
| grade A (derived from or stated by a system of record)                      | high         |
| grade B and corroborated by records from >= 2 different source systems      | high         |
| grade B (formal document, first-person message)                             | moderate     |
| grade C (hearsay, summaries, unverified identity)                           | low          |

The answer's confidence is `conflicting` when any disagreement is shown, `unknown` when nothing was supported,
otherwise the LOWEST level among the shown claims (an answer is no stronger than its weakest shown claim).
A claim's confidence applies to that claim only, not to the whole topic.
"""

from __future__ import annotations

ORDER = ["unknown", "low", "moderate", "high"]
LABEL = {"high": "High", "moderate": "Moderate", "low": "Low", "unknown": "Unknown",
         "conflicting": "The sources disagree"}

# Source system -> the department whose specialists understand it (routing is automatic, never required).
SYSTEM_DEPARTMENT = {
    "jira": "engineering", "git": "engineering", "incident_log": "engineering", "oncall_rota": "engineering",
    "zoho_books": "finance", "board_reporting": "finance", "payroll_register": "finance",
    "keka_hrms": "people_ops", "hiring_tracker": "people_ops", "pulse_survey": "people_ops",
    "leave_register": "people_ops",
}


def claim_level(grade: str, systems: set[str], in_conflict: bool) -> tuple[str, str]:
    if in_conflict:
        return "conflicting", "a cited record is part of an unresolved source disagreement"
    if grade == "D":
        return "unknown", "no usable supporting evidence"
    if grade == "A":
        return "high", "stated by or derived from a system of record"
    if grade == "B" and len(systems) >= 2:
        return "high", f"formal sources from {len(systems)} different systems agree"
    if grade == "B":
        return "moderate", "a formal document or first-person statement, not independently corroborated"
    return "low", "an indirect source (a summary, hearsay or an unconfirmed identity)"


def assess(result: dict) -> dict:
    """Add per-claim and overall confidence to a composed answer (pure function on the answer dict)."""
    records = {e["record_id"]: e for e in result.get("evidence", [])}
    facts = {f["fact_id"]: f for f in result.get("facts", [])}
    disputed = {c["record_id"] for d in result.get("disagreements", []) for c in d.get("claims", [])}
    levels = []
    for c in result.get("claims", []):
        rids = {e for e in c["evidence"] if e in records} | {i for e in c["evidence"] for i in facts.get(e, {})
                                                            .get("inputs", [])}
        systems = {records[r]["system"] for r in rids if r in records}
        lvl, why = claim_level(c.get("grade", "D"), systems, bool(rids & disputed))
        c["confidence"] = {"level": lvl, "label": LABEL[lvl], "reason": why}
        levels.append(lvl)
    if result.get("status") == "refused":
        overall, why = "unknown", "refused by policy: no evidence was used"
    elif result.get("disagreements"):
        overall, why = "conflicting", "the sources disagree and no value is chosen"
    elif not levels:
        overall, why = "unknown", "nothing in the admitted evidence supports an answer"
    else:
        known = [x for x in levels if x in ORDER]
        overall = min(known, key=ORDER.index) if known else "conflicting"
        why = "the weakest shown claim sets the answer's confidence"
    result["confidence"] = {"level": overall, "label": LABEL[overall], "reason": why,
                            "method": "deterministic (grade, corroboration, conflicts); not a probability"}
    return result


def specialists(result: dict, agents: list[dict]) -> list[dict]:
    """Which grounded agents' areas the cited evidence touches (agents: {agent_id, category, display_name})."""
    depts: dict[str, set[str]] = {}
    for e in result.get("evidence", []):
        d = SYSTEM_DEPARTMENT.get(e["system"])
        if d:
            depts.setdefault(d, set()).add(e["system"].replace("_", " "))
    out = []
    for a in agents:
        if a["category"] in depts:
            out.append({"agent_id": a["agent_id"], "name": a.get("display_name"), "department": a["category"],
                        "reason": f"the answer cites {', '.join(sorted(depts[a['category']]))} records"})
    return out


def follow_ups(result: dict) -> list[str]:
    """Suggested next questions derived from THIS answer's content (never generic)."""
    out = []
    for d in result.get("disagreements", [])[:1]:
        out.append(f"Why do the sources disagree about {d['subject']}?")
    if any("not shown" in u for u in result.get("unknowns", [])):
        out.append("Show only the items that were not listed.")
    for f in result.get("facts", [])[:1]:
        if f.get("metric"):
            out.append(f"How has {f['metric'].split('@')[0].replace('_', ' ')} changed over time?")
    if result.get("status") == "abstained":
        out.append("What records would answer this?")
    if result.get("evidence"):
        first = result["evidence"][0]
        out.append(f"What else do the {first['system'].replace('_', ' ')} records say?")
    return list(dict.fromkeys(out))[:3]
