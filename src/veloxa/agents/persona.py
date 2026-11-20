"""Fictional persona details for the 14 agent people, every one logged in the Assumption Ledger (migration 0021).

The brief: "Where the data is silent (their lives, their private thoughts), invent it and log it as an assumption."

Rules:
- Deterministic: seeded by the agent id and the generator version, never by a person's name, gender, age or origin,
  so no detail is inferred from identity and nothing changes between runs.
- Every detail is an ASSUMPTION (`origin = synthetic_persona`, `scope = persona`). The database refuses any other
  origin or scope, and assumptions are never used as evidence for workload, attendance, performance, pay,
  promotion, employment decisions, risk scores, commitments or access (veloxa.agents.people enforces the split).
- Work facts (role, department, manager, assigned work) come only from admitted records, never from here.
"""

from __future__ import annotations

import hashlib
import random
import uuid

import psycopg

GENERATOR = "veloxa.agents.persona"
VERSION = "1"

OPTIONS: dict[str, list[str]] = {
    "interests": ["urban sketching", "long-distance cycling", "regional cinema", "chess problems", "birdwatching",
                  "home baking", "amateur astronomy", "podcasts on economic history", "board games",
                  "learning a third language", "trail running", "community gardening", "jazz records", "pottery"],
    "hobbies": ["weekend treks", "a book club that meets monthly", "restoring an old bicycle", "growing herbs on a "
                "balcony", "photographing street markets", "playing badminton on Thursdays", "cooking for friends",
                "writing short fiction", "volunteering at an animal shelter", "practising the guitar"],
    "background": ["studied at a (fictional) state university before joining a startup",
                   "moved cities twice for work before Veloxa", "started in a services firm and moved to product",
                   "took a year out to travel before this role", "first job after a master's programme",
                   "came from a larger company looking for more ownership"],
    "goals": ["wants to mentor a junior colleague this year", "hopes to lead a project end to end",
              "is working towards a professional certification", "wants a calmer release cadence",
              "would like to understand the business side better",
              "plans to take a long holiday after the next launch"],
    "communication_style": ["direct and brief; prefers written updates", "warm, asks questions before answering",
                            "careful and precise; checks numbers twice", "informal; thinks out loud",
                            "structured; likes agendas and follow-up notes", "quiet in meetings, detailed in writing"],
    "routine": ["starts early and protects mornings for focused work", "walks during long calls",
                "keeps Friday afternoons for tidying up loose ends", "reviews the week every Sunday evening",
                "takes a proper lunch break away from the desk", "does deep work after 4 pm"],
    "living_context": ["shares a flat with a friend", "lives with family", "lives alone with a cat",
                       "lives with a partner and a dog", "commutes by metro", "recently moved closer to the office"],
    "preferences": ["filter coffee over tea", "dark mode everywhere", "handwritten notes for planning",
                    "short meetings with clear owners", "headphones on for focus", "plain-text email"],
    "personality": ["steady under pressure", "curious and a little impatient", "dry sense of humour",
                    "thoughtful and conflict-averse", "energetic and optimistic", "sceptical until shown evidence"],
}
PER_AGENT = ("interests", "hobbies", "background", "goals", "communication_style", "routine", "living_context",
             "preferences", "personality")


def generate(agent_id: str) -> list[dict]:
    rng = random.Random(int(hashlib.sha256(f"{GENERATOR}/{VERSION}/{agent_id}".encode()).hexdigest(), 16))  # noqa: S311
    out = []
    for cat in PER_AGENT:
        val = rng.choice(OPTIONS[cat])
        aid = "AS-" + hashlib.sha256(f"{agent_id}|{cat}|{VERSION}".encode()).hexdigest()[:12]
        out.append({"assumption_id": aid, "agent_id": agent_id, "category": cat,
                    "description": f"Fictional {cat.replace('_', ' ')} for the agent's persona",
                    "value": val, "origin": "synthetic_persona",
                    "reason": "The records are silent on personal life; the brief asks for invented, logged details",
                    "generator": GENERATOR, "generator_version": VERSION, "status": "active", "scope": "persona"})
    return out


def record(conn: psycopg.Connection, tenant_id: uuid.UUID, agent_ids: list[str]) -> int:
    """Append each agent's persona assumptions once (idempotent by deterministic id)."""
    n = 0
    for a in agent_ids:
        for x in generate(a):
            n += conn.execute(
                "INSERT INTO runtime.assumption (tenant_id, assumption_id, agent_id, category, description, value,"
                " origin, reason, generator, generator_version, status, scope) VALUES"
                " (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (tenant_id, x["assumption_id"], a, x["category"], x["description"], x["value"], x["origin"],
                 x["reason"], x["generator"], x["generator_version"], x["status"], x["scope"])).rowcount
    return n
