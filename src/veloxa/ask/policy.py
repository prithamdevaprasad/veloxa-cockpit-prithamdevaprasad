"""Refusal catalogue (ARCHITECTURE.md §13.6): policy in code, checked BEFORE any data is read (§13.1 stage 2).

Each policy has an identifier, a reason and an alternative. Matching is deterministic (patterns plus a check for
names of people in the HR export); an optional classifier may later add a second signal, never replace this.
A refusal is a successful response with status "refused" (§19.1), not an error.

The patterns err on the side of refusing: a false refusal costs a rephrase, a false answer can expose a person.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Policy:
    policy_id: str
    title: str
    reason: str
    alternative: str


@dataclass(frozen=True)
class Refusal:
    policy: Policy
    matched: str  # which rule matched (a rule label, never question text)


POLICIES = {
    p.policy_id: p for p in (
        Policy("POL-PAY-01", "Individual compensation",
               "Individual pay is protected. Pay values are released only as audited aggregates, and the disclosure "
               "auditor that clears them is not available yet.",
               "Ask about ledger spend excluding payroll lines, or headcount by department."),
        Policy("POL-PEOPLE-01", "Ranking or predicting individuals",
               "Veloxa does not rank or predict individuals' attrition, performance or sentiment.",
               "Ask about continuity risk at the level of work: which projects or functions depend on few people, "
               "and what the records say about them."),
        Policy("POL-COMMS-01", "Monitoring private communications",
               "Private communications are used only as evidence for the people who were party to them, never to "
               "monitor someone.",
               "Ask a question about the work itself; messages you were party to are cited where they are evidence."),
        Policy("POL-SENS-01", "Reasons behind personal or sensitive records",
               "The reasons behind leave, health, resignation or other personal records are not disclosed or "
               "inferred.",
               "Ask what the record changes for the work: dates, ownership and handover, as recorded."),
        Policy("POL-PROD-01", "Activity-based productivity",
               "Commit counts, activity timing and similar signals are not used to judge people's productivity.",
               "Ask about delivery at the level of work items and milestones, with their evidence."),
        Policy("POL-ACT-01", "Actions in business systems",
               "Veloxa reads and explains; it takes no action in any business system.",
               "Ask for the evidence or a summary you can act on yourself."),
        Policy("POL-PAY-02", "Protected pay aggregates",
               "Payroll aggregates are released only after the disclosure auditor clears them. The auditor is not "
               "available yet, so the figure is withheld (fail closed).",
               "Ask about ledger spend excluding payroll lines, or headcount by department."),
    )
}

_PAY = r"\b(salar(y|ies)|pay(slip|check|cheque)?|paid|ctc|compensation|wages?|gross|net pay|earn\w*|" \
       r"bonus(es)?|increments?|hikes?|appraisal amount)\b"
_INDIVIDUAL = r"\b(his|her|their|each|every|individual|per (person|employee|head)|who|whose|which (employee|person)|" \
              r"highest|lowest|top earners?|best paid|worst paid)\b"
# A role title names ONE person as surely as a name does ("how much does the CTO earn").
_ROLE = r"\b(ceo|cto|cfo|coo|cpo|chro|cmo|founder|co-?founder|vp|vice president|head of \w+|director)\b"
_BUSINESS_PAYEE = r"\b(vendors?|suppliers?|payees?|invoices?|ledger|cost cent(re|er)s?|contracts?)\b"
_ATTRITION = r"\b(attrition|flight risk|churn|quit\w*|resign\w*|leav(e|ing) (the company|veloxa|us))\b"
_JUDGEMENT = (r"\b(under-?perform\w*|(low|high|top|poor|weak|best|worst) performers?|"
              r"performance (ratings?|reviews?|scores?|rankings?)|perform(s|ing)? (badly|poorly|well|best|worst)|"
              r"sentiment|unhappy|disengaged|morale|burn(ed|t)? ?out|(de)?motivated)\b")
_PREDICT = (r"\b(predict\w*|forecast\w*|likel(y|ihood)|probabilit\w+|chances?|(at )?risk of|going to|will|might|"
            r"may|could|rank\w*|scor(e|es|ing))\b")
_WHO = (r"\b(who|whom|whose|which (employees?|people|persons?|engineers?|staff|members?)|list (the )?"
        r"(people|employees|staff))\b")
_MONITOR = r"\b(monitor(ing)?|surveil(lance)?|spy|keep (an )?eye on|track(ing)?|snoop)\b"
_COMMS = r"\b(e-?mails?|messages?|inbox(es)?|chats?|slack|dms?|communications?|whatsapp|texts?)\b"
_READ = r"\b(read|summari[sz]e|show( me)?|open|go through|forward|dump|export)\b"
_WHY = r"\b(why|reason|reasons|cause|what made|what led)\b"
_SENSITIVE = r"\b(leave|sick|medical|health|illness|pregnan\w*|maternity|paternity|absen(ce|t)|resign(ed|ation)?|" \
             r"quit|exit(ed)?|left|fired|terminat\w+|disciplinary|pip|warning|personal reasons?)\b"
_PRODUCTIVITY = r"\b(commit(s)? (count|counts|per|by)|number of commits|how many commits|" \
                r"commit(s|ted)? (the )?(most|least|fewest)|(most|least|more|less|fewest) (code|commits)|" \
                r"(most|least|fewest) commits|lines of code|keystrokes?|" \
                r"log-?in (times?|hours?)|online (hours?|times?)|active hours|hours (online|logged)|" \
                r"(most|least) (productive|active)|productivity (score|ranking|of)|works? (the )?(most|least|late))\b"
_ACTION = r"^\s*(please\s+|can you\s+|could you\s+|would you\s+|go ahead and\s+)*(send|approve|reject|create|update|" \
          r"delete|remove|pay|transfer|schedule|fire|terminate|post|book|raise|file|submit|assign|close|reopen|" \
          r"cancel|e-?mail|message|notify|reply|forward|hire|promote|demote|change|set|edit)\b"


def _has(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.IGNORECASE) is not None


def _names_in(text: str, people: list[str]) -> bool:
    low = text.lower()
    for full in people:
        parts = [full.lower()] + [p.lower() for p in full.split()[:1] if len(p) > 2]
        if any(re.search(rf"\b{re.escape(p)}\b", low) for p in parts):
            return True
    return False


def prescreen(question: str, people: list[str]) -> Refusal | None:
    """Return the first matching refusal, or None. `people` are the full names AND employee IDs from the HR
    export at the latest sealed batch (used only to recognise that a question targets an individual; nothing is
    disclosed). Either form names one person, so "what is the salary of <employee ID>" is refused like a name."""
    q = question.strip()
    names = _names_in(q, people)
    if _has(_ACTION, q):
        return Refusal(POLICIES["POL-ACT-01"], "imperative action verb")
    if _has(_PRODUCTIVITY, q):
        return Refusal(POLICIES["POL-PROD-01"], "activity productivity")
    if _has(_PAY, q) and (names or _has(_ROLE, q) or (_has(_INDIVIDUAL, q) and not _has(_BUSINESS_PAYEE, q))):
        return Refusal(POLICIES["POL-PAY-01"], "individual compensation")
    if (_has(_ATTRITION, q) and _has(_PREDICT, q)) or (_has(_JUDGEMENT, q) and (names or _has(_WHO, q)
                                                                                  or _has(_PREDICT, q))):
        return Refusal(POLICIES["POL-PEOPLE-01"], "individual attrition, performance or sentiment")
    if _has(_COMMS, q) and (_has(_MONITOR, q) or (_has(_READ, q) and (names or _has(r"'s\b|\b(his|her|their)\b",
                                                                                     q)))):
        return Refusal(POLICIES["POL-COMMS-01"], "reading someone's communications")
    if _has(_WHY, q) and _has(_SENSITIVE, q) and (names or _has(r"\b(he|she|they|his|her|their|someone|employee)\b",
                                                                 q)):
        return Refusal(POLICIES["POL-SENS-01"], "reason for a personal record")
    return None
