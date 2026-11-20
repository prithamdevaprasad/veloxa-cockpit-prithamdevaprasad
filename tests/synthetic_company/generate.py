"""Deterministic generator for the Veloxa Demo Lab (Profile B): a fictional company, "Lumora Systems".

**Everything here is invented.**
- No person, figure, document or name comes from the challenge material, and nothing is copied from a release
  pack.
- The output is reproducible: a fixed seed, and no clock.
- Every record carries a stable ID, the department, the source, a timestamp or period, `synthetic = yes` and
  the generator version, as columns or as document lines.

`generate(out_dir)` writes one folder per source type and returns the manifest (`SOURCES` plus the facts that
the expanded gold questions check). The files then go through the normal data room: upload, validation, review,
authorisation and admission. Nothing is inserted directly.

**Deliberate exclusions:**
- no salaries or other individual compensation;
- no individual resignation-risk or performance scores;
- leave only as department-month aggregates.
"""

from __future__ import annotations

import random
from pathlib import Path

VERSION = "lumora-demo/1"
SEED = 20261118
COMPANY = "Lumora Systems"
MONTHS = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09",
          "2026-10", "2026-11", "2026-12"]
DEPARTMENTS = {"engineering": "Engineering", "finance": "Finance", "people": "People Operations",
               "marketing": "Marketing", "sales": "Sales", "operations": "Operations", "leadership": "Leadership"}
PROJECTS = ["Nimbus", "Quartz", "Helix", "Juniper", "Kestrel"]
FIRST = ["Asha", "Bram", "Cleo", "Dev", "Elin", "Faro", "Gita", "Hugo", "Ines", "Jory", "Kavi", "Lena", "Milo",
         "Nora", "Omar", "Pia", "Quin", "Ravi", "Sana", "Teo"]
LAST = ["Arden", "Brook", "Calder", "Dunmore", "Ellery", "Fenwick", "Garrow"]

# (source_type, department, kind, system, data_class, records, glob, title, key columns, file name)
SOURCES = [
    ("hr_directory", "people", "export", "keka_hrms", "personal", "rows", "*.xlsx", "Employee directory",
     ["Emp ID"], "Employee_Directory.xlsx"),
    ("headcount_snapshots", "people", "export", "people_metrics", "internal", "rows", "*.xlsx",
     "Monthly headcount by department", ["Snapshot ID"], "Headcount_Snapshots.xlsx"),
    ("hiring", "people", "export", "recruiting", "internal", "rows", "*.xlsx", "Hiring requisitions",
     ["Requisition"], "Hiring_Requisitions.xlsx"),
    ("engagement", "people", "export", "engagement_survey", "internal", "rows", "*.xlsx",
     "Team engagement (teams of 5 or more)", ["Record ID"], "Team_Engagement.xlsx"),
    ("leave_summary", "people", "export", "leave_aggregates", "internal", "rows", "*.xlsx",
     "Leave days by department and month", ["Record ID"], "Leave_Summary.xlsx"),
    ("general_ledger", "finance", "export", "zoho_books", "confidential", "rows", "*.xlsx", "General ledger",
     ["Voucher"], "General_Ledger.xlsx"),
    ("revenue", "finance", "export", "revenue_reporting", "confidential", "rows", "*.xlsx", "Monthly revenue",
     ["Month"], "Revenue_Monthly.xlsx"),
    ("budget_actuals", "finance", "export", "fp_and_a", "confidential", "rows", "*.xlsx",
     "Budget versus actuals", ["Line ID"], "Budget_vs_Actuals.xlsx"),
    ("cash", "finance", "export", "treasury", "confidential", "rows", "*.xlsx", "Cash and runway", ["Month"],
     "Cash_and_Runway.xlsx"),
    ("forecast", "finance", "export", "fp_and_a_forecast", "confidential", "rows", "*.xlsx",
     "Revenue forecast (forecast, not actuals)", ["Quarter"], "Revenue_Forecast.xlsx"),
    ("pipeline", "sales", "export", "crm", "confidential", "rows", "*.xlsx", "Sales pipeline", ["Opportunity"],
     "Sales_Pipeline.xlsx"),
    ("renewals", "sales", "export", "crm_renewals", "confidential", "rows", "*.xlsx", "Renewals", ["Renewal"],
     "Renewals.xlsx"),
    ("campaigns", "marketing", "export", "marketing_ops", "internal", "rows", "*.xlsx", "Campaign performance",
     ["Campaign ID"], "Campaigns.xlsx"),
    ("epics", "engineering", "export", "issue_tracker_epics", "internal", "rows", "*.xlsx", "Epics",
     ["Epic Key"], "Epics.xlsx"),
    ("issues", "engineering", "export", "issue_tracker", "internal", "rows", "*.xlsx", "Issues", ["Issue Key"],
     "Issues.xlsx"),
    ("releases", "engineering", "export", "release_calendar", "internal", "rows", "*.xlsx", "Release versions",
     ["Version"], "Releases.xlsx"),
    ("incidents", "engineering", "export", "incident_log", "internal", "rows", "*.xlsx", "Incidents",
     ["Incident"], "Incidents.xlsx"),
    ("vendors", "operations", "export", "vendor_register", "confidential", "rows", "*.xlsx", "Vendor register",
     ["Vendor ID"], "Vendors.xlsx"),
    ("deliveries", "operations", "export", "vendor_deliveries", "internal", "rows", "*.xlsx", "Vendor deliveries",
     ["Delivery"], "Deliveries.xlsx"),
    ("board_update", "leadership", "document", "board_reporting", "confidential", "document", "*.pdf",
     "Board updates", [], None),
    ("leadership_notes", "leadership", "document", "leadership_docs", "confidential", "document", "*.docx",
     "OKRs, decisions and commitments", [], None),
]


def _sheet(path: Path, title: str, header: list[str], rows: list[list]) -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append([f"{COMPANY} (SYNTHETIC DEMO, {VERSION}): {title}"])
    ws.append([])
    ws.append(header)
    for r in rows:
        ws.append(r)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def _docx(path: Path, paragraphs: list[str]) -> None:
    import docx

    d = docx.Document()
    for p in paragraphs:
        d.add_paragraph(p)
    path.parent.mkdir(parents=True, exist_ok=True)
    d.save(str(path))


def _pdf(path: Path, lines: list[str]) -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from synthetic_pack import _pdf as pdf

    pdf(path, lines)


def generate(out: Path) -> dict:
    rnd = random.Random(SEED)
    out = Path(out)
    facts: dict = {}
    S = "SYNTHETIC"                                          # every row carries the synthetic flag

    # People: 60 employees across the departments; managers are department heads.
    depts = list(DEPARTMENTS)
    per = {"engineering": 18, "finance": 6, "people": 5, "marketing": 7, "sales": 12, "operations": 7,
           "leadership": 5}
    people, n = [], 1
    heads = {}
    for d in depts:
        for i in range(per[d]):
            emp = f"LU{n:03d}"
            name = f"{FIRST[(n * 7) % len(FIRST)]} {LAST[(n * 3) % len(LAST)]}"
            title = ("Head of " + DEPARTMENTS[d]) if i == 0 else rnd.choice(["Analyst", "Specialist", "Lead",
                                                                              "Manager", "Engineer", "Associate"])
            if i == 0:
                heads[d] = emp
            join = f"20{rnd.randint(19, 25)}-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}"
            people.append([emp, name, DEPARTMENTS[d], title, None if i == 0 else heads[d], join, "Active", None,
                           f"{name.split()[0].lower()}.{emp.lower()}@lumora.example"])
            n += 1
    people[0][3] = "Chief Executive Officer"
    _sheet(out / "hr_directory" / "Employee_Directory.xlsx", "Employee directory (no compensation)",
           ["Emp ID", "Full Name", "Department", "Title", "Manager ID", "Join Date", "Status", "Exit Date",
            "Work Email"], people)
    facts["headcount_total"] = len(people)
    facts["headcount_by_department"] = {DEPARTMENTS[d]: per[d] for d in depts}

    snaps, base = [], {d: max(1, per[d] - 3) for d in depts}
    for mi, m in enumerate(MONTHS):
        for d in depts:
            count = min(per[d], base[d] + mi // 4)
            snaps.append([f"HC-{m}-{d}", m, DEPARTMENTS[d], count, S, VERSION])
    _sheet(out / "headcount_snapshots" / "Headcount_Snapshots.xlsx", "Monthly headcount",
           ["Snapshot ID", "Month", "Department", "Headcount", "Synthetic", "Generator"], snaps)

    reqs = []
    for i in range(1, 19):
        d = depts[i % len(depts)]
        status = ["Open", "Filled", "On hold"][i % 3]
        reqs.append([f"LRQ-{i:03d}", DEPARTMENTS[d], f"{rnd.choice(['Senior', 'Junior', ''])} Role {i}".strip(),
                     MONTHS[i % 12], status, "Q4 hiring freeze (decision D-07)" if status == "On hold" else "", S])
    _sheet(out / "hiring" / "Hiring_Requisitions.xlsx", "Hiring requisitions (no candidate data)",
           ["Requisition", "Department", "Role", "Opened", "Status", "Note", "Synthetic"], reqs)
    facts["requisitions_on_hold"] = sum(1 for r in reqs if r[4] == "On hold")

    eng = [[f"ENG-{q}-{d}", q, DEPARTMENTS[d], per[d], round(3.4 + rnd.random() * 1.2, 1), S]
           for q in ("2026-Q2", "2026-Q3") for d in depts if per[d] >= 5]
    _sheet(out / "engagement" / "Team_Engagement.xlsx", "Team engagement, team level only (no comments)",
           ["Record ID", "Quarter", "Team", "Team size", "Engagement (1-5)", "Synthetic"], eng)

    leave = [[f"LV-{m}-{d}", m, DEPARTMENTS[d], rnd.randint(2, 20), S] for m in MONTHS[:10] for d in depts]
    _sheet(out / "leave_summary" / "Leave_Summary.xlsx", "Leave days, department-month aggregates only",
           ["Record ID", "Month", "Department", "Leave days", "Synthetic"], leave)

    # Finance: revenue, budgets, cash, forecast and a general ledger that reconcile.
    vendors = [f"{w} {s}" for w, s in zip(["Aster", "Birch", "Cobalt", "Dune", "Ember", "Fjord", "Garnet", "Halo",
                                           "Iris", "Jade", "Krypt", "Lumen", "Mosaic", "Nova", "Opal", "Prism",
                                           "Quill", "Rune", "Sable", "Tidal", "Umber", "Vale", "Willow", "Xeno",
                                           "Yarrow"], ["Cloud", "Labs", "Logistics", "Media", "Supply"] * 5,
                                          strict=True)]
    revenue, arr, customers = [], 4_200_000, 61
    for m in MONTHS:
        new, churn = rnd.randint(150_000, 320_000), rnd.randint(20_000, 90_000)
        arr += new - churn
        customers += rnd.randint(1, 5)
        revenue.append([m, arr, arr // 12, new, churn, customers, S])
    _sheet(out / "revenue" / "Revenue_Monthly.xlsx", "Monthly revenue (actuals, USD)",
           ["Month", "ARR", "MRR", "New ARR", "Churned ARR", "Customers", "Synthetic"], revenue)
    facts["arr_dec"], facts["customers_dec"] = revenue[-1][1], revenue[-1][5]

    gl, bva, voucher = [], [], 1
    spend = {m: 0 for m in MONTHS}
    for m in MONTHS:
        for d in depts:
            budget = 60_000 + 15_000 * per[d] // 3
            actual = budget + rnd.randint(-12_000, 18_000)
            bva.append([f"BVA-{m}-{d}", m, DEPARTMENTS[d], budget, actual, actual - budget, S])
        for _ in range(4):
            v = rnd.choice(vendors)
            amt = rnd.randint(4_000, 40_000)
            spend[m] += amt
            gl.append([f"{m}-15", f"V-{voucher:04d}", v, f"CC-{rnd.choice(['ENG', 'OPS', 'MKT', 'FIN'])}", amt,
                       f"{v} services, {m} (synthetic)"])
            voucher += 1
    _sheet(out / "budget_actuals" / "Budget_vs_Actuals.xlsx", "Budget versus actuals (USD)",
           ["Line ID", "Month", "Department", "Budget", "Actual", "Variance", "Synthetic"], bva)
    _sheet(out / "general_ledger" / "General_Ledger.xlsx", "General ledger extract",
           ["Txn Date", "Voucher", "Vendor / Payee", "Cost Centre", "Amount (Rs.)", "Narration"], gl)
    facts["ledger_total"] = sum(r[4] for r in gl)

    cash, bal = [], 9_800_000
    for m in MONTHS:
        burn = rnd.randint(380_000, 520_000)
        bal -= burn
        cash.append([m, bal, burn, round(bal / burn, 1), S])
    _sheet(out / "cash" / "Cash_and_Runway.xlsx", "Cash balance and runway (USD)",
           ["Month", "Cash balance", "Net burn", "Runway months", "Synthetic"], cash)
    facts["runway_dec"] = cash[-1][3]
    fc = [[q, arr + i * 280_000, "Pipeline-weighted forecast; not an actual", S]
          for i, q in enumerate(["2027-Q1", "2027-Q2", "2027-Q3", "2027-Q4"], start=1)]
    _sheet(out / "forecast" / "Revenue_Forecast.xlsx", "Revenue forecast (FORECAST, not actuals)",
           ["Quarter", "Forecast ARR", "Basis", "Synthetic"], fc)

    # Sales: 100 accounts, opportunities and renewals.
    accounts = [f"Account {chr(65 + i // 26)}{chr(65 + i % 26)}" for i in range(100)]
    opps = []
    for i in range(1, 161):
        stage = rnd.choice(["Prospecting", "Qualified", "Proposal", "Negotiation", "Won", "Lost"])
        opps.append([f"OPP-{i:04d}", accounts[i % 100], stage, rnd.randint(8, 120) * 1000, rnd.choice(MONTHS),
                     rnd.choice(PROJECTS), S])
    _sheet(out / "pipeline" / "Sales_Pipeline.xlsx", "Sales pipeline (USD)",
           ["Opportunity", "Account", "Stage", "Amount", "Close month", "Product", "Synthetic"], opps)
    facts["won_count"] = sum(1 for o in opps if o[2] == "Won")
    ren = [[f"REN-{i:03d}", accounts[i], rnd.choice(MONTHS[6:]), rnd.choice(["Renewed", "At risk", "Churned",
                                                                          "Pending"]), S] for i in range(40)]
    _sheet(out / "renewals" / "Renewals.xlsx", "Contract renewals",
           ["Renewal", "Account", "Due month", "Status", "Synthetic"], ren)

    # Marketing: 20 campaigns; some depend on a release.
    camps = []
    for i in range(1, 21):
        p = PROJECTS[i % 5]
        camps.append([f"CMP-{i:02d}", f"{p} launch wave {i}", rnd.choice(["Email", "Events", "Paid search",
                                                                           "Partners", "Content"]),
                      rnd.randint(5, 60) * 1000, rnd.randint(40, 900), rnd.randint(2, 80),
                      rnd.randint(20, 400) * 1000, f"{p} 2.0 release" if i % 4 == 0 else "", S])
    _sheet(out / "campaigns" / "Campaigns.xlsx", "Campaign performance (USD)",
           ["Campaign ID", "Campaign", "Channel", "Spend", "Leads", "Conversions", "Attributed pipeline",
            "Depends on", "Synthetic"], camps)

    # Engineering: 5 projects, 15 epics, 150 issues, releases, incidents.
    epics, issues, releases, incidents = [], [], [], []
    for pi, p in enumerate(PROJECTS):
        releases.append([f"{p} 2.0", p, f"2027-0{pi + 2}-15", "Planned", S])
        for e in range(3):
            key = f"{p[:3].upper()}-E{e + 1}"
            epics.append([key, f"{p} {['platform', 'experience', 'reliability'][e]} work", p,
                          ["In progress", "Done", "To do"][(pi + e) % 3], f"{p} 2.0", S])
    statuses = ["Done", "In progress", "To do"]
    for i in range(1, 151):
        ep = epics[i % 15]
        issues.append([f"LUM-{i:04d}", f"{ep[1]}: task {i}", ep[0], ep[2], statuses[i % 3], rnd.choice([1, 2, 3, 5, 8]),
                       rnd.choice(MONTHS[6:]), S])
    for i in range(1, 13):
        ep = epics[(i * 2) % 15]
        incidents.append([f"LIN-{i:03d}", f"{ep[2]} service degradation {i}", rnd.choice(["P1", "P2", "P3"]),
                          rnd.choice(MONTHS[4:]), ep[0], "Resolved", S])
    _sheet(out / "epics" / "Epics.xlsx", "Epics", ["Epic Key", "Epic", "Project", "Status", "Target release",
                                                  "Synthetic"], epics)
    _sheet(out / "issues" / "Issues.xlsx", "Issues", ["Issue Key", "Summary", "Epic Key", "Project", "Status",
                                                     "Points", "Updated", "Synthetic"], issues)
    _sheet(out / "releases" / "Releases.xlsx", "Release versions", ["Version", "Project", "Target date", "Status",
                                                                   "Synthetic"], releases)
    _sheet(out / "incidents" / "Incidents.xlsx", "Incidents", ["Incident", "Title", "Severity", "Month",
                                                              "Affected epic", "Status", "Synthetic"], incidents)
    facts["issues_total"], facts["epics_total"] = len(issues), len(epics)

    # Operations: 25 vendors, deliveries (some delayed, with the commitment they affect).
    vrows = [[f"VEN-{i + 1:03d}", v, v.split()[1], rnd.choice(MONTHS), rnd.randint(20, 400) * 1000, S]
             for i, v in enumerate(vendors)]
    _sheet(out / "vendors" / "Vendors.xlsx", "Vendor register (USD)", ["Vendor ID", "Vendor", "Category",
                                                                    "Contract renewal", "Annual value", "Synthetic"],
           vrows)
    deliv = []
    for i in range(1, 31):
        v = vendors[i % 25]
        delay = rnd.choice([0, 0, 0, 5, 12, 20])
        deliv.append([f"DLV-{i:03d}", v, rnd.choice(MONTHS), delay,
                      f"{PROJECTS[i % 5]} 2.0 release" if delay > 10 else "", S])
    _sheet(out / "deliveries" / "Deliveries.xlsx", "Vendor deliveries", ["Delivery", "Vendor", "Due month",
                                                                        "Delay days", "Affects", "Synthetic"], deliv)
    facts["delayed_deliveries"] = sum(1 for d in deliv if d[3] > 10)

    # Leadership: board updates (formal key-numbers tables) and OKR / decision notes with commitments.
    for qi, (period, sent, mi) in enumerate([("Apr to Jun 2026", "10 Jul 2026", 5),
                                             ("Jul to Sep 2026", "9 Oct 2026", 8)]):
        _pdf(out / "board_update" / f"Board_Update_Q{qi + 2}_2026.pdf",
             [f"{COMPANY}: Board update (SYNTHETIC DEMO)", f"Quarterly update. Period: {period}. Sent {sent}.",
              "Key numbers", "Metric Value", f"ARR USD {revenue[mi][1]:,}", f"Customers {revenue[mi][5]}",
              f"Runway {cash[mi][3]} months", "Product",
              "Nimbus 2.0 is due on 15 February 2027.",
              "Quartz 2.0 is due on 15 March 2027." if qi == 0 else
              "Quartz 2.0 was moved because a vendor delivery slipped; no new date has been agreed."])
    _docx(out / "leadership_notes" / "Leadership_Notes_H2_2026.docx", [
        f"{COMPANY} leadership notes (SYNTHETIC DEMO, {VERSION})",
        "OKR 1: Grow ARR above USD 6,500,000 by December 2026.",
        "OKR 2: Ship Nimbus 2.0 and Helix 2.0 with fewer than two P1 incidents per quarter.",
        "Decision D-07 (September 2026): a Q4 hiring freeze outside Engineering, to protect runway.",
        "Decision D-09 (October 2026): Helix moves ahead of Juniper in engineering priority.",
        "Commitment: Helix 2.0 is due on 15 April 2027.",
        "Dependency: the Quartz 2.0 marketing campaign depends on the Quartz 2.0 release.",
        "Dependency: Nimbus 2.0 depends on the Aster Cloud platform delivery.",
    ])
    facts.update({"commitments": {"Nimbus 2.0": "2027-02-15", "Helix 2.0": "2027-04-15"},
                  "moved": "Quartz 2.0", "decision_freeze": "D-07", "decision_priority": "D-09"})
    return {"version": VERSION, "seed": SEED, "sources": SOURCES, "facts": facts, "heads": heads,
            "people": [p[0] for p in people]}


if __name__ == "__main__":
    import json
    import sys

    m = generate(Path(sys.argv[1]))
    print(json.dumps({"version": m["version"], "facts": m["facts"]}, indent=1, default=str))
