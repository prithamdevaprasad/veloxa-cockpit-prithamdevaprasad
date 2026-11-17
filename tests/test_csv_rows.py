"""Generic CSV row reading (SYNTHETIC data)."""

from veloxa.ingest.csv_rows import read


def test_rows_keep_values_as_written_with_row_numbers_and_widths():
    header, rows = read(b'\xef\xbb\xbfa,b,c\n1,"x, y",\n\n2,z,3,extra\n4\n')
    assert header == ["a", "b", "c"]
    assert [r["__row__"] for r in rows] == [1, 3, 4]                  # the blank line is skipped, numbering kept
    assert rows[0] == {"__row__": 1, "__width__": 3, "a": "1", "b": "x, y", "c": ""}
    assert rows[1]["__width__"] == 4 and rows[2]["__width__"] == 1  # a contract can reject wrong widths


def test_an_empty_file_has_no_header_and_no_rows():
    assert read(b"") == ([], [])
