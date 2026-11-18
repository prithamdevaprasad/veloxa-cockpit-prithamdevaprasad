"""A SYNTHETIC four-day company for the acceptance suite and point-in-time tests. Every name and value is invented.

The four Day folders match config/source_catalogue.toml, so they go through the same admission pipeline, contracts,
K-13 source checks, projections, claims and Conflict Registry as the real packs:
- Day 1: brief, README, HR export (19 people: 14 in engineering/finance/marketing/people ops), payroll, ledger,
         a board update (ARR for two periods), an e-mail, a meeting summary that states an ARR matching no period.
- Day 2: hiring tracker (one requisition on hold), leave register, pulse survey (two quarters), a meeting summary.
- Day 3: Jira export (Issues with initials as assignees; a version moved 30 days), on-call rota (one person primary
         in 6 of 8 weeks), incidents, git log (authors by exact corporate e-mail), the T6b-style voice note.
- Day 4: a corrected HR export (one title changed: a correction, not a conflict) and a T6d-style voice note.
"""

from __future__ import annotations

from pathlib import Path

from synthetic_pack import GL_COLUMNS, HR_COLUMNS, PAYROLL_COLUMNS, _docx, _xlsx, make_board_update

DAYS = ("Day1_Mon_16Nov", "Day2_Tue_17Nov", "Day3_Wed_18Nov", "Day4_Thu_19Nov")
DEPTS = [("Engineering", "S10", ["VP Engineering", "Staff Engineer", "Frontend Engineer", "Site Reliability Engineer",
                                 "Backend Engineer"]),
         ("Finance", "S20", ["CFO", "FP&A Manager", "Accounts Executive"]),
         ("Marketing", "S30", ["Head of Marketing", "Growth Manager", "Product Marketing Manager"]),
         ("People & Ops", "S40", ["Head of People", "Talent Partner", "Office Executive"]),
         ("Sales", "S50", ["Sales Lead", "Account Executive"])]
SOURCES = {
    "D2": ["data_room/hr/Hiring_Tracker.xlsx", "data_room/hr/Leave_Register.xlsx", "data_room/hr/Pulse_Survey.xlsx",
           "transcripts/T3_Synthetic.docx"],
    "D3": ["data_room/engineering/Jira_Export_SYN.xlsx", "data_room/engineering/OnCall_Rota.xlsx",
           "data_room/engineering/Incidents_Synthetic.xlsx", "data_room/engineering/git_log.csv",
           "transcripts/T6b_Synthetic.docx"],
    "D4": ["data_room/hr/Employee_Master.xlsx", "transcripts/T6d_Synthetic.docx"],
}


def people(corrected: bool = False) -> list[list]:
    rows = [["S000", "Synthetic Chief", "Leadership", "CEO", None, "2090-01-01", "Active", None, "chief@example.test"]]
    for dept, prefix, titles in DEPTS:
        for i, t in enumerate(titles, 1):
            emp = f"{prefix}{i}"
            if corrected and emp == "S104":
                t = "Senior Site Reliability Engineer"          # the Day 4 correction
            status = "Notice period" if emp == "S303" else "Active"
            rows.append([emp, f"Syn {dept.split()[0]} {i}", dept, t, "S000" if i == 1 else f"{prefix}1",
                         f"209{i}-0{i}-0{i}", status, None, f"{emp.lower()}@example.test"])
    return rows


def _jira(path: Path) -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Issues"
    ws.append(["Synthetic Jira export"])
    ws.append(["Key", "Type", "Summary", "Epic Link", "Assignee", "Story Points", "Status", "Created", "Resolved",
               "Fix Version"])
    for i in range(1, 7):
        ws.append([f"SYN-{i}", "Story", f"Synthetic task {i}", "SYN-E1", "AB" if i % 2 else "CD", 3,
                   "Done" if i <= 2 else "In Progress", "2099-01-0" + str(i), "2099-02-01" if i <= 2 else None,
                   "Orion 2"])
    v = wb.create_sheet("Versions")
    v.append(["Version", "Release Date", "Original Release Date", "Last Changed", "Changed By"])
    v.append(["Orion 2", "2099-04-30", "2099-03-31", "2099-02-15", "pm-x"])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def make_days(root: Path) -> Path:
    d1, d2, d3, d4 = (root / d for d in DAYS)
    # Day 1
    _docx(d1 / "brief" / "Synthetic_Brief.docx", ["Synthetic brief", "Fourteen people across four departments."])
    (d1 / "data_room").mkdir(parents=True, exist_ok=True)
    (d1 / "data_room" / "README.txt").write_text("Synthetic data room.\n", encoding="utf-8")
    _xlsx(d1 / "data_room" / "hr" / "Employee_Master.xlsx", "Synthetic HR export", HR_COLUMNS, people())
    _xlsx(d1 / "data_room" / "finance" / "Payroll_Register_Synthetic.xlsx", "Synthetic payroll", PAYROLL_COLUMNS,
          [["S101", "Jan-2099", 31, 31, 1000, 100, 900, "XX"]])
    _xlsx(d1 / "data_room" / "finance" / "GL_Export_Synthetic.xlsx", "Synthetic ledger", GL_COLUMNS,
          [["2099-01-01", "V-1", "Vendor One", "CC-A", 10, "Synthetic line"],
           ["2099-01-28", "V-2", "Salaries (payroll)", "CC-P", 3000, "Synthetic payroll line"]])
    make_board_update(d1, [("ARR (recurring)", "Rs. 7.3 Cr")], "Board_Q1.pdf", "Jan to Mar 2099", "9 Apr 2099")
    make_board_update(d1, [("ARR (recurring)", "Rs. 9 Cr")], "Board_Q2.pdf", "Apr to Jun 2099", "9 Jul 2099")
    (d1 / "data_room" / "email").mkdir(parents=True, exist_ok=True)
    (d1 / "data_room" / "email" / "01.eml").write_text(
        "From: Syn Finance 1 <s201@example.test>\nTo: Synthetic Chief <chief@example.test>\nSubject: synthetic\n"
        "Date: Mon, 05 Jan 2099 10:00:00 +0530\nMessage-ID: <syn-1@example.test>\n"
        "Content-Type: text/plain; charset=\"utf-8\"\n\nSynthetic body.\n", encoding="utf-8")
    _docx(d1 / "transcripts" / "T1_Synthetic.docx",
          ["T1 synthetic kickoff", "Our ARR is about Rs. 30 Cr and growing."])
    # Day 2
    _xlsx(d2 / "data_room" / "hr" / "Hiring_Tracker.xlsx", "Synthetic hiring", [
        "Req ID", "Role", "Team", "Hiring Manager", "Opened", "Status", "Status Date", "In pipeline", "Notes"],
          [["R-1", "Platform Engineer", "Engineering", "S101", "2099-01-10", "On hold", "2099-02-01", 2, None],
           ["R-2", "Analyst", "Finance", "S201", "2099-01-12", "Open", "2099-02-01", 4, None]])
    _xlsx(d2 / "data_room" / "hr" / "Leave_Register.xlsx", "Synthetic leave",
          ["Employee", "Leave Type", "From", "To", "Days", "Approved By"],
          [["Syn Engineering 2", "Annual", "2099-02-01", "2099-02-03", 3, "S101"]])
    _xlsx(d2 / "data_room" / "hr" / "Pulse_Survey.xlsx", "Synthetic pulse",
          ["Quarter", "Team", "Team size", "Responses", "Engagement (1-5)", "Comments"],
          [["Jan-Mar 2099", "Engineering", 5, 5, 3.8, "Not shown"],
           ["Apr-Jun 2099", "Engineering", 5, 4, 3.4, "Not shown"]])
    _docx(d2 / "transcripts" / "T3_Synthetic.docx", ["T3 synthetic people call", "Hiring is paused for now."])
    # Day 3
    _jira(d3 / "data_room" / "engineering" / "Jira_Export_SYN.xlsx")
    _xlsx(d3 / "data_room" / "engineering" / "OnCall_Rota.xlsx", "Synthetic rota", ["Week of", "Primary", "Secondary"],
          [[f"2099-0{1 + w // 4}-{1 + 7 * (w % 4):02d}", "Asha" if w < 6 else "Ravi", "Ravi"] for w in range(8)])
    _xlsx(d3 / "data_room" / "engineering" / "Incidents_Synthetic.xlsx", "Synthetic incidents",
          ["ID", "Severity", "Opened", "Resolved", "Service", "Responder"],
          [["INC-S1", "P2", "2099-02-01T10:00:00", "2099-02-01T12:00:00", "Checkout", "Asha"]])
    (d3 / "data_room" / "engineering").mkdir(parents=True, exist_ok=True)
    (d3 / "data_room" / "engineering" / "git_log.csv").write_text(
        "sha,author_email,committed_at,repo,message,files_changed\n"
        "a1,s102@example.test,2099-01-05T10:00:00,orion,synthetic change,3\n"
        "a2,s103@example.test,2099-01-06T10:00:00,orion-web,synthetic change,2\n"
        "a3,outsider@example.test,2099-01-07T10:00:00,orion,synthetic change,1\n", encoding="utf-8")
    _docx(d3 / "transcripts" / "T6b_Synthetic.docx", ["T6b synthetic voice note", "Marketing will move under a new "
                                                      "leader next month, still finalising."])
    # Day 4
    _xlsx(d4 / "data_room" / "hr" / "Employee_Master.xlsx", "Synthetic HR export (corrected)", HR_COLUMNS,
          people(corrected=True))
    _docx(d4 / "transcripts" / "T6d_Synthetic.docx", ["T6d synthetic voice note",
                                                      "Zephyrine resignation affects the Orion 2 launch."])
    return root


def authorisations_toml() -> str:
    out = ['[[pack]]\nid = "D1"\nbaseline = true\napproved_by = "Synthetic owner"\napproved_on = 2026-11-16\n'
           'decision_record = "synthetic acceptance fixture"\n']
    for pid, srcs in SOURCES.items():
        out.append(f'[[pack]]\nid = "{pid}"\napproved_by = "Synthetic owner"\napproved_on = 2026-11-18\n'
                   f'decision_record = "synthetic acceptance fixture"\nsources = {srcs!r}\n'.replace("'", '"'))
    return "\n".join(out)
