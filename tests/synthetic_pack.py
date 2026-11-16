"""Generate a small SYNTHETIC release pack for tests and CI. Every name and value here is invented.

Usage: python tests/synthetic_pack.py <output-folder>
Creates <output-folder>/Day1_Mon_16Nov with files that match config/source_catalogue.toml.
"""

from __future__ import annotations

import sys
from pathlib import Path

D1 = "Day1_Mon_16Nov"
# Expected admission results for the synthetic D1 pack.
EXPECTED_FILES = 8
EXPECTED_RECORDS = 11  # brief 1, readme 1, hr 2, payroll 2, ledger 3, email 1, transcript 1; template 0
EXPECTED_BY_CLASS = {
    "internal": 2, "personal": 2, "sensitive_personal": 2, "confidential": 3,
    "payroll_aggregate": 1, "private_comms": 1,
}


HR_COLUMNS = ["Emp ID", "Full Name", "Department", "Title", "Manager ID", "Join Date", "Status", "Exit Date",
              "Work Email"]
PAYROLL_COLUMNS = ["Emp ID", "Month", "Days in Month", "Days Payable", "Gross (Rs.)", "Deductions (Rs.)", "Net (Rs.)",
                   "Processed By"]
GL_COLUMNS = ["Txn Date", "Voucher", "Vendor / Payee", "Cost Centre", "Amount (Rs.)", "Narration"]


def _xlsx(path: Path, title: str, header: list[str], rows: list[list]) -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet A"
    ws.append([title])
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


def make_d1(root: Path) -> Path:
    pack = root / D1
    _docx(pack / "brief" / "Synthetic_Brief.docx", ["Synthetic brief", "A fictional company for tests."])
    (pack / "data_room").mkdir(parents=True, exist_ok=True)
    (pack / "data_room" / "README.txt").write_text("Synthetic data room.\n\nhr/ test folder\n", encoding="utf-8")
    _xlsx(pack / "data_room" / "templates" / "Blank_Template.xlsx", "Template", ["A", "B", "C"], [])
    _xlsx(pack / "data_room" / "hr" / "Employee_Master.xlsx", "Synthetic HR export", HR_COLUMNS,
          [["T001", "Person Alpha", "Testing", "Lead Tester", None, "2099-01-01", "Active", None, "alpha@example.test"],
           ["T002", "Person Beta", "Testing", "Tester", "T001", "2099-01-02", "Active", None, "beta@example.test"]])
    _xlsx(pack / "data_room" / "finance" / "Payroll_Register_Synthetic.xlsx", "Synthetic payroll", PAYROLL_COLUMNS,
          [["T001", "Jan-2099", 31, 31, 1000, 100, 900, "XX"], ["T002", "Jan-2099", 31, 31, 2000, 200, 1800, "XX"]])
    _xlsx(pack / "data_room" / "finance" / "GL_Export_Synthetic.xlsx", "Synthetic ledger", GL_COLUMNS,
          [["2099-01-01", "V-1", "Vendor One", "CC-A", 10, "Synthetic line"],
           ["2099-01-28", "V-2", "Salaries (payroll)", "CC-P", 3000, "Synthetic payroll line"],
           ["2099-02-01", "V-3", "Vendor Two", "CC-A", 20, "Synthetic line"]])
    (pack / "data_room" / "email").mkdir(parents=True, exist_ok=True)
    (pack / "data_room" / "email" / "01.eml").write_text(
        "From: Person Alpha <alpha@example.test>\nTo: Person Beta <beta@example.test>\n"
        "Subject: synthetic\nDate: Mon, 05 Jan 2099 10:00:00 +0530\nMessage-ID: <s-1@example.test>\n"
        "Content-Type: text/plain; charset=\"utf-8\"\n\nSynthetic body.\n",
        encoding="utf-8",
    )
    _docx(pack / "transcripts" / "T1_Synthetic.docx", ["T1 synthetic", "Summary line."])
    return pack


def make_poisoned_d2(root: Path) -> Path:
    """A D2 folder whose only file matches no catalogue rule: reading it would raise UnclassifiedSource."""
    pack = root / "Day2_Tue_17Nov"
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "UNRELEASED_DO_NOT_READ.bin").write_bytes(b"\x00synthetic\x00")
    return pack


if __name__ == "__main__":
    out = Path(sys.argv[1])
    make_d1(out)
    print(f"synthetic pack written to {out / D1}")
