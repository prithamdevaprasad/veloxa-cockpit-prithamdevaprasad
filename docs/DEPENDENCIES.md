# Dependency inventory

Exact versions are pinned in `requirements.lock`, which pip uses as constraints. This file records why each direct dependency exists.

| Package | Range (`pyproject.toml`) | License | Purpose |
|---|---|---|---|
| psycopg[binary] | >=3.2,<3.4 | LGPL-3.0 | PostgreSQL driver (system of record) |
| fastapi, uvicorn | <1 | MIT, BSD | HTTP API |
| openpyxl, python-docx, pdfplumber | pinned ranges | MIT | Source parsers |
| **dbos** | >=3.5,<3.6 (3.5.0) | MIT (DBOS, Inc.; Production/Stable) | C26 durable workflows (§17.2). Added 17 Nov 2026 with owner approval. Transitive: SQLAlchemy 2.1.9 (+greenlet), PyYAML, python-dateutil, croniter, click, typing-extensions; colorama and tzdata on Windows only. **No optional extras** (otel, validation, sqlite, enterprise) are installed |
| pytest, httpx, ruff | dev only | MIT, BSD | Tests and lint |

**Frontend** (`frontend/package.json`, with an npm lockfile):
- react and react-dom 18.3.1;
- dev only: vite, @vitejs/plugin-react, typescript, @playwright/test. Playwright is a test-only browser runner, never shipped.

**Not yet in place:** automated vulnerability scanning (pip-audit or npm audit in CI). This is part of the Sprint 10 security milestone.
