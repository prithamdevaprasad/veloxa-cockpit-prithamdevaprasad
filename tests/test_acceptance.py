"""Runs the 32-case hackathon acceptance suite (scripts/run_acceptance.py) as one test, in a subprocess so its own
configuration and disposable database stay isolated. Synthetic fixtures only: safe for CI."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.db
ROOT = Path(__file__).resolve().parents[1]


def test_the_acceptance_suite_passes_every_case(tmp_path):
    if not os.environ.get("VELOXA_TEST_ADMIN_DSN"):
        pytest.skip("VELOXA_TEST_ADMIN_DSN not set: database tests skipped")
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "run_acceptance.py"), "--json",
                          str(tmp_path / "a.json"), "--md", str(tmp_path / "a.md")],
                         capture_output=True, text=True, timeout=900)
    assert out.returncode == 0, out.stdout[-4000:] + out.stderr[-4000:]
    assert "32/32 passed" in out.stdout
