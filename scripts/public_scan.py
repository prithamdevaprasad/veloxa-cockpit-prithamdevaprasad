"""Public-repository safety scan: secrets, local paths, personal addresses, raw data files, currency amounts.

Usage:
    python scripts/public_scan.py               # scan every tracked or committable file in this repository
    python scripts/public_scan.py --root DIR    # scan every file under DIR (used to test the failure path)

Exit status: 0 only when NOTHING is found; 1 on any finding; 2 on a usage or I/O error. Every finding is printed
(never truncated), secret values are redacted, and the last line is the verdict. Gate on the EXIT STATUS, not on
the printed text: in CI each step runs under `bash -eo pipefail`, so a pipe into `tail` cannot hide a failure.

This scanner holds no private material. Checks that need private reference data (names and identifiers from the
release packs, later-pack terms) run in a separate, private, local scan before every push.
Allowlisted currency amounts (config/public_scan_allowlist.toml) are exact tokens of invented test values that
have been checked against the release packs; never add a real company value there.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ALLOWED_EMAIL_DOMAINS = ("example.test", "users.noreply.github.com", "anthropic.com")

CHECKS: dict[str, tuple[re.Pattern, bool]] = {   # name -> (pattern, redact the match in output)
    "secret": (re.compile(r"(?i)sk-ant-[a-z0-9_-]{8,}|ghp_[a-z0-9]{20,}|github_pat_[a-z0-9_]{20,}|AKIA[0-9A-Z]{16}"
                          r"|-----BEGIN [A-Z ]*PRIVATE KEY-----|xox[baprs]-[a-z0-9-]{10,}"), True),
    "assigned credential": (re.compile(r"(?i)\b(?:api[_-]?key|secret[_-]?key|password)\s*[:=]\s*['\"][^'\"\s]{12,}"
                                       r"['\"]"), True),
    "local path": (re.compile(r"(?i)\b[A-Z]:[\\/]Users[\\/]|/home/[a-z][\w.-]+/|/Users/[A-Za-z][\w.-]+/|OneDrive"
                              r"|AppData[\\/]"), False),
    "email address": (re.compile(r"[\w.+-]+@((?:[\w-]+\.)+[A-Za-z]{2,})\b"), False),  # a DSN's @host IP is not one
    "currency amount": (re.compile(r"₹\s?\d[\d,.]*|Rs\.\s?\d[\d,.]*(?:\s?(?:Cr|L|lakh|crore|bn)\b)?"
                                   r"|\b\d+(?:\.\d+)?\s?(?:lakh|crore)\b"), False),
}
RAW_FILE = re.compile(r"(?i)\.(xlsx|xls|csv|eml|docx|pdf|zip|parquet|sqlite|db)$|(^|/)\.env$")
SKIP = {"scripts/public_scan.py", "config/public_scan_allowlist.toml"}  # the scanner's own patterns and allowlist


def allowlist() -> set[tuple[str, str]]:
    path = REPO / "config" / "public_scan_allowlist.toml"
    if not path.is_file():
        return set()
    return {(e["file"], e["token"]) for e in tomllib.loads(path.read_text(encoding="utf-8")).get("allow", [])}


def files(root: Path | None) -> list[tuple[str, Path]]:
    if root is not None:
        return [(p.relative_to(root).as_posix(), p) for p in sorted(root.rglob("*")) if p.is_file()]
    out = subprocess.run(["git", "-C", str(REPO), "ls-files", "--cached", "--others", "--exclude-standard"],
                         capture_output=True, text=True, check=True).stdout.splitlines()
    return [(f, REPO / f) for f in out if (REPO / f).is_file()]


def scan(root: Path | None = None) -> list[str]:
    allowed = allowlist() if root is None else set()
    findings: list[str] = []
    for rel, path in files(root):
        if RAW_FILE.search(rel):
            findings.append(f"raw data file tracked: {rel}")
            continue
        if rel in SKIP:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for name, (pattern, redact) in CHECKS.items():
                for m in pattern.finditer(line):
                    token = m.group(0).strip()
                    if name == "email address" and m.group(1).lower().endswith(ALLOWED_EMAIL_DOMAINS):
                        continue
                    if (rel, token) in allowed:
                        continue
                    shown = f"[redacted {len(token)} chars]" if redact else token
                    findings.append(f"{name}: {rel}:{lineno}: {shown}")
    return findings


def main(argv: list[str]) -> int:
    root = None
    if argv:
        if len(argv) != 2 or argv[0] != "--root" or not Path(argv[1]).is_dir():
            print("usage: public_scan.py [--root DIR]", file=sys.stderr)
            return 2
        root = Path(argv[1])
    findings = scan(root)
    for f in findings:
        print(f"FINDING {f}")
    print(f"public scan: {len(findings)} finding(s): {'CLEAN' if not findings else 'FAILED'}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
