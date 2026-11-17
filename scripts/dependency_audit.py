"""Dependency vulnerability scan (Sprint 10): Python pins and the frontend lockfile.

    python scripts/dependency_audit.py

- Python: runs pip-audit (pinned below) in a THROWAWAY virtual environment against the exact pins in
  requirements.lock, so the auditor and its ~20 transitive packages never enter the project's dependencies.
- Frontend: `npm audit` against frontend/package-lock.json (built into npm).
Only public package names and versions are sent to the vulnerability databases (PyPI/OSV, npm). Exit 1 on any
finding. SAST is separate: Ruff's `S` (flake8-bandit) rules run with `ruff check`.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIP_AUDIT = "pip-audit==2.10.1"


def python_audit() -> int:
    pins = [ln for ln in (ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines()
            if ln and not ln.startswith("#")]
    with tempfile.TemporaryDirectory(prefix="veloxa-audit-") as d:
        env_dir = Path(d) / "venv"
        venv.create(env_dir, with_pip=True)
        py = env_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        subprocess.run([str(py), "-m", "pip", "install", "-q", PIP_AUDIT], check=True)  # noqa: S603
        req = Path(d) / "requirements.txt"
        req.write_text("\n".join(pins) + "\n", encoding="utf-8")
        r = subprocess.run([str(py), "-m", "pip_audit", "-r", str(req), "--no-deps",  # noqa: S603
                            "--progress-spinner", "off"], check=False)
    return r.returncode


def npm_audit() -> int:
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    return subprocess.run([npm, "audit"], cwd=ROOT / "frontend", check=False).returncode  # noqa: S603


if __name__ == "__main__":
    py_rc, npm_rc = python_audit(), npm_audit()
    print(f"dependency audit: python {'clean' if py_rc == 0 else 'FINDINGS'}, "
          f"frontend {'clean' if npm_rc == 0 else 'FINDINGS'}")
    sys.exit(0 if py_rc == 0 and npm_rc == 0 else 1)
