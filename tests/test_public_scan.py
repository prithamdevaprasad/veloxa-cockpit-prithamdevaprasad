"""The public safety scan is a real gate: it exits non-zero on any finding, prints every finding without leaking
secret values, and a pipe into `tail` cannot hide its failure under `pipefail` (how CI runs every step).
Planted findings are assembled at run time, so this file itself contains none."""

import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCAN = REPO / "scripts" / "public_scan.py"

PLANTED = {
    "secret": "token = '" + "gh" + "p_" + "a" * 36 + "'\n",
    "local path": "see " + "C:" + "\\" + "Users" + "\\" + "someone\\file\n",
    "email address": "write to " + "person" + "@" + "corp-example.org\n",
    "currency amount": "the fee is " + "R" + "s. 5 Cr\n",
}


def run(*args):
    return subprocess.run([sys.executable, str(SCAN), *args], capture_output=True, text=True)


def test_the_repository_itself_scans_clean():
    r = run()
    assert r.returncode == 0, r.stdout
    assert r.stdout.strip().endswith("CLEAN")


def test_a_clean_directory_passes(tmp_path):
    (tmp_path / "ok.txt").write_text("nothing to see\n", encoding="utf-8")
    assert run("--root", str(tmp_path)).returncode == 0


@pytest.mark.parametrize("kind", sorted(PLANTED))
def test_every_kind_of_finding_fails_the_scan(tmp_path, kind):
    (tmp_path / "planted.txt").write_text(PLANTED[kind], encoding="utf-8")
    r = run("--root", str(tmp_path))
    assert r.returncode == 1 and f"FINDING {kind}" in r.stdout and r.stdout.strip().endswith("FAILED")


def test_secret_values_are_redacted_in_the_output(tmp_path):
    (tmp_path / "planted.txt").write_text(PLANTED["secret"], encoding="utf-8")
    out = run("--root", str(tmp_path)).stdout
    assert "redacted" in out and "a" * 36 not in out


def test_a_raw_data_file_fails_the_scan(tmp_path):
    (tmp_path / "export.xlsx").write_bytes(b"PK")
    r = run("--root", str(tmp_path))
    assert r.returncode == 1 and "raw data file" in r.stdout


def test_usage_errors_are_not_a_pass(tmp_path):
    assert run("--root", str(tmp_path / "missing")).returncode == 2


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_a_pipe_into_tail_cannot_hide_a_failure_under_pipefail(tmp_path):
    (tmp_path / "planted.txt").write_text(PLANTED["secret"], encoding="utf-8")
    py = Path(sys.executable).as_posix()
    cmd = f'"{py}" "{SCAN.as_posix()}" --root "{tmp_path.as_posix()}" | tail -n 1'
    masked = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
    gated = subprocess.run(["bash", "-eo", "pipefail", "-c", cmd], capture_output=True, text=True)
    assert masked.returncode == 0        # why the earlier gate (E50) was unsafe: the pipe reported tail's status
    assert gated.returncode != 0         # how CI runs every step: the scan's failure fails the step


def test_allowlist_entries_are_exact_full_amounts_not_prefixes():
    entries = tomllib.loads((REPO / "config" / "public_scan_allowlist.toml").read_text(encoding="utf-8"))["allow"]
    full = re.compile(r"^Rs\. \d+(?:\.\d+)? (?:Cr|L|bn)$")
    assert entries and all(full.match(e["token"]) for e in entries)
    for e in entries:   # every allowlisted token is still present in its file (no stale exclusions)
        assert e["token"] in (REPO / e["file"]).read_text(encoding="utf-8")
