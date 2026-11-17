# Dependency inventory

Exact versions are pinned in `requirements.lock`, which pip uses as constraints. This file records why each direct dependency exists.

| Package | Range (`pyproject.toml`) | License | Purpose |
|---|---|---|---|
| psycopg[binary] | >=3.2,<3.4 | LGPL-3.0 | PostgreSQL driver (system of record) |
| fastapi, uvicorn | <1 | MIT, BSD | HTTP API |
| openpyxl, python-docx, pdfplumber | pinned ranges | MIT | Source parsers |
| **dbos** | >=3.5,<3.6 (3.5.0) | MIT (DBOS, Inc.; Production/Stable) | C26 durable workflows (§17.2). Added 17 Nov 2026 with owner approval. Transitive: SQLAlchemy 2.1.9 (+greenlet), PyYAML, python-dateutil, croniter, click, typing-extensions; colorama and tzdata on Windows only. **No optional extras** (otel, validation, sqlite, enterprise) are installed |
| pytest (>=9.0.3), httpx, ruff | dev only | MIT, BSD | Tests, lint and SAST (Ruff `S` rules). pytest 9.0.3 fixes PYSEC-2026-1845 |

**Frontend** (`frontend/package.json`, with an npm lockfile):
- react and react-dom 18.3.1;
- dev only: vite 6.4.5, @vitejs/plugin-react 4.7.0, typescript 5.6.3, @playwright/test 1.65.1. Playwright is a test-only browser runner, never shipped.

**Vulnerability scanning.** `python scripts/dependency_audit.py` runs pip-audit 2.10.1 in a throwaway venv against `requirements.lock`, plus `npm audit`. The auditor is not a project dependency. On 17 Nov 2026 both reported no known vulnerabilities. On 18 Nov 2026 Python is still clean; see the open finding below for npm.

**Open finding, 18 Nov 2026: the audit FAILS (frontend). It is not suppressed.**
- **Advisory:** [GHSA-fc5q-39xc-vjpf](https://github.com/advisories/GHSA-fc5q-39xc-vjpf), High (CVSS 4.0 8.7, availability only). ReDoS in `picomatch` ≤ 4.0.7 through attacker-supplied extglob patterns. Published 19 Oct 2026, updated 17 Nov 2026.
- **No patched version exists:** 4.0.7 is the latest release.
- **Path:** `vite@6.4.5` → `picomatch@4.0.7`, both directly and through `fdir` and `tinyglobby`.

**Exposure (checked):**
- **Build and development time only.** It is not in the production dependency tree (`npm ls picomatch --omit=dev` is empty), and not in the shipped bundle (`dist/`).
- **No user-supplied patterns:** the glob patterns come from Vite's own configuration, never from users.
- **Local only:** the development server binds to `127.0.0.1`.

**Not done:**
- `npm audit fix --force`: it would *downgrade* Vite to 6.2.7 and reintroduce the advisories fixed by 6.4.5.
- An override: none is possible without a patched release.

**Recommendation (owner decision):** accept this as a development-time residual risk until a patched `picomatch` is published. Then update and re-run the audit. The CI `dependency-audit` job will fail until then. That is intended.

**Not yet:**
- CI execution: the jobs are defined but the branch is not pushed.
- Container OS-package scanning: no scanner is available locally (see `docs/operations/CONTAINER_SECURITY.md`).
