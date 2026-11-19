"""Conversational replies for agent interviews, built ONLY from verified claims, the session's own history and the
declared policy. Deterministic: no model, no new facts.

The verified claims (each citing its records) stay the authority and are shown as the answer's sources. This module
decides which of them answer the question and joins them into plain sentences; it also answers questions about the
conversation itself ("what did I ask earlier?", "say that more simply", "what supports that?") from the stored turns,
and says plainly what the agent does not know. Persona details are only ever offered with an explicit label that
they are invented for the simulation.
"""

from __future__ import annotations

import re

INTENTS: tuple[tuple[str, str], ...] = (       # order matters: the first match wins
    ("greeting", r"^\s*(hi|hello|hey|hiya|good (morning|afternoon|evening)|namaste|greetings)\b[\s!.,]*"
                 r"(there|again|team)?[\s!.,]*$"),
    ("thanks", r"^\s*(thanks?( you)?|thank you( so much)?|cheers|great,? thanks?|ok(ay)?,? thanks?)[\s!.,]*$"),
    ("help", r"^\s*(help|what can you do|how (do|can) (i|you) (use|help)|what can i ask( you)?)\b"),
    ("more", r"^\s*(tell me more|more( detail(s)?)?|go on|continue|and\?|elaborate|expand( on that)?|"
             r"what else)\b"),
    ("updates", r"\b(any (updates?|news)|what'?s new|latest|what'?s (going on|happening)|project status|"
                r"status of (the )?(project|work|release)|how is (the )?(project|work|release) going|"
                r"update me|catch me up|progress)\b"),
    ("blockers", r"\b(blockers?|blocked|blocking|what'?s holding|holding (us|you|it) (up|back)|impediments?|"
                 r"dependenc(y|ies)|at risk)\b"),
    ("memory", r"\b(what did i (just )?ask|earlier|previous(ly)?|before that|asked you)\b"),
    ("evidence", r"\b(evidence|sources?|support(s|ed)?|how do you know|cite|proof|where does that come from)\b"),
    ("simpler", r"\b(simpler|simply|plain (english|terms|language)|non[- ]technical|layman|explain (that|it)|"
                r"in other words|eli5)\b"),
    ("unknowns", r"\b(what (do|don't) you not know|what don't you know|what can'?t you|limits?|gaps?|"
                 r"don't you know)\b"),
    ("personal", r"\b(hobb(y|ies)|weekend|free time|family|personal life|personality|interests?|outside work|"
                 r"live|lunch|coffee)\b"),
    ("manager", r"\b(report(s|ing)? to|manager|boss|line manager|supervis\w*)\b"),
    ("joined", r"\b(when did you (join|start)|how long|start(ed)? (date|here)|join(ed)?)\b"),
    ("status", r"\b(status|still (here|employed|working)|leaving|left|notice)\b"),
    ("about", r"\b(about yourself|introduce|who are you|tell me (a little |a bit )?about you)\b"),
    ("role", r"\b(role|what do you do|job|title|position|responsib\w*|work as)\b"),
    ("workload", r"\b(workload|busy|tasks?|working on|current(ly)? (project|work)|projects?)\b"),
    ("challenge", r"\b(challenges?|blockers?|problems?|struggl\w*|difficult\w*|worr\w*)\b"),
    ("team", r"\b(team|department|other departments|connect\w*|work with)\b"),
)
SELF_FACTS = {"about": ["self.identity", "self.title", "self.department", "self.manager", "self.join_date"],
              "role": ["self.title", "self.department"], "manager": ["self.manager"],
              "joined": ["self.join_date"], "status": ["self.status", "self.exit_date"],
              "team": ["self.department", "self.manager"]}
TOPIC = {"updates": "recent updates for my department", "blockers": "anything blocking the work",
         "workload": "my current workload or open tasks", "challenge": "what I'm finding hard right now",
         "team": "what my team is working on", "role": "my role", "manager": "my reporting line",
         "joined": "when I joined", "status": "my employment status", "about": "my background"}
SMALL_TALK = ("greeting", "thanks", "help")
HELP = ("You can ask me \u201cAny updates?\u201d, \u201cWhat's blocking the work?\u201d, "
        "\u201cWhat is your role?\u201d, "
        "\u201cWho do you report to?\u201d, \u201cTell me more\u201d or \u201cShow evidence\u201d. I answer only "
        "from approved company records, and I'll say so when the records don't cover something.")
UNKNOWN_LINES = [
    "My records cover my job title, department, reporting line, start date and recorded employment status.",
    "They don't tell me my current workload, how I'm feeling, how I'm performing, or anything private.",
    "Task and rota names that can't be matched to me exactly by employee ID or work e-mail stay unlinked, so I won't "
    "claim them.",
    "I can't see pay, leave details or private messages, and I won't guess at them.",
]


NO_RECORD = {"workload", "challenge", "other"}     # self facts never "answer" these: say plainly that we don't know
SYSTEMS = {"keka_hrms": "the HR export (Keka)", "keka_hire": "the hiring tracker", "zoho_books": "the general ledger",
           "jira": "the Jira export", "git": "the git history", "board_reporting": "the board updates",
           "incident_log": "the incident log", "oncall_rota": "the on-call rota"}
PLAIN_REFUSAL = {
    "POL-PAY-01": "Individual pay is confidential, so I can't share anyone's salary or pay details.",
    "POL-COMMS-01": "Private messages and e-mails are off limits, so I can't read or share them.",
    "POL-PROD-01": "I don't rank or judge individual people's productivity or performance.",
    "POL-ACT-01": "I can only talk; I can't approve, send or change anything.",
}


def intent(message: str) -> str:
    m = message.lower()
    for name, rx in INTENTS:
        if re.search(rx, m):
            return name
    return "other"


LEADS = ("From my department's records (not something I can confirm I saw): ",
         "In my role I'd plausibly have been aware of this: ",
         "I'd have had access to this record (not that I read it): ")


def _sentences(claims: list[dict]) -> str:
    """Join claim texts; a hedge shared by every claim is said once, at the start (the claims keep it)."""
    texts = [c["text"] for c in claims]
    lead = next((h for h in LEADS if texts and all(t.startswith(h) for t in texts)), "")
    parts = [(t[len(lead):] if lead else t).rstrip(".") for t in texts]
    body = " ".join((p[:1].upper() + p[1:] if lead else p) + "." for p in parts if p)
    return (lead.rstrip(": ") + ". " + body) if lead and body else body


def self_intents(message: str) -> list[str]:
    """Every self-profile intent a question asks for ("your role and who you report to" asks for two)."""
    m = message.lower()
    found = {name for name, rx in INTENTS if name in SELF_FACTS and re.search(rx, m)}
    return [k for k in SELF_FACTS if k in found]             # natural order: about, role, manager, joined, ...


def select(kind: str, claims: list[dict], message: str = "") -> list[dict] | None:
    """The claims that actually answer the question: only the relevant self facts for a self question (all the self
    intents it asks for, in order, without repeats); none when the only facts are self facts but the question is
    about something an HR record cannot tell (workload, challenges)."""
    kinds = self_intents(message) or ([kind] if kind in SELF_FACTS else [])
    if kinds:
        order = list(dict.fromkeys(f for k in (["about"] if "about" in kinds else kinds) for f in SELF_FACTS[k]))
        wanted = [c for f in order for c in claims if c.get("fact_id") == f]
        return wanted or None
    if kind in NO_RECORD and claims and all(str(c.get("fact_id", "")).startswith("self.") for c in claims):
        return []
    return None


def reply(kind: str, claims: list[dict], status: str) -> str:
    """The reply for a turn that used tools. Only verified claim texts appear (plus fixed connective phrasing)."""
    if kind in SELF_FACTS and claims:
        return _sentences(claims)
    if claims:
        return _sentences(claims[:4]) + (" There's more in the sources below." if len(claims) > 4 else "")
    topic = TOPIC.get(kind, "that")
    if kind == "blockers":
        return ("My department's records don't show a moved date, paused hiring, incident or open work that I could "
                "point to as a constraint, and nothing is marked as a blocker. I'd rather not guess.")
    return (f"I don't have a record that tells me {topic}, so I'd rather not guess. "
            "I can tell you my role, my department, who I report to and when I joined.")


def history_reply(kind: str, history: list[dict], persona: list[dict] | None = None) -> tuple[str, list[str]] | None:
    """Questions answered from the session itself. Returns (reply, evidence record IDs) or None."""
    prev = [h for h in history if h.get("message")]
    if kind == "memory":
        if not prev:
            return "This is the start of our conversation, so you haven't asked me anything earlier.", []
        asked = [f"“{h['message']}”" for h in prev[-3:]]
        lead = ("Earlier you asked " if len(prev) == 1 else
                f"You've asked me {len(prev)} questions so far. The most recent were: ")
        return lead + "; ".join(asked) + ".", []
    if kind == "evidence":
        last = next((h for h in reversed(prev) if h.get("evidence")), None)
        if last is None:
            return "I haven't told you anything that needed a record yet, so there's nothing to cite.", []
        ids = [e["record_id"] for e in last["evidence"]]
        systems = sorted({SYSTEMS.get(e.get("system", ""), e.get("system", "").replace("_", " "))
                          for e in last["evidence"]} - {""})
        where = " and ".join(systems) or "the company records"
        return (f"That came from {len(ids)} record{'s' if len(ids) != 1 else ''} in {where}. They're listed below, "
                "and you can open each one."), ids
    if kind == "simpler":
        last = next((h for h in reversed(prev) if h.get("reply")), None)
        if last is None:
            return "There's nothing to simplify yet. Ask me about my role and I'll keep it short.", []
        parts = [x for x in re.split(r"(?<=[.!?])\s+", last["reply"].strip()) if not x.startswith("I'm the AI")]
        return f"In short: {(parts or [last['reply']])[0]}", [e["record_id"] for e in last.get("evidence", [])]
    if kind == "unknowns":
        return " ".join(UNKNOWN_LINES), []
    if kind == "more":
        last = next((h for h in reversed(prev) if h.get("claims")), None)
        if last is None:
            return "There's nothing to expand on yet. Ask me for any updates first.", []
        rest = last["claims"][4:8]
        if rest:
            return " ".join(c["text"].rstrip(".") + "." for c in rest), [r for c in rest for r in c["evidence"]]
        ids = [e["record_id"] for e in last.get("evidence", [])]
        unknown = [u for u in last.get("unknowns", []) if "fact(s) rest on" not in u][:2]
        return ("That's everything my records hold on that. " + (" ".join(u.rstrip(".") + "." for u in unknown)
                + " " if unknown else "") + f"It rests on {len(ids)} record{'s' if len(ids) != 1 else ''}; ask "
                "\u201cShow evidence\u201d to see them."), ids
    if kind == "personal":
        if not persona:
            return ("Nothing in the company records covers my personal life, and no persona details are set for me, "
                    "so I won't make any up."), []
        pick = [p for p in persona if p["category"] in ("hobbies", "interests")][:2] or persona[:1]
        detail = " and ".join(p["value"] for p in pick)
        return ("Nothing in the company records covers that. For this simulation my persona was given an invented "
                f"detail: {detail}. It's a fictional assumption, not a fact about the real employee."), []
    return None


def small_talk(kind: str, who: dict) -> str:
    """Greetings, thanks and help: answered without searching records (nothing here is a company fact)."""
    name = who.get("display_name")
    me = (f"the AI representation of {name}" + (f", {who['title']}" if who.get("title") else "")
          + (f" in {who['department']}" if who.get("department") else "")) if name else "an AI representation"
    if kind == "greeting":
        return (f"Hello. I'm {me}, built from approved company records; I'm not the person themselves. "
                "Ask me for any updates, or about my role.")
    if kind == "thanks":
        return "You're welcome. Ask me anything else about the work whenever you like."
    return f"I'm {me}. " + HELP


def refusal_reply(refusal: dict) -> str:
    alt = refusal.get("alternative")
    why = PLAIN_REFUSAL.get(refusal.get("policy")) or (
        str(refusal.get("reason") or "That's outside what I'm allowed to share").rstrip(".") + ".")
    return f"I can't help with that. {why}" + (f" Instead, you could {alt[0].lower() + alt[1:].rstrip('.')}."
                                               if alt else "")
