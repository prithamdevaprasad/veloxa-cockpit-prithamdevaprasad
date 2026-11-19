"""Orbitra Labs: a SECOND, larger fictional company for conversation, Gemini and load evaluation (Phase 5).

Entirely invented and separate from the supplied company (D1-D4) and from Lumora: its own seed, prefixes ("OR"),
e-mail domain (orbitra.example), database (`veloxa_synthco[_x]`) and tenant (`orbitra`). Every row carries the
SYNTHETIC flag and every document says SYNTHETIC. It uses the same source types as Lumora, so it flows through
the real data room (upload, validate, review, authorise, admit, seal, project).

Scenarios built in (each recorded in `facts` so tests can assert against the generator, not against guesses):
- delivery delay: Orbit Core 3.0 moved from its original date;
- dependencies: issues blocked by a paused requisition and by a late vendor delivery;
- resignations: employees on notice and exited (HR), plus one resignation REPORTED in leadership notes only;
- financial disagreement: the Q3 board update reports an ARR figure that differs from the revenue export;
- missed commitment: Pulse 1.5 committed for 30 Sep 2026 and released later;
- stale information: headcount snapshots stop in August while the directory is current;
- missing evidence: Project Lynx is named in leadership notes but has no issues, epics or release.
"""

from __future__ import annotations

import random
from pathlib import Path

from .generate import SOURCES, _docx, _pdf  # same source types and file helpers as Lumora

VERSION = "orbitra-synthetic/1"
SEED = 20261119
COMPANY = "Orbitra Labs"
DOMAIN = "orbitra.example"
MONTHS = [f"2026-{m:02d}" for m in range(1, 13)]
DEPARTMENTS = {"engineering": "Engineering", "finance": "Finance", "people": "People Operations",
               "marketing": "Marketing", "sales": "Sales", "operations": "Operations", "leadership": "Leadership"}
PER = {"engineering": 60, "finance": 15, "people": 15, "marketing": 20, "sales": 35, "operations": 20,
       "leadership": 15}                                                   # 180 employees
PROJECTS = ["Orbit Core", "Pulse", "Vega", "Lattice", "Comet"]
FIRST = ["Aru", "Bexa", "Cyan", "Dario", "Esme", "Feliks", "Galen", "Hesta", "Ilka", "Joren", "Kiri", "Lusa", "Marek",
         "Nyla", "Orrin", "Peri", "Quilla", "Rosk", "Sefa", "Tamsin", "Ulric", "Veda", "Wren", "Xavi", "Yara", "Zeno"]
LAST = ["Ashgrove", "Bellmont", "Corvane", "Daleth", "Everly", "Farrow", "Glenmar", "Harlow", "Ivers", "Juniper"]


def _sheet(path: Path, title: str, header: list[str], rows: list[list]) -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append([f"{COMPANY} (SYNTHETIC, {VERSION}): {title}"])
    ws.append([])
    ws.append(header)
    for r in rows:
        ws.append(r)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def generate(out: Path) -> dict:
    rnd = random.Random(SEED)
    out, S, facts = Path(out), "SYNTHETIC", {}

    # ---- people: 180, heads report to the CEO; resignations recorded in HR
    people, heads, n = [], {}, 1
    for d in ["leadership", *[x for x in DEPARTMENTS if x != "leadership"]]:      # the CEO heads Leadership
        for i in range(PER[d]):
            emp = f"OR{n:03d}"
            name = f"{FIRST[(n * 5) % len(FIRST)]} {LAST[(n * 3 + n // len(FIRST)) % len(LAST)]}"
            title = ("Head of " + DEPARTMENTS[d]) if i == 0 else rnd.choice(
                ["Analyst", "Specialist", "Lead", "Manager", "Engineer", "Associate", "Senior Engineer"]
                if d == "engineering" else ["Analyst", "Specialist", "Lead", "Manager", "Associate"])
            if i == 0:
                heads[d] = emp
            join = f"20{rnd.randint(18, 25)}-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}"
            people.append([emp, name, DEPARTMENTS[d], title, None if i == 0 else heads[d], join, "Active", None,
                           f"{name.split()[0].lower()}.{emp.lower()}@{DOMAIN}"])
            n += 1
    people[0][3] = "Chief Executive Officer"
    for p in people[1:]:
        if p[0] in heads.values() or p[2] == "Leadership":
            p[4] = people[0][0]
    notice = [people[i] for i in (7, 70, 120)]                 # resigned, serving notice
    for p, exit_ in zip(notice, ("2027-01-15", "2026-12-31", "2027-02-28"), strict=True):
        p[6], p[7] = "Notice period", exit_
    exited = [people[i] for i in (33, 150)]
    for p, exit_ in zip(exited, ("2026-08-31", "2026-09-30"), strict=True):
        p[6], p[7] = "Exited", exit_
    _sheet(out / "hr_directory" / "Employee_Directory.xlsx", "Employee directory as at 31 Oct 2026 (no compensation)",
           ["Emp ID", "Full Name", "Department", "Title", "Manager ID", "Join Date", "Status", "Exit Date",
            "Work Email"], people)
    facts.update(headcount_total=len(people), headcount_active=sum(p[6] == "Active" for p in people),
                 on_notice=len(notice), exited=len(exited),
                 headcount_by_department={DEPARTMENTS[d]: PER[d] for d in DEPARTMENTS}, ceo=people[0][1],
                 engineering_head=next(p[1] for p in people if p[0] == heads["engineering"]))

    # ---- stale: monthly headcount snapshots stop in August
    snaps = [[f"HC-{m}-{d}", m, DEPARTMENTS[d], max(1, PER[d] - 6 + mi // 2), S, VERSION]
             for mi, m in enumerate(MONTHS[:8]) for d in DEPARTMENTS]
    _sheet(out / "headcount_snapshots" / "Headcount_Snapshots.xlsx", "Monthly headcount (last snapshot: Aug 2026)",
           ["Snapshot ID", "Month", "Department", "Headcount", "Synthetic", "Generator"], snaps)
    facts["last_headcount_snapshot"] = MONTHS[7]

    # ---- hiring: one paused backend requisition blocks platform work
    reqs = []
    for i in range(1, 31):
        d = list(DEPARTMENTS)[i % 7]
        status = ["Open", "Filled", "On hold"][i % 3]
        reqs.append([f"ORQ-{i:03d}", DEPARTMENTS[d], f"{rnd.choice(['Senior', 'Junior', ''])} Role {i}".strip(),
                     MONTHS[i % 12], status, "Q4 hiring pause (decision OD-04)" if status == "On hold" else "", S])
    reqs[0] = ["ORQ-001", "Engineering", "Senior Backend Engineer", "2026-07", "On hold",
               "Q4 hiring pause (decision OD-04); needed for Orbit Core 3.0 sync service", S]
    _sheet(out / "hiring" / "Hiring_Requisitions.xlsx", "Hiring requisitions (no candidate data)",
           ["Requisition", "Department", "Role", "Opened", "Status", "Note", "Synthetic"], reqs)
    facts["requisitions_on_hold"] = sum(r[4] == "On hold" for r in reqs)

    eng = [[f"ENG-{q}-{d}", q, DEPARTMENTS[d], PER[d], round(3.2 + rnd.random() * 1.4, 1), S]
           for q in ("2026-Q2", "2026-Q3") for d in DEPARTMENTS]
    _sheet(out / "engagement" / "Team_Engagement.xlsx", "Team engagement, team level only (no comments)",
           ["Record ID", "Quarter", "Team", "Team size", "Engagement (1-5)", "Synthetic"], eng)
    leave = [[f"LV-{m}-{d}", m, DEPARTMENTS[d], rnd.randint(4, 40), S] for m in MONTHS[:10] for d in DEPARTMENTS]
    _sheet(out / "leave_summary" / "Leave_Summary.xlsx", "Leave days, department-month aggregates only",
           ["Record ID", "Month", "Department", "Leave days", "Synthetic"], leave)

    # ---- finance; the Q3 board update reports a different ARR (financial disagreement)
    vendors = [f"{w} {s}" for w, s in zip(["Alder", "Basalt", "Cinder", "Delta", "Ember", "Flint", "Gale", "Heron",
                                           "Indigo", "Juno", "Kelp", "Lyric", "Moss", "Nimbus", "Onyx", "Pike",
                                           "Quartzite", "Reed", "Slate", "Thorn"],
                                          ["Cloud", "Labs", "Logistics", "Media", "Supply"] * 4, strict=True)]
    revenue, arr, customers = [], 8_400_000, 140
    for m in MONTHS:
        arr += rnd.randint(250_000, 520_000) - rnd.randint(40_000, 160_000)
        customers += rnd.randint(2, 8)
        revenue.append([m, arr, arr // 12, customers, S])
    _sheet(out / "revenue" / "Revenue_Monthly.xlsx", "Monthly revenue (actuals, USD)",
           ["Month", "ARR", "MRR", "Customers", "Synthetic"], revenue)
    sep_arr = revenue[8][1]
    board_q3_arr = round(sep_arr * 1.06, -5)                    # the board reports a higher, rounded figure
    facts.update(arr_sep_export=sep_arr, arr_sep_board=int(board_q3_arr), arr_dec=revenue[-1][1])

    gl, bva, voucher = [], [], 1
    for m in MONTHS:
        for d in DEPARTMENTS:
            budget = 90_000 + 9_000 * PER[d] // 3
            actual = budget + rnd.randint(-15_000, 25_000)
            bva.append([f"BVA-{m}-{d}", m, DEPARTMENTS[d], budget, actual, actual - budget, S])
        for _ in range(5):
            v = rnd.choice(vendors)
            amt = rnd.randint(5_000, 60_000)
            gl.append([f"{m}-15", f"OV-{voucher:04d}", v, f"CC-{rnd.choice(['ENG', 'OPS', 'MKT', 'FIN'])}", amt,
                       f"{v} services, {m} (synthetic)"])
            voucher += 1
    _sheet(out / "budget_actuals" / "Budget_vs_Actuals.xlsx", "Budget versus actuals (USD)",
           ["Line ID", "Month", "Department", "Budget", "Actual", "Variance", "Synthetic"], bva)
    _sheet(out / "general_ledger" / "General_Ledger.xlsx", "General ledger extract",
           ["Txn Date", "Voucher", "Vendor / Payee", "Cost Centre", "Amount (Rs.)", "Narration"], gl)
    facts["ledger_total"] = sum(r[4] for r in gl)
    cash, bal = [], 21_000_000
    for m in MONTHS:
        burn = rnd.randint(700_000, 980_000)
        bal -= burn
        cash.append([m, bal, burn, round(bal / burn, 1), S])
    _sheet(out / "cash" / "Cash_and_Runway.xlsx", "Cash balance and runway (USD)",
           ["Month", "Cash balance", "Net burn", "Runway months", "Synthetic"], cash)
    facts["runway_dec"] = cash[-1][3]
    fc = [[q, arr + i * 400_000, "Pipeline-weighted forecast; not an actual", S]
          for i, q in enumerate(["2027-Q1", "2027-Q2", "2027-Q3", "2027-Q4"], start=1)]
    _sheet(out / "forecast" / "Revenue_Forecast.xlsx", "Revenue forecast (FORECAST, not actuals)",
           ["Quarter", "Forecast ARR", "Basis", "Synthetic"], fc)

    accounts = [f"Client {chr(65 + i // 26)}{chr(65 + i % 26)}" for i in range(120)]
    opps = [[f"OOP-{i:04d}", accounts[i % 120], rnd.choice(["Prospecting", "Qualified", "Proposal", "Negotiation",
                                                             "Won", "Lost"]), rnd.randint(10, 150) * 1000,
             rnd.choice(MONTHS), rnd.choice(PROJECTS), S] for i in range(1, 201)]
    _sheet(out / "pipeline" / "Sales_Pipeline.xlsx", "Sales pipeline (USD)",
           ["Opportunity", "Account", "Stage", "Amount", "Close month", "Product", "Synthetic"], opps)
    facts["won_count"] = sum(o[2] == "Won" for o in opps)
    ren = [[f"ORN-{i:03d}", accounts[i], rnd.choice(MONTHS[6:]), rnd.choice(["Renewed", "At risk", "Churned",
                                                                          "Pending"]), S] for i in range(50)]
    _sheet(out / "renewals" / "Renewals.xlsx", "Contract renewals", ["Renewal", "Account", "Due month", "Status",
                                                                    "Synthetic"], ren)
    camps = [[f"OCM-{i:02d}", f"{PROJECTS[i % 5]} launch wave {i}", rnd.choice(["Email", "Events", "Paid search",
                                                                               "Partners", "Content"]),
              rnd.randint(8, 90) * 1000, rnd.randint(60, 1200), rnd.randint(3, 90), rnd.randint(30, 600) * 1000,
              f"{PROJECTS[i % 5]} 3.0 release" if i % 4 == 0 else "", S] for i in range(1, 25)]
    _sheet(out / "campaigns" / "Campaigns.xlsx", "Campaign performance (USD)",
           ["Campaign ID", "Campaign", "Channel", "Spend", "Leads", "Conversions", "Attributed pipeline",
            "Depends on", "Synthetic"], camps)

    # ---- engineering: delay, blocked work, missed commitment
    epics, issues, releases, incidents = [], [], [], []
    for pi, p in enumerate(PROJECTS):
        code = "".join(w[0] for w in p.split()).upper() + p.split()[0][1:3].upper()
        for e in range(3):
            epics.append([f"{code}-E{e + 1}", f"{p} {['platform', 'experience', 'reliability'][e]} work", p,
                          ["In progress", "Done", "To do"][(pi + e) % 3], f"{p} 3.0", S])
    releases = [["Orbit Core 3.0", "Orbit Core", "2027-01-31", "2026-11-30", "Planned (date moved)", S],
                ["Pulse 1.5", "Pulse", "2026-10-28", "2026-09-30", "Released late", S],
                ["Vega 3.0", "Vega", "2027-03-15", "2027-03-15", "Planned", S],
                ["Lattice 3.0", "Lattice", "2027-04-30", "2027-04-30", "Planned", S],
                ["Comet 3.0", "Comet", "2027-05-31", "2027-05-31", "Planned", S]]
    statuses = ["Done", "In progress", "To do", "Blocked"]
    for i in range(1, 401):
        ep = epics[i % 15]
        st = statuses[i % 4] if ep[2] == "Orbit Core" else statuses[i % 3]
        blocked_by = ("ORQ-001 (paused hiring)" if st == "Blocked" and i % 8 == 3 else
                      "Vendor delivery ODL-004" if st == "Blocked" else "")
        issues.append([f"ORB-{i:04d}", f"{ep[1]}: task {i}", ep[0], ep[2], st, rnd.choice([1, 2, 3, 5, 8]),
                       rnd.choice(MONTHS[6:]), blocked_by, S])
    for i in range(1, 19):
        ep = epics[(i * 2) % 15]
        incidents.append([f"OIN-{i:03d}", f"{ep[2]} service degradation {i}", rnd.choice(["P1", "P2", "P3"]),
                          rnd.choice(MONTHS[4:]), ep[0], "Resolved", S])
    _sheet(out / "epics" / "Epics.xlsx", "Epics", ["Epic Key", "Epic", "Project", "Status", "Target release",
                                                  "Synthetic"], epics)
    _sheet(out / "issues" / "Issues.xlsx", "Issues", ["Issue Key", "Summary", "Epic Key", "Project", "Status",
                                                     "Points", "Updated", "Blocked by", "Synthetic"], issues)
    _sheet(out / "releases" / "Releases.xlsx", "Release versions", ["Version", "Project", "Target date",
                                                                   "Original date", "Status", "Synthetic"], releases)
    _sheet(out / "incidents" / "Incidents.xlsx", "Incidents", ["Incident", "Title", "Severity", "Month",
                                                              "Affected epic", "Status", "Synthetic"], incidents)
    facts.update(issues_total=len(issues), epics_total=len(epics), blocked=sum(r[4] == "Blocked" for r in issues),
                 delayed_release="Orbit Core 3.0", delayed_from="2026-11-30", delayed_to="2027-01-31",
                 missed_commitment="Pulse 1.5", missed_committed="2026-09-30", missed_actual="2026-10-28",
                 incidents_total=len(incidents))

    vrows = [[f"OVN-{i + 1:03d}", v, v.split()[1], rnd.choice(MONTHS), rnd.randint(30, 500) * 1000, S]
             for i, v in enumerate(vendors)]
    _sheet(out / "vendors" / "Vendors.xlsx", "Vendor register (USD)", ["Vendor ID", "Vendor", "Category",
                                                                    "Contract renewal", "Annual value", "Synthetic"],
           vrows)
    deliv = [[f"ODL-{i:03d}", vendors[i % 20], rnd.choice(MONTHS), rnd.choice([0, 0, 0, 6, 14, 25]), "", S]
             for i in range(1, 31)]
    deliv[3] = ["ODL-004", "Delta Media", "2026-10", 32, "Orbit Core 3.0 release", S]
    _sheet(out / "deliveries" / "Deliveries.xlsx", "Vendor deliveries", ["Delivery", "Vendor", "Due month",
                                                                        "Delay days", "Affects", "Synthetic"], deliv)

    # ---- leadership: board updates and notes (reported resignation, missing-evidence project)
    for qi, (period, sent, mi) in enumerate([("Apr to Jun 2026", "10 Jul 2026", 5),
                                             ("Jul to Sep 2026", "9 Oct 2026", 8)]):
        reported = revenue[mi][1] if qi == 0 else int(board_q3_arr)
        _pdf(out / "board_update" / f"Board_Update_Q{qi + 2}_2026.pdf",
             [f"{COMPANY}: Board update (SYNTHETIC)", f"Quarterly update. Period: {period}. Sent {sent}.",
              "Key numbers", "Metric Value", f"ARR USD {reported:,}", f"Customers {revenue[mi][3]}",
              f"Runway {cash[mi][3]} months", "Product",
              "Orbit Core 3.0 is due on 30 November 2026." if qi == 0 else
              "Orbit Core 3.0 is now due on 31 January 2027 because a vendor delivery slipped.",
              "Pulse 1.5 is due on 30 September 2026." if qi == 0 else "Pulse 1.5 shipped on 28 October 2026."])
    _docx(out / "leadership_notes" / "Leadership_Notes_H2_2026.docx", [
        f"{COMPANY} leadership notes (SYNTHETIC, {VERSION})",
        "Commitment: Pulse 1.5 is due on 30 September 2026.",
        "Commitment: Orbit Core 3.0 is due on 30 November 2026.",
        "Decision OD-04 (September 2026): a Q4 hiring pause, including the senior backend requisition ORQ-001.",
        "Dependency: Orbit Core 3.0 depends on the ORQ-001 hire and on vendor delivery ODL-004.",
        "Note (October 2026): a senior platform engineer told their manager they intend to resign; HR has not "
        "recorded it.",
        "Project Lynx: exploratory; no plan, tracker or budget has been set up yet.",
    ])
    facts.update(reported_resignation="senior platform engineer (not recorded by HR)", no_evidence_project="Lynx",
                 decision_pause="OD-04")
    return {"version": VERSION, "seed": SEED, "sources": SOURCES, "facts": facts, "heads": heads,
            "people": [p[0] for p in people], "company": COMPANY, "domain": DOMAIN, "departments": DEPARTMENTS}


if __name__ == "__main__":
    import json
    import sys

    m = generate(Path(sys.argv[1]))
    print(json.dumps({"version": m["version"], "facts": m["facts"]}, indent=1, default=str))
