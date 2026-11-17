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

**Vulnerability scanning.** `python scripts/dependency_audit.py` runs pip-audit 2.10.1 in a throwaway venv against `requirements.lock`, plus `npm audit`. The auditor is not a project dependency. On 17 Nov 2026 both reported no known vulnerabilities.

**Not yet:** running the audit in CI; container image scanning. No scanner is installed, so the image has not been scanned.
