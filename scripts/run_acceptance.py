"""Hackathon acceptance suite: 32 expected-answer cases over a SYNTHETIC four-day company. One command:

    python scripts/run_acceptance.py [--json out.json] [--md out.md]

- Builds the synthetic Day 1-4 folders (tests/synthetic_days.py) and a disposable PostgreSQL 18 database
  (`veloxa_test_acc_<hex>`, created and dropped by this run, like the pytest fixtures). Needs VELOXA_TEST_ADMIN_DSN.
  The real `veloxa` database, the Company Lab and the Demo Lab are never touched.
- Admits through the governed pipeline with SIMULATED release times (the clock is passed explicitly): D1-D3 at
  18 Nov 12:00 IST, a D4 attempt before its release (must be refused), then D4 at 19 Nov 12:00 IST.
- Exercises the real API (FastAPI TestClient, development fixture principal = the synthetic CEO) and the real
  modules. Checks structured facts and statuses, never prose. No model is called (DENY_ALL).
- Exit code 0 only if every case passes. Writes a JSON and a Markdown report.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests"), str(ROOT / "scripts")]
IST = "+05:30"
# The synthetic company has its OWN schedule, four days earlier than the real packs (12-15 Nov 2026), because the
# database refuses an admission recorded before its release time (real clock). Day N releases at 10:00 IST.
DAY = {1: "2026-11-12", 2: "2026-11-13", 3: "2026-11-14", 4: "2026-11-15"}
T = {k: datetime.fromisoformat(f"{DAY[d]}T{h}:00:00{IST}")
     for k, d, h in (("d3", 3, "12"), ("pre_d4", 4, "09"), ("d4", 4, "12"), ("api", 4, "15"))}
TOKEN = "acceptance-local-fixture-token-0123456789abcdef"  # noqa: S105  (local fixture for a disposable database)
CEO = "S000"
ROLE_PW = {"migrator": "local-dev-migrator", "app": "local-dev-app", "ingest": "local-dev-ingest",
           "projection": "local-dev-projection"}


def _dsn(admin: str, db: str, role: str | None = None) -> str:
    p = urlsplit(admin)
    netloc = p.netloc if role is None else f"veloxa_{role}:{ROLE_PW[role]}@{p.netloc.rsplit('@', 1)[-1]}"
    return urlunsplit((p.scheme, netloc, f"/{db}", p.query, p.fragment))


class Suite:
    def __init__(self):
        self.cases: list[dict] = []

    def case(self, cid, category, question, expected, fn, *, principal=CEO, scope="synthetic company",
             knowledge_date="current", confidence=None, citations=None, refusal=None, routing=None):
        t0 = time.perf_counter()
        try:
            ok, actual = fn()
            err = None
        except Exception as e:  # noqa: BLE001  (a crashing case is a failing case, reported)
            ok, actual, err = False, None, f"{type(e).__name__}: {e}"
        self.cases.append({"id": cid, "category": category, "question": question, "principal": principal,
                           "scope": scope, "knowledge_date": knowledge_date, "expected": expected,
                           "expected_confidence": confidence, "expected_citations": citations,
                           "expected_refusal_or_uncertainty": refusal, "expected_routing": routing,
                           "actual": actual, "error": err, "passed": bool(ok),
                           "ms": round((time.perf_counter() - t0) * 1000)})


def build(admin: str, db: str, data: Path, cfg: Path) -> dict:
    import psycopg
    from synthetic_days import make_days

    from veloxa import config, knowledge, projector
    from veloxa.agents import people, registry
    from veloxa.db import connect
    from veloxa.ingest.pipeline import admit_pack
    from veloxa.migrate import migrate
    from veloxa.releases import AdmissionRefused, load_authorisations, load_schedule

    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{db}"')
    for r, pw in ROLE_PW.items():
        os.environ.setdefault(f"VELOXA_{r.upper()}_PASSWORD", pw)
    migrate(_dsn(admin, db), _dsn(admin, db, "migrator"), db)
    make_days(data)
    schedule, auth = load_schedule(), load_authorisations()
    tid = config.tenant_uuid(schedule.tenant_slug)
    out = {"tid": tid, "admitted": {}, "d4_before_release": None}
    with connect(_dsn(admin, db, "ingest")) as conn:
        for pid in ("D1", "D2", "D3"):
            r = admit_pack(conn, tid, schedule, pid, data, T["d3"], authorisation=auth[pid])
            out["admitted"][pid] = (r.status, r.batch_seq, r.file_count, r.record_count)
        try:
            admit_pack(conn, tid, schedule, "D4", data, T["pre_d4"], authorisation=auth["D4"])
        except AdmissionRefused as e:
            out["d4_before_release"] = e.reason
        with connect(_dsn(admin, db, "projection")) as pc:
            projector.build(pc, tid)
        with connect(_dsn(admin, db)) as a:
            registry.seed_slots(a, tid)
            out["grounded_at_k3"] = people.ground(a, tid, 3)
            out["profile_k3_eng04"] = people.profile(a, tid, 3, "agent-eng-04")
        r = admit_pack(conn, tid, schedule, "D4", data, T["d4"], authorisation=auth["D4"])
        out["admitted"]["D4"] = (r.status, r.batch_seq, r.file_count, r.record_count)
    with connect(_dsn(admin, db, "projection")) as pc:
        projector.build(pc, tid)
        knowledge.backfill(pc, tid)
    return out


def run(admin: str) -> Suite:
    db = f"veloxa_test_acc_{uuid.uuid4().hex[:10]}"
    tmp = Path(tempfile.mkdtemp(prefix="veloxa-acc-"))
    cfg = tmp / "config"
    shutil.copytree(ROOT / "config", cfg)
    from synthetic_days import authorisations_toml

    (cfg / "admission_authorisations.toml").write_text(authorisations_toml(), encoding="utf-8")
    sched = (cfg / "release_schedule.toml").read_text(encoding="utf-8")
    for n, real in ((1, "2026-11-16"), (2, "2026-11-17"), (3, "2026-11-18"), (4, "2026-11-19")):
        sched = sched.replace(f'release_at = "{real}T', f'release_at = "{DAY[n]}T')
    (cfg / "release_schedule.toml").write_text(sched, encoding="utf-8")
    os.environ["VELOXA_CONFIG_DIR"] = str(cfg)
    s = Suite()
    try:
        built = build(admin, db, tmp / "data", cfg)
        cases(s, admin, db, tmp / "data", built)
    finally:
        import psycopg

        with psycopg.connect(admin, autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')      # this run's own disposable database
        shutil.rmtree(tmp, ignore_errors=True)
    return s


def cases(s: Suite, admin: str, db: str, data: Path, built: dict) -> None:  # noqa: C901, PLR0915
    from company_lab import inventory
    from fastapi.testclient import TestClient

    from veloxa import knowledge
    from veloxa.api import PRIVILEGED_ENV, create_app
    from veloxa.db import connect, tenant_tx
    from veloxa.releases import Authorisation
    from veloxa.risk import governance

    tid = built["tid"]
    for k in PRIVILEGED_ENV:
        os.environ.pop(k, None)
    os.environ.update({"VELOXA_DEV_AUTH": "1", "VELOXA_DEV_TOKEN": TOKEN, "VELOXA_DEV_PRINCIPAL": CEO,
                       "VELOXA_RATE_LIMIT_PER_MIN": "6000", "VELOXA_RATE_LIMIT_EXPENSIVE_PER_MIN": "6000"})
    client = TestClient(create_app(dsn=_dsn(admin, db, "app"), clock=lambda: T["api"]))
    client.__enter__()                                   # runs the app lifespan (startup checks)
    H = {"Authorization": f"Bearer {TOKEN}"}

    def ask(q, known=None):
        body = {"question": q} | ({"as_known_at": known} if known else {})
        return client.post("/v1/ask", json=body, headers=H).json()

    def sql(q, *a):
        with connect(_dsn(admin, db)) as c, tenant_tx(c, tid):
            return [dict(r) for r in c.execute(q, a).fetchall()]

    # ---- Day folder and transcript reconciliation (5)
    inv = inventory(data, T["d3"], {p: {"batch_seq": v[1], "admitted_at": "x"} for p, v in built["admitted"].items()
                                     if p != "D4"})
    s.case("R1", "reconciliation", "Inventory every Day folder file with hash, rule and release state",
           "4 Day folders inventoried; every file hashed and classified; D4 embargoed on Day 3",
           lambda: (len({i["pack"] for i in inv}) == 4
                    and all(len(i["sha256"]) == 64 and i["classification"] for i in inv)
                    and {i["status"] for i in inv if i["pack"] == "D4"} == {"embargoed"},
                    {"files": len(inv), "by_status": {st: sum(i["status"] == st for i in inv) for st in
                                                      {i["status"] for i in inv}}}))
    s.case("R2", "reconciliation", "Admit each Day folder only through source-specific authorisation (K-13)",
           "D1-D4 admitted as batches 1-4; a file without its own approval refuses the pack",
           lambda: (_k13(Authorisation) and [v[1] for v in built["admitted"].values()] == [1, 2, 3, 4],
                    built["admitted"]))
    reg = sql("SELECT subject, resolution, claim_ids FROM knowledge.conflict")
    s.case("R3", "reconciliation", "Reconcile the spoken ARR in the kickoff transcript with the board updates",
           "One registry entry for ARR, needs_review, citing both board updates and the transcript",
           lambda: (any(r["subject"] == "metric:arr" and r["resolution"] == "needs_review" and len(r["claim_ids"]) == 3
                        for r in reg), reg), confidence="conflicting", citations=["documents", "meeting_summaries"])
    s.case("R4", "reconciliation", "A corrected HR export (Day 4) supersedes rather than disagrees",
           "The S104 title has claims from two record versions and no conflict entry",
           lambda: (len({r["record_version"] for r in sql(
               "SELECT record_version FROM knowledge.claim WHERE subject = 'person:S104'"
               " AND predicate = 'title'")}) == 2
                    and not any(r["subject"] == "person:S104" for r in reg), "versions checked"))
    pulse = sql("SELECT valid_at, available_at, locator FROM knowledge.claim WHERE subject = 'team:Engineering'"
                " AND predicate = 'engagement_score'")
    s.case("R5", "reconciliation", "Two quarters of pulse scores are a series with exact locators and release time",
           "Two claims, different valid quarters, row locators, available_at = D2 release; no conflict",
           lambda: (len(pulse) == 2 and len({p["valid_at"] for p in pulse}) == 2
                    and all("row" in p["locator"] for p in pulse)
                    and all(p["available_at"].isoformat().startswith(f"{DAY[2]}T04:30") for p in pulse)
                    and not any(r["subject"].startswith("team:") for r in reg),
                    [str(p["valid_at"]) for p in pulse]))

    # ---- Agent grounding and consistency (5)
    ppl = client.get("/v1/people", headers=H).json()
    by = {p["agent_id"]: p for p in ppl.get("people", [])}
    s.case("A1", "agents", "Represent exactly 14 individual people across the four departments",
           "14 grounded agents, 14 distinct HR records, 5/3/3/3 by department",
           lambda: (ppl["count"] == 14 and len({p["person_record"] for p in by.values()}) == 14
                    and sorted(sum(1 for p in by.values() if p["department"] == d) for d in
                               ("Engineering", "Finance", "Marketing", "People & Ops")) == [3, 3, 3, 5],
                    {"count": ppl.get("count")}))
    e1 = by.get("agent-eng-01", {})
    s.case("A2", "agents", "What is agent-eng-01's role and who do they report to?",
           "VP Engineering, Engineering, reports to the CEO; every fact cites the HR record",
           lambda: (e1.get("title") == "VP Engineering" and any(f["fact"] == "Reports to" and "CEO" in f["value"]
                                                               for f in e1.get("verified_facts", []))
                    and all(f["record"].startswith("keka_hrms:") for f in e1.get("verified_facts", [])),
                    e1.get("verified_facts")), citations=["keka_hrms"])
    asum = sql("SELECT agent_id, scope, origin FROM runtime.assumption")
    s.case("A3", "agents", "Log every invented persona detail in the Assumption Ledger",
           "126 assumptions (9 per agent), all scope=persona and origin=synthetic_persona; labelled in profiles",
           lambda: (len(asum) == 126 and {(a["scope"], a["origin"]) for a in asum} == {("persona", "synthetic_persona")}
                    and all(a["label"].startswith("Assumption") for p in by.values() for a in p["assumptions"]),
                    {"assumptions": len(asum)}))
    s.case("A4", "agents", "Derive agent-eng-01's workload from evidence only",
           "Owns requisition R-1 (hiring record, by employee ID); open tasks unknown (None), not zero",
           lambda: (e1["workload"]["requisitions_owned"] == ["R-1"] and e1["workload"]["open_tasks"] is None,
                    e1.get("workload")))
    e4_now = by.get("agent-eng-04", {})
    s.case("A5", "agents", "Keep agent-eng-04 consistent across knowledge points",
           "Same person at K3 and K4; title changes only from the Day 4 correction",
           lambda: (built["profile_k3_eng04"]["person_record"] == e4_now.get("person_record")
                    and built["profile_k3_eng04"]["title"] == "Site Reliability Engineer"
                    and e4_now.get("title") == "Senior Site Reliability Engineer",
                    [built["profile_k3_eng04"]["title"], e4_now.get("title")]), knowledge_date="Day 3 vs current")

    # ---- CEO answers, citations and confidence (5)
    hc = ask("What is our headcount?")
    s.case("C1", "answers", "What is our headcount?",
           "answered; 'People in the HR export: 17' (the fixture has 17 people); "
           "cites keka_hrms",
           lambda: (hc["status"] in ("answered", "qualified") and any("People in the HR export: 17" in c["text"]
                                                                     for c in hc["claims"])
                    and all(e["system"] == "keka_hrms" for e in hc["evidence"]),
                    {"status": hc["status"], "claims": [c["text"] for c in hc["claims"]],
                     "systems": sorted({e["system"] for e in hc["evidence"]})}),
           citations=["keka_hrms"])
    s.case("C2", "answers", "What is our headcount? (confidence)", "High confidence: derived from the system of record",
           lambda: (hc["confidence"]["level"] == "high", hc.get("confidence")), confidence="high")
    board = ask("What did the board updates report?")
    s.case("C3", "answers", "What did the board updates report?", "Board figures cited to the two board documents",
           lambda: (board["status"] in ("answered", "qualified", "conflict") and board["evidence"]
                    and all(e["system"] in ("documents", "meeting_summaries") for e in board["evidence"]),
                    [c["text"] for c in board["claims"]][:3]), citations=["documents"])
    s.case("C4", "answers", "Who contributed to the headcount answer?",
           "People Operations specialists routed automatically",
           lambda: (bool(hc["specialists"]) and all(x["department"] == "people_ops" for x in hc["specialists"]),
                    [x["agent_id"] for x in hc["specialists"]]), routing="people_ops")
    arr = ask("What is the ARR?")
    s.case("C5", "answers", "What is the ARR? (follow-ups)",
           "Follow-up questions derived from the answer (the disagreement)",
           lambda: (any("disagree" in f for f in arr.get("follow_ups", [])), arr.get("follow_ups")))

    # ---- Ranked worry view (4)
    pending = client.get("/v1/worry", headers=H).json()
    s.case("W1", "worry", "What should I worry about? (before D-15 is recorded)",
           "ranking_mode=pending; nothing ranked; items listed unranked",
           lambda: (pending["ranking_mode"] == "pending" and pending["ranked"] == [] and pending["unranked"],
                    pending["ranking_mode"]))
    with connect(_dsn(admin, db)) as c:
        governance.record_decision(c, tid, ROOT / "tests" / "fixtures" / "d15_test_policy.toml")
    wr = client.get("/v1/worry", headers=H).json()
    s.case("W2", "worry", "What should I worry about?", "Weighted list; weights sum to 1; probabilities disabled",
           lambda: (wr["ranking_mode"] == "weighted" and abs(sum(wr["policy"]["weights"].values()) - 1) < 1e-9
                    and wr["policy"]["probabilities"] == "disabled" and wr["ranked"],
                    [(x["rank"], x["title"]) for x in wr["ranked"]]))
    s.case("W3", "worry", "Why is each item ranked where it is?",
           "Every ranked item shows C1-C4 status, weight, contribution, evidence and a reason; "
           "the Orion 2 slip has C1 = 30 days",
           lambda: (all(set(x["severity"]["criteria"]) == {"C1", "C2", "C3", "C4"} and x["evidence"] and x["why"]
                        for x in wr["ranked"])
                    and any(x["severity"]["criteria"]["C1"]["low"] == 30 for x in wr["ranked"]),
                    [x["why"] for x in wr["ranked"]][:2]))
    s.case("W4", "worry", "Are unsupported items kept out of the ranking?",
           "The notice-period item is unrankable with a reason (no approved measure)",
           lambda: (any(u["item_id"].startswith("notice:") and u["reasons"] for u in wr["unrankable"])
                    and not any(x["item_id"].startswith("notice:") for x in wr["ranked"]),
                    [u["title"] for u in wr["unrankable"]]), refusal="unsupported risk not ranked")

    # ---- Point-in-time answers (5)
    k17, k18, k19 = (ask("What is our headcount?", d)["read_basis"]["batch_seq"]
                     for d in (DAY[2], DAY[3], DAY[4]))
    s.case("P1", "point_in_time", "What did we know on Day 2, Day 3 and Day 4 (synthetic dates)?",
           "Knowledge points 2, 3 and 4 respectively", lambda: ((k17, k18, k19) == (2, 3, 4), (k17, k18, k19)),
           knowledge_date=f"{DAY[2]}/{DAY[3]}/{DAY[4]}")
    o17, o18 = ask("What do we know about Orion?", DAY[2]), ask("What do we know about Orion?", DAY[3])
    s.case("P2", "point_in_time", "What do we know about Orion? (Day 2 vs Day 3)",
           "Day 2: I don't know (Day 3 not yet released); Day 3: answered from Day 3 records",
           lambda: (o17["status"] == "abstained" and o18["status"] != "abstained", (o17["status"], o18["status"])),
           knowledge_date=f"{DAY[2]} / {DAY[3]}")
    z18, z19 = ask("What do we know about Zephyrine?", DAY[3]), ask("What do we know about Zephyrine?")
    s.case("P3", "point_in_time", "What do we know about Zephyrine? (only in the Day 4 voice note)",
           "Day 3: I don't know; current: answered citing the T6d note",
           lambda: (z18["status"] == "abstained" and z19["status"] != "abstained"
                    and any("T6d" in (e.get("source_path") or "") for e in z19["evidence"]),
                    (z18["status"], z19["status"])), knowledge_date=f"{DAY[3]} / current")
    s.case("P4", "point_in_time", "Can Day 4 be admitted before its release time?",
           "Refused not_yet_released; nothing opened", lambda: (built["d4_before_release"] == "not_yet_released",
                                                               built["d4_before_release"]))
    e4_18 = client.get("/v1/people/agent-eng-04", params={"as_known_at": DAY[3]}, headers=H).json()
    s.case("P5", "point_in_time", "What was agent-eng-04's title as known on Day 3, and now?",
           "Day 3: Site Reliability Engineer (what was known then); now: the corrected title",
           lambda: (e4_18.get("title") == "Site Reliability Engineer" and e4_now.get("title", "").startswith("Senior"),
                    (e4_18.get("title"), e4_now.get("title"))), knowledge_date=f"{DAY[3]} / current")

    # ---- Uncertainty and refusal (8)
    none = ask("What is our churn rate in Antarctica?")
    s.case("U1", "uncertainty", "What is our churn rate in Antarctica?", "I don't know (no supporting records)",
           lambda: (none["status"] == "abstained" and none["confidence"]["level"] == "unknown", none["status"]),
           confidence="unknown", refusal="I don't know")
    s.case("U2", "uncertainty", "What is the ARR?", "The sources disagree; no single figure; confidence conflicting",
           lambda: (arr["status"] == "conflict" and arr["answer"].startswith("The sources disagree")
                    and arr["confidence"]["level"] == "conflicting", arr["status"]),
           confidence="conflicting", refusal="The sources disagree")
    anon = TestClient(create_app(dsn=_dsn(admin, db, "app"), clock=lambda: T["api"]))
    anon.__enter__()
    r401 = anon.get("/v1/people")
    s.case("U3", "uncertainty", "(no credentials) List the people", "Refused (401) without disclosing any record",
           lambda: (r401.status_code == 401 and "Syn " not in r401.text, r401.status_code), principal="anonymous",
           refusal="refuse without disclosure")
    pay = ask("What is Syn Engineering 2's salary?")
    s.case("U4", "uncertainty", "What is Syn Engineering 2's salary?", "Refused by the payroll policy (POL-PAY-01)",
           lambda: (pay["status"] == "refused" and pay["refusal"]["policy"].startswith("POL-PAY"), pay.get("refusal")),
           refusal="payroll restriction")
    hob = ask("What are Syn Engineering 4's hobbies?")
    e4h = [a for a in e4_now.get("assumptions", []) if a["category"] == "hobbies"]
    s.case("U5", "uncertainty", "What are Syn Engineering 4's hobbies?",
           "The invented hobby never appears in the answer as a fact (Ask uses records only); the profile shows it "
           "labelled as an assumption",
           lambda: (e4h and e4h[0]["label"].startswith("Assumption") and e4h[0]["value"] not in hob["answer"]
                    and all(e4h[0]["value"] not in c["text"] for c in hob["claims"]),
                    {"ask_status": hob["status"], "assumption": e4h[:1]}), refusal="assumption, not a verified fact")
    s.case("U6", "uncertainty", "What do we know about Zephyrine, as known on Day 3?",
           "The Day 4 source is not used before its release: I don't know",
           lambda: (z18["status"] == "abstained" and not z18["evidence"], z18["status"]), knowledge_date=DAY[3],
           refusal="later source not used")
    e2 = by.get("agent-eng-02", {})
    s.case("U7", "uncertainty", "Which Jira tasks does agent-eng-02 own?",
           "Not attributed: Jira assignees are initials (K-04); workload unknown, said so; "
           "exact-email git link still works",
           lambda: (e2["workload"]["open_tasks"] is None and any("cannot be linked" in u for u in e2["unknowns"])
                    and [r["repo"] for r in e2["workload"]["repositories"]] == ["orion"],
                    {"open_tasks": e2["workload"]["open_tasks"], "repos": e2["workload"]["repositories"]}),
           refusal="ambiguous identity not merged")
    s.case("U8", "uncertainty", "Is the hiring pause ranked as an evidence-backed worry?",
           "No: listed as unrankable (no approved D-15 measure), with its reason",
           lambda: (any(u["item_id"].startswith("hiring:") for u in wr["unrankable"])
                    and not any(x["item_id"].startswith("hiring:") for x in wr["ranked"]), True),
           refusal="unsupported risk conclusion not ranked")
    del knowledge


def _k13(Authorisation) -> bool:
    from veloxa.releases import AdmissionRefused

    a = Authorisation("D9", False, "o", "2026-11-18", "x", ("data_room/hr/A.xlsx",))
    try:
        a.check_sources(["data_room/hr/A.xlsx", "data_room/hr/B.xlsx"])
    except AdmissionRefused as e:
        return e.reason == "source_not_authorised"
    return False


def report(s: Suite, json_path: Path, md_path: Path) -> int:
    passed = sum(c["passed"] for c in s.cases)
    cats: dict[str, list] = {}
    for c in s.cases:
        cats.setdefault(c["category"], []).append(c)
    json_path.write_text(json.dumps({"total": len(s.cases), "passed": passed, "cases": s.cases}, indent=2,
                                    default=str), encoding="utf-8")
    lines = ["# Hackathon acceptance report (synthetic four-day company)", "",
             f"**{passed} / {len(s.cases)} passed.** Deterministic fixtures; no model calls; disposable database.", "",
             "| Category | Passed |", "|---|---|"]
    lines += [f"| {k} | {sum(c['passed'] for c in v)} / {len(v)} |" for k, v in cats.items()]
    lines += ["", "| ID | Question | Expected | Result |", "|---|---|---|---|"]
    for c in s.cases:
        res = "PASS" if c["passed"] else "FAIL: " + str(c["error"] or c["actual"])[:120]
        lines.append(f"| {c['id']} | {c['question']} | {c['expected']} | {res} |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for c in s.cases:
        print(f"{'PASS' if c['passed'] else 'FAIL'} {c['id']:<3} {c['question'][:70]}"
              + ("" if c["passed"] else f"  -> {(c['error'] or c['actual'])}"))
    print(f"{passed}/{len(s.cases)} passed; reports: {json_path}, {md_path}")
    return 0 if passed == len(s.cases) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", default="acceptance_report.json")
    ap.add_argument("--md", default="acceptance_report.md")
    args = ap.parse_args()
    admin = os.environ.get("VELOXA_TEST_ADMIN_DSN")
    if not admin:
        raise SystemExit("VELOXA_TEST_ADMIN_DSN is required (a PostgreSQL 18 administrator DSN)")
    return report(run(admin), Path(args.json), Path(args.md))


if __name__ == "__main__":
    raise SystemExit(main())
