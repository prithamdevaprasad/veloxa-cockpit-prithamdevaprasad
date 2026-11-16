"""Export validation contracts (ARCHITECTURE.md §7.2). SYNTHETIC data only."""

import pytest

from veloxa.catalogue import classify, load_catalogue
from veloxa.ingest.contracts import Contract, validate_rows
from veloxa.releases import AdmissionRefused

C = Contract(
    required_columns=("ID", "Date", "Gross", "Ded", "Net", "Days", "Max"),
    optional=("Max",),
    unique=("ID",),
    iso_dates=("Date",),
    integers=("Gross", "Ded", "Net", "Days"),
    identities=(("Gross", "Ded", "Net"),),
    not_greater=(("Days", "Max"),),
)


def _row(**over):
    row = {"ID": "A1", "Date": "2099-01-31", "Gross": 1000, "Ded": 100, "Net": 900, "Days": 30, "Max": 31}
    row.update(over)
    return row


def _check(*rows):
    validate_rows("x.xlsx", C, [({"row": 4 + i}, r) for i, r in enumerate(rows)])


def test_valid_rows_pass():
    _check(_row(), _row(ID="A2"))


@pytest.mark.parametrize("bad, column, problem", [
    ({"Date": "31/01/2099"}, "Date", "ISO date"),
    ({"Gross": "1000"}, "Gross", "integer"),
    ({"Net": 901}, "Net", "arithmetic identity"),
    ({"Days": 32}, "Days", "must not exceed"),
    ({"ID": None}, "ID", "required value missing"),
])
def test_violations_are_refused_and_name_row_and_column_but_never_the_value(bad, column, problem):
    with pytest.raises(AdmissionRefused) as exc:
        _check(_row(**bad))
    msg = str(exc.value)
    assert exc.value.reason == "contract_violation"
    assert "row 4" in msg and repr(column) in msg and problem in msg
    for value in bad.values():
        if value is not None:
            assert str(value) not in msg


def test_duplicate_in_unique_column_is_refused():
    with pytest.raises(AdmissionRefused, match="duplicate"):
        _check(_row(), _row())


def test_missing_required_column_is_refused():
    row = _row()
    del row["Date"]
    with pytest.raises(AdmissionRefused, match="required column missing"):
        _check(row)


def test_optional_column_may_be_empty():
    _check(_row(Max=31), _row(ID="A2", Max=40))


@pytest.mark.parametrize("path", [
    "data_room/hr/Employee_Master.xlsx",
    "data_room/finance/Payroll_Register_X.xlsx",
    "data_room/finance/GL_Export_X.xlsx",
])
def test_every_d1_row_export_has_a_contract(path):
    assert classify(load_catalogue(), path).contract is not None


@pytest.mark.db
def test_a_contract_violation_refuses_the_whole_pack_and_commits_nothing(ingest_conn, tenant, schedule, tmp_path):
    import openpyxl
    from conftest import D1_DAY
    from synthetic_pack import make_d1

    from veloxa import ledger
    from veloxa.ingest.pipeline import admit_pack

    pack = make_d1(tmp_path)
    path = pack / "data_room" / "finance" / "Payroll_Register_Synthetic.xlsx"
    wb = openpyxl.load_workbook(path)
    wb.worksheets[0].cell(row=4, column=7).value = 1  # Net no longer equals Gross - Deductions
    wb.save(path)
    with pytest.raises(AdmissionRefused) as exc:
        admit_pack(ingest_conn, tenant, schedule, "D1", tmp_path, D1_DAY)
    assert exc.value.reason == "contract_violation" and "row 4" in str(exc.value)
    assert ledger.batches(ingest_conn, tenant) == []
