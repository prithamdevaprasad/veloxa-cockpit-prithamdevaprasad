# Veloxa CXO Cockpit

An evidence-grounded executive cockpit for **Veloxa Technologies**, a synthetic company created for the Hiyaku Systems Hackathon (16–20 Nov 2026).

This README is the **local operating manual**. Every command below was run on Windows 11 (PowerShell 7) on 17–18 Nov 2026. Extended detail, including the API contract, the module map and more troubleshooting, is in [`LOCAL_DEVELOPMENT.md`](LOCAL_DEVELOPMENT.md).

> **Status:** local development build. **Nothing is production-deployed.** Features marked *blocked* below wait for owner decisions or for release-pack admission. They are not hidden and not faked.

---

## 1. Product overview

**What it does.** The cockpit answers a CEO's questions with:
- short answers that **cite the records behind them**, with an evidence grade A–D;
- honest **"I don't know"** and **"the sources disagree"** answers.

It also:
- answers **"what did we know on date X?"** without leaking later material;
- shows what to watch, unranked until the owner sets the ranking values (D-15);
- lets an interviewer talk to **employee agents** that are grounded only in records the interviewer may see.

**Architecture** (approved baseline v1.0.1, [`ARCHITECTURE.md`](ARCHITECTURE.md)):

| Layer | Implementation |
|---|---|
| Data | PostgreSQL 18 (pgvector, btree_gist). Append-only, hash-chained evidence ledger. FORCE row-level security per tenant. Time axes: recorded, valid, extracted, ingested, knowledge point K |
| Ingestion | File-drop connector: release-gated, explicit owner-authorised admission, one sealed batch per pack. Isolated parsing: size, type and archive limits, and a time-boxed parser process |
| Authorisation | Evidence = knowledge boundary ∩ current authorisation ∩ policy (ReBAC tuples, ABAC `abac/1`, historical ACL). The disclosure auditor gates protected aggregates (payroll release is **off**) |
| Answers | Ask pipeline: refusal pre-screen, temporal parse, deterministic planner, governed metric tools, verifier, grade |
| Agents | One shared runtime; agents are versioned configuration; five-tier memory isolated per interviewer |
| Workflows | DBOS 3.5.0 durable workflows (separate system database) |
| Model client | Governed client: policy, redaction, hard token budgets, record/replay. **Default DENY_ALL: no model is called** |
| API | FastAPI, versioned JSON (`/v1`). Gateway controls: security headers, body limits, rate limits |
| Cockpit | React 18 + Vite 6 + TypeScript (`frontend/`); dark plum design system, self-hosted IBM Plex fonts; rules in `.claude/skills/no-ai-slop-frontend` |

**What works in the local demo** (synthetic data):
- Today view and briefing;
- Ask, with citations, evidence drawer, grades and refusals;
- conflict register;
- timeline;
- scenarios ("what if a commitment moves") with dependencies and early warnings;
- department views;
- access view;
- an interview with a labelled synthetic agent, with memory and session history;
- privacy refusals.

**Blocked by approvals or release dates** (shown as blocked in the product):

| Item | Blocked by |
|---|---|
| Real employee agents | All 14 slots are unassigned and blocked until the owner maps them |
| Ranked "top three" | D-15 ranking values not set; the watchlist stays unranked |
| Payroll figures | Disclosure release not approved |
| Model-composed answers | No model provider is selected |
| Later release packs | Each pack is admitted only after its release time **and** the owner's admission authorisation |
| Engineering-continuity journey | Its sources are in a later pack that is not admitted |

## 2. Prerequisites

Versions as found in this repository and as tested:

| Tool | Requirement | Tested with |
|---|---|---|
| Python | 3.12 (`requires-python >= 3.12`; container image `python:3.12-slim`) | 3.12.10 |
| Node.js / npm | Node 24 (as in CI) and the npm that ships with it | Node 24.14.0, npm 11.9.0 |
| PostgreSQL | 18, with `pgvector` and `btree_gist` (image `pgvector/pgvector:pg18`) | 18.7 |
| Docker | Docker with Compose v2: needed for the bundled PostgreSQL and the one-command run | Docker 29.0.1, Compose 2.40.3 |
| PostgreSQL client tools | ≥ 18; only for `veloxa backup` / `restore-drill`. A local bin directory, or `docker:<container>` | via `docker:veloxa-cockpit-db-1` |

Python packages are pinned in [`requirements.lock`](requirements.lock). Frontend packages are pinned in [`frontend/package-lock.json`](frontend/package-lock.json).

## 3. Repository setup (Windows PowerShell)

```powershell
cd C:\path\to\veloxa                      # the repository (or worktree) root
py -3.12 -m venv .venv                    # once
.\.venv\Scripts\Activate.ps1              # if blocked: Set-ExecutionPolicy -Scope Process Bypass
python --version                          # Python 3.12.x
pip install --constraint requirements.lock -e ".[dev]"
cd frontend; npm ci; cd ..                # frontend dependencies from the lockfile
veloxa --help                             # lists the commands
```

Linux and macOS: `python3.12 -m venv .venv && . .venv/bin/activate`, then the same `pip` and `npm ci` commands.

## 4. Environment configuration

**Files:**
- [`.env.example`](.env.example) lists every setting, with **placeholders only**.
- Copy it to `.env` for local values. `.env` is git-ignored and never committed; CI and the container image do not read it.

```powershell
Copy-Item .env.example .env               # then edit .env in your editor
. .\scripts\load-env.ps1                  # loads .env into THIS PowerShell session; prints names, never values
```

Docker Compose also reads `.env` automatically.

| Setting | Used for |
|---|---|
| `VELOXA_DATA_PATH` | The extracted release-pack folder (the one containing `Day1_Mon_16Nov`). Mounted read-only; never copied into the repository |
| `VELOXA_DB_HOST`, `VELOXA_DB_PORT`, `VELOXA_DB_NAME` | Which database the `veloxa` CLI and API use (defaults `127.0.0.1`, `5432`, `veloxa`). The Compose database is published on `127.0.0.1:55432` |
| `VELOXA_DB_ADMIN_USER` / `_PASSWORD`, `VELOXA_MIGRATOR_PASSWORD`, `VELOXA_INGEST_PASSWORD`, `VELOXA_PROJECTION_PASSWORD`, `VELOXA_APP_PASSWORD` | Role credentials. The Compose defaults (`local-dev-*`) suit a localhost-only database. `veloxa serve` refuses to start if any privileged credential is in its environment |
| `VELOXA_TEST_ADMIN_DSN` | An administrator DSN for **disposable** databases (tests and the demo), e.g. `postgresql://veloxa_admin:<password>@127.0.0.1:55432/postgres` |
| `VELOXA_DEV_AUTH=1`, `VELOXA_DEV_TOKEN`, `VELOXA_DEV_PRINCIPAL` | **Local development sign-in fixture** (all three, or the API refuses to start). The token is a random secret of 32 or more characters that you choose; the principal is an employee ID from the HR export |
| `VELOXA_WORKFLOW_DATABASE_URL` | Optional: the DBOS system database (a **separate** database) for durable workflows |
| `VELOXA_RATE_LIMIT_PER_MIN` (120), `VELOXA_RATE_LIMIT_EXPENSIVE_PER_MIN` (10) | Gateway limits. These are development defaults and a proposed pilot value, **not** approved production limits |

**Model provider and API key.**
- **No real model provider is selected or implemented**, so **there is no API-key variable**. Do not create one: an unknown variable has no effect.
- The only implemented adapter is `replay`, which serves recorded responses with no network and no key.
- The model stays **disabled (DENY_ALL)** unless *all* of these are set: `VELOXA_MODEL_PROVIDER` (an implemented adapter), `VELOXA_MODEL_NAME`, `VELOXA_MODEL_STAGES`, `VELOXA_MODEL_ALLOWED_CLASSES` and `VELOXA_MODEL_MAX_ESTIMATE_TOKENS`, plus `VELOXA_MODEL_REPLAY_DIR` for `replay`.
- **Budgets:** even when configured, every call is refused unless the owner has set a token-budget row (`runtime.llm_budget`) for the tenant.
- **Check the state** at any time. It names missing settings, never values:
  ```powershell
  (Invoke-RestMethod http://127.0.0.1:8000/health).model
  # model  reason
  # disabled  no provider configured (DENY_ALL); facts-only composition
  ```
- **When the owner approves a provider:** its adapter will be added together with exactly one key variable named after that provider's convention, which you then put in `.env` (never in code, never in a chat or prompt). Until then agents use facts-only composition.

**Demo mode vs real mode:**

| | Demo mode | Real mode |
|---|---|---|
| Data | Synthetic pack generated in code | Your release packs (`VELOXA_DATA_PATH`) |
| Database | A disposable `veloxa_demo_<8 hex>`; dropped by a clean exit, by the next start, or by `--cleanup` | The Compose `veloxa` database |
| Sign-in | A fixed synthetic fixture token, printed at start | The fixture you configure in `.env` |
| API | `http://127.0.0.1:8765` | `http://127.0.0.1:8000` |

### Sign-in (single sign-on)

Browser sign-in is an OpenID Connect authorization code flow with PKCE, run by the API (the browser never holds an
ID token). The returned ID token is verified against the **tenant's registered issuer** (`veloxa tenant add-issuer`:
signature via JWKS, issuer, audience, expiry) plus `state` and `nonce`, and mapped to an invited member by
`(issuer, subject)`. The tenant and role are never taken from the browser or from token claims.

| Setting | Meaning |
|---|---|
| `VELOXA_OIDC=1` | Per-tenant ID-token verification (required for any provider) |
| `VELOXA_PUBLIC_URL` | The cockpit's public origin; the redirect URI is `<origin>/v1/auth/callback/<provider>` |
| `VELOXA_SSO_ENTRA_TENANT`, `VELOXA_SSO_ENTRA_CLIENT_ID`, optional `VELOXA_SSO_ENTRA_CLIENT_SECRET` | Microsoft Entra ID (issuer `https://login.microsoftonline.com/<tenant>/v2.0`; register it for the workspace with the client ID as audience) |
| `VELOXA_SSO_GOOGLE_CLIENT_ID`, `VELOXA_SSO_GOOGLE_CLIENT_SECRET`, optional `VELOXA_SSO_GOOGLE_HD` | Google Workspace (optional; shown only when both are set) |
| `VELOXA_SSO_REQUIRE_MFA=1` | Refuse sign-ins whose ID token does not report MFA (`amr`); enforce MFA in the identity provider |
| `VELOXA_DEMO_LOGIN=1` | Local demo entry (with the development fixture only; never on a shared deployment) |

New members sign in through their invitation link (`#/signin?invite=<token>`). Sessions are server-side: 8 hours
absolute, 30 minutes idle, `HttpOnly; SameSite=Lax` cookies (`Secure` behind HTTPS); cookie-authenticated writes need
the `X-Veloxa-Request` header. Sessions are held in the API process, so restarting it signs everyone out.
**Not yet verified against a live Entra or Google tenant:** no client ID exists for this project, so the flow is tested
end to end against a fake identity provider with synthetic keys (`tests/test_login.py`).

## 5. Database setup

**Start PostgreSQL** (the Compose `db` service, published on `127.0.0.1:55432`):

```powershell
$env:VELOXA_DATA_PATH = "C:\path\to\Veloxa_Hackathon_Release_Packs"   # Compose requires it to be set
docker compose up --detach --wait db
```

**Which database each command uses:**

| Command | Database |
|---|---|
| `python scripts\demo_env.py` | A new `veloxa_demo_<8 hex>` on the server in `VELOXA_TEST_ADMIN_DSN`; dropped at exit or by `--cleanup` |
| `pytest` | New `veloxa_test_<random>` databases on the server in `VELOXA_TEST_ADMIN_DSN`; dropped at the end |
| `docker compose up` (`init` then `api`) | `veloxa` in the Compose volume `pgdata` |
| `veloxa <command>` | `VELOXA_DB_NAME` on `VELOXA_DB_HOST:VELOXA_DB_PORT` |

> **Warning: this branch adds migrations 0013–0021** (0017 workspace, 0018 data room, 0019 text chunks, 0020 threads and agent updates, 0021 company intelligence). `docker compose up` and `veloxa migrate` apply them to the database they point at. If your Compose volume holds a database you want to keep at an earlier migration, **do not** run them against it. Use a separate Compose project with its own volume and ports instead. Example (tested):
> ```powershell
> $env:VELOXA_DB_PUBLISH_PORT = "55433"; $env:VELOXA_API_PORT = "8001"
> docker compose -p veloxa-trial up --build --detach --wait
> ```
> Migrations are forward-only: take `veloxa backup` (and run `veloxa restore-drill`) before upgrading a database you keep.

**Migrate, admit, seed and self-check with the CLI** (against the database in `VELOXA_DB_*`; tested on a separate trial database):

```powershell
$env:VELOXA_DB_HOST = "127.0.0.1"; $env:VELOXA_DB_PORT = "55432"; $env:VELOXA_DB_NAME = "veloxa"
. .\scripts\load-env.ps1                  # role passwords from .env (or set them in the session)
veloxa migrate                            # roles, extensions, migrations; "none (up to date)" on a second run
veloxa ingest --pack D1                   # admits only released packs listed in config/admission_authorisations.toml
veloxa project                            # builds projections for sealed batches
veloxa agents-seed                        # publishes the 14 agent slots, unassigned and blocked
veloxa selfcheck --role app               # self-check: PASS (role veloxa_app): RLS, FORCE, policies, grants
veloxa selfcheck --role ingest
veloxa status                             # clock, sealed watermark K, each pack's state
```

**Verify RLS.** Both self-checks fail if any table lacks ENABLE + FORCE RLS or its restrictive tenant policy. The database guarantees are tested in `tests/test_ledger_db.py` (see section 8).

**Synthetic demo data** is created by `scripts\demo_env.py` (section 6). Do not use the real database for demos.

## 6. Starting Veloxa

### Hackathon submission: one command from a fresh clone

Branch **`feat/veloxa-sprints-7-10`** holds the submitted work (`main` is not updated). From a fresh clone of that
branch, with Docker running and the release packs extracted locally (never committed):

```bash
VELOXA_DATA_PATH=/path/to/Veloxa_Hackathon_Release_Packs docker compose --profile hackathon up --build --detach --wait
```

PowerShell: `$env:VELOXA_DATA_PATH = "C:\path\to\Veloxa_Hackathon_Release_Packs"; docker compose --profile hackathon up --build --detach --wait`

This starts PostgreSQL, runs `init` (migrations and the Day 1 baseline, unchanged), then
`veloxa hackathon-bootstrap`, which:

- admits each pack **in release order** only if it is **released** (release time has passed) **and authorised
  source by source** in `config/admission_authorisations.toml` (K-13); an unreleased pack is refused before any of
  its files is opened, and an unauthorised one is refused and written to the audit log;
- builds projections and claims, grounds the 14 agents in the HR export (owner-approved mapping by exact employee
  ID), and records the CEO's explicit company-wide read (from the HR title, server-side, audited);
- runs the self-check. Re-running it is safe.

The API is on `http://127.0.0.1:8000` (`/health`, `/v1/meta/releases`). To use the cockpit, enable the local
developer sign-in in `.env` (section 4) and start the frontend (below). The plain `docker compose up` stack keeps
its general behaviour: it never admits a later pack.

### Demo (recommended first run): synthetic data, two terminals

```powershell
# Terminal 1: the API on a disposable synthetic database
.\.venv\Scripts\Activate.ps1
$env:VELOXA_TEST_ADMIN_DSN = "postgresql://veloxa_admin:local-dev-admin@127.0.0.1:55432/postgres"
python scripts\demo_env.py --port 8765
#  -> demo database veloxa_demo_xxxxxxxx ready; token synthetic-demo-fixture-token-...
```

```powershell
# Terminal 2: the cockpit (Vite dev server, proxies /v1 and /health to the API)
cd frontend
$env:VELOXA_API_URL = "http://127.0.0.1:8765"
npm run dev
#  -> Local: http://127.0.0.1:5173/
```

**Start order:** PostgreSQL, then the API, then the frontend.

**Notes:**
- The demo's `/health` shows `degraded` while a released pack (D2) awaits authorisation. This is expected.
- No separate worker is needed: DBOS workflows run in the process that starts them (section 8).

### Demo Lab (Profile B): a fully populated fictional company

The Demo Lab builds **Lumora Systems (DEMO)**, a synthetic company: 22 generated files, 14 synthetic agents with updates, and departments. All of it goes through the real data-room pipeline (submission, validation, review, authorisation, admission). It uses its **own** database and never touches `veloxa` or the hackathon demo.

```powershell
# Terminal 1: build once, then serve
.\.venv\Scripts\Activate.ps1
$env:VELOXA_TEST_ADMIN_DSN = "postgresql://veloxa_admin:local-dev-admin@127.0.0.1:55432/postgres"
python scripts\demo_lab.py --db veloxa_demolab --create --originals $env:TEMP\veloxa-demolab-originals
python scripts\demo_lab.py --db veloxa_demolab --serve --port 8770 --originals $env:TEMP\veloxa-demolab-originals
#  -> Demo Lab API on http://127.0.0.1:8770; token synthetic-demo-lab-fixture-token-0123456789 (demo CEO)
```

```powershell
# Terminal 2: the cockpit against the Demo Lab
cd frontend
$env:VELOXA_API_URL = "http://127.0.0.1:8770"
npm run dev                               # http://127.0.0.1:5173
```

**Notes:**
- **Allowlisted database names only.** The script accepts only `veloxa_demolab` or `veloxa_demolab_<suffix>` and refuses anything else.
- `--create` refuses an existing database. To rebuild, either choose a new suffix (for example `veloxa_demolab_2`) or drop the old Demo Lab database yourself.
- Use the **same** `--originals` folder for create and serve. That folder holds the uploaded original files that *Open original* and *Download original* serve.
- `/health` may show the primary tenant's schedule; this is a known limitation.

### Real mode: your release packs

```powershell
Copy-Item .env.example .env               # set VELOXA_DATA_PATH, VELOXA_DEV_AUTH=1, VELOXA_DEV_TOKEN, VELOXA_DEV_PRINCIPAL
docker compose up --build --detach --wait # db, then init (migrate, baseline admission, self-check), then api on :8000
Invoke-RestMethod http://127.0.0.1:8000/health
cd frontend; npm run dev                  # VELOXA_API_URL defaults to http://127.0.0.1:8000
```

Read the migration warning in section 5 first.

**Production-like build of the cockpit:** `npm run build`, then `npm run preview`, served at `http://127.0.0.1:4173`.

| URL | What |
|---|---|
| `http://127.0.0.1:5173` | Cockpit, development server |
| `http://127.0.0.1:4173` | Cockpit, preview of the production build |
| `http://127.0.0.1:8765` | Demo API |
| `http://127.0.0.1:8000` | API (Compose or `veloxa serve`) |
| `127.0.0.1:55432` | PostgreSQL (Compose) |

### Company intelligence (the supplied company, D1–D3)

The real release packs are loaded into an **isolated, allowlisted lab database** (`veloxa_company[_x]`) through the
governed pipeline. The `veloxa` database is never migrated by this.

```powershell
$env:VELOXA_DATA_PATH = "C:\path\to\Veloxa_Hackathon_Release_Packs"
$env:VELOXA_TEST_ADMIN_DSN = "postgresql://veloxa_admin:local-dev-admin@127.0.0.1:55432/postgres"
python scripts\company_lab.py --dry-run D3                      # parse + contracts + K-13 source check, no database
python scripts\company_lab.py --db veloxa_company --create --admit --review   # released + authorised packs only
python scripts\company_lab.py --db veloxa_company --serve --port 8780         # local fixture token printed
python scripts\company_lab.py --inventory inventory.json        # hash, rule and release state of every Day file
```

**How it works:**
- **Admission:** source by source (K-13). Every file of a pack must be listed in `config/admission_authorisations.toml`.
  A pack is never opened before its release time. **Day 4 is embargoed until 19 Nov 2026 10:00 IST.**
- **New views:**
  - claims and the Conflict Registry: `/v1/knowledge/conflicts`;
  - the 14 grounded people with labelled persona assumptions: `/v1/people`;
  - the D-15 weighted worry list: `/v1/worry`, page *What to worry about*;
  - findings: `/v1/findings`;
  - connector status: `/v1/admin/connectors`.
- **D-15 file:** the D-15 v1 decision file is private (`VELOXA_D15_POLICY`). Record it in a lab database only.

**Acceptance suite (one command, synthetic, CI-safe):** `python scripts\run_acceptance.py`. 40 cases (13 on
uncertainty, refusal or conflict); a non-zero
exit code on any failure; JSON and Markdown reports.

**Gemini wording experiment** (synthetic Lumora data only; the result was *do not adopt*):
`pip install -e ".[gemini]"`, then `python scripts\gemini_ab.py --env-file <path with GEMINI_API_KEY>`. The key stays
on the backend; the supplied company's tenant is refused by name.

## 7. First-time demonstration

Use the Demo Lab (section 6) for the fullest tour; the hackathon demo (`demo_env.py`, token `synthetic-demo-fixture-token-0123456789abcdef`) works the same way with less data.

1. Open `http://127.0.0.1:5173`.
2. **Sign in:** the sign-in screen opens first.
   - **Demo Lab:** choose **Enter Demo Workspace**. It appears only when the API runs locally with the development
     fixture and `VELOXA_DEMO_LOGIN=1` (the Demo Lab launcher sets it), only from this machine, and only for the
     synthetic Demo Lab workspace. No token is shown; the session is an HttpOnly cookie.
   - **Hackathon demo and company lab:** open **Developer access (local only)** and paste the fixture token printed
     by the API. It is kept for this tab only.
   - **Single sign-on** (Microsoft Entra ID; Google Workspace optional) appears only when configured: see
     *Sign-in* in section 4. Sign out from the top bar or **Admin > Account**.
3. **Overview:**
   - KPI cards and charts computed only from admitted rows you may read (each says what it was computed from);
   - **Recent Agent Updates**, labelled DEMO.
4. **Ask Veloxa:**
   - the search field in the top bar, the *Ask Veloxa* button, or **Ctrl+K** opens a window over the page; the expand icon (or *Ask Veloxa* in the sidebar, `#/ask`) turns the same conversation into a full-screen workspace, and Back returns to the window;
   - try *What is our headcount by department?* or *What happened to Quartz 2.0?*;
   - answers show status, grade, interpretation, highlighted matches and numbered claims;
   - every question is saved as a conversation: search and reopen it from the conversation list (the menu icon on narrow screens).
5. **Contextual Ask:** open an agent update on the Overview and choose *Ask about this*. Follow-ups keep that context.
6. **Evidence drawer:** select a claim's source number. It shows source, location, version and batch K, classification, admission, your access, *Open original* and *Download original* (audited).
7. **Evidence & Data:**
   - upload a file;
   - run the dry-run validation;
   - submit for review.

   Review and authorisation are separate steps, and nothing is admitted until both happen.
8. **Agents / Departments:** browse the 14 synthetic agents and their updates.
9. **Intelligence:** **Ask a department** (a clarification request; Veloxa contacts nobody), conflicts (sources side by side, no side chosen), source coverage, and what to watch (unranked until D-15).
10. **What-if:** move a commitment by N days. Observed, derived, assumption and unknown stay separate, and no probability is invented.
11. **Timeline:** record events, with a "known as at" date selector.
12. **Privacy refusal:** ask *What is Person Beta's salary?* (hackathon demo). It is refused with a policy, a reason and an alternative.

Everything is **facts-only**: no model writes any answer (DENY_ALL).

## 8. Testing

```powershell
$env:VELOXA_TEST_ADMIN_DSN = "postgresql://veloxa_admin:local-dev-admin@127.0.0.1:55432/postgres"
python -m pytest -q                        # all Python tests; pass = "N passed", exit code 0
python -m pytest -q -m "not realdata"      # what CI runs (synthetic only)
ruff check .                               # lint + SAST (bandit rules); pass = "All checks passed!"
python scripts\public_scan.py              # confidentiality scan; any finding exits 1
python scripts\dependency_audit.py         # pip-audit (throwaway venv) + npm audit; pass = "python clean, frontend clean"
cd frontend
npm run build                              # type-check + production build; exit 0
$env:VELOXA_PYTHON = (Resolve-Path ..\.venv\Scripts\python.exe).Path
npx playwright install chromium            # once
npx playwright test                        # browser journeys on desktop, tablet, mobile; pass = "60 passed"
cd ..
```

| Focus | Command |
|---|---|
| DBOS durable workflows, including a real crash and recovery | `python -m pytest -q tests\test_workflows_dbos.py` |
| Backup and restore drill | `python -m pytest -q tests\test_ops_backup.py` (needs `VELOXA_PG_TOOLS`, e.g. `docker:veloxa-cockpit-db-1`) |
| Migrations (idempotent; self-checks) | `python -m pytest -q tests\test_ledger_db.py` |
| Real-data checks (local only) | Set `VELOXA_DATA_PATH`; `python -m pytest -q -m realdata` |
| Gold questions (cases are private) | Set `VELOXA_GOLD_PATH`; `python -m pytest -q tests\gold` |

Always read the exit code (`$LASTEXITCODE`). Never judge a run by piping it through a filter.

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| `connection refused` on 55432 | Start PostgreSQL: `docker compose up --detach --wait db`. Use `127.0.0.1`, not `localhost` (on Windows `localhost` tries IPv6 first) |
| A command touched the wrong database | Check `$env:VELOXA_DB_NAME` / `VELOXA_DB_PORT` (CLI) or `VELOXA_TEST_ADMIN_DSN` (tests, demo). See the table in section 5 |
| `migration … changed after it was applied` | An applied migration file was edited. Never edit applied migrations; restore the file and add a new one |
| `Port … already in use` / Vite `strictPort` error | Stop the other process (`Get-NetTCPConnection -LocalPort 5173` shows its owner) or choose another port (`--port` for the APIs) |
| `veloxa` not found / wrong Python | Activate `.venv`; `(Get-Command python).Source` must point into it |
| `Cannot find module` in the frontend | `cd frontend; npm ci` |
| Model shows `disabled` | Expected (DENY_ALL). `/health` lists any missing setting. There is no API-key variable today |
| A model call is refused for budget | No owner-set `runtime.llm_budget` row for the tenant and month. This is by design |
| `VELOXA_WORKFLOW_DATABASE_URL … is required` | Durable-workflow commands need a separate DBOS database (see `docs/workflows/WORKFLOWS.md`) |
| The API refuses to start: development fixture | All three `VELOXA_DEV_*` settings are needed; the token must be ≥ 32 characters |
| 401 in the cockpit | Sign in with the token; with real mode, the `.env` fixture must be enabled |
| Playwright cannot start its servers | Ports 8765 and 4173 must be free; `VELOXA_TEST_ADMIN_DSN` and `VELOXA_PYTHON` must be set; run `npm run build` first |
| `refused: ... not an allowlisted Demo Lab database` | Use `--db veloxa_demolab` or `veloxa_demolab_<suffix>` |
| Demo Lab `--create` says the database exists | Serve it with `--serve`, or create a new suffix such as `veloxa_demolab_2` |
| *Open original* fails in the Demo Lab | Serve with the same `--originals` folder that was used for `--create` |
| Leftover `veloxa_demo_*` databases | `python scripts\demo_env.py --cleanup` (drops only exact `veloxa_demo_<8 hex>` names) |

## 10. Stop and cleanup

```powershell
# Ctrl+C in each terminal stops the demo API and the Vite server. Then, always:
python scripts\demo_env.py --cleanup       # drops any demo database the stop left behind (exact veloxa_demo_<8 hex>)
# The Demo Lab database (veloxa_demolab) is kept on purpose so it can be served again; drop it yourself when done.
docker compose stop                        # stops db/api, keeps the data volume
docker compose -p veloxa-trial down --volumes   # removes a TRIAL project and its volume (never your main volume)
```

**Preserve:**
- **Never** run `docker compose down --volumes` on a volume holding data you need: it deletes the database.
- Other `veloxa_test_*` databases you did not create are left alone. Ask their owner before dropping any.

**Temporary files:** browser-test output is in `frontend\test-results` and `frontend\playwright-report`; both are safe to delete.

## 11. Security warnings

- **Never use production or real personal data in the demo.** The demo builds synthetic data only.
- **Never commit `.env`** or any key. Do not paste keys into chats, prompts, issues or logs.
- **Never disable or weaken the confidentiality scans** (`scripts/public_scan.py`, CI gates).
- **Never expose the database publicly.** Compose binds to `127.0.0.1` only. The `local-dev-*` passwords are for a localhost-only database.
- **Never enable payroll access** or record a disclosure release without the owner's authorisation.
- **Never bypass admission rules.** A pack is admitted only after its release time **and** an owner authorisation in `config/admission_authorisations.toml`. A passed date alone is not enough.
- **Never enable the development sign-in fixture** on a shared or production deployment.

## 12. Architecture and operational references

| Document | Contents |
|---|---|
| [`LOCAL_DEVELOPMENT.md`](LOCAL_DEVELOPMENT.md) | Command line, API contract, module map, more troubleshooting |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Approved reference architecture; hackathon profile (§25); deferred decisions (§26) |
| [`DECISIONS.md`](DECISIONS.md), [`ASSUMPTIONS.md`](ASSUMPTIONS.md), [`TRANSCRIPT_NOTES.md`](TRANSCRIPT_NOTES.md), [`AI_LOG.md`](AI_LOG.md) | Decisions, assumptions, transcript notes and the AI log |
| [`docs/agents/`](docs/agents/) | Agent runtime, registry, tools and memory |
| [`docs/workflows/WORKFLOWS.md`](docs/workflows/WORKFLOWS.md) | DBOS durable workflows, retention (dry run until approved) |
| [`docs/operations/`](docs/operations/) | Load and concurrency results, PITR/WAL design, container security, retention and erasure matrix |
| [`docs/DEPENDENCIES.md`](docs/DEPENDENCIES.md) | Dependency inventory and audit |
| [`docs/D-15_owner_decision_sheet.md`](docs/D-15_owner_decision_sheet.md) | The owner's pending risk-ranking decisions |

**Public-repository safety.**
- No raw challenge data, personal names, employee identifiers, pay details or financial figures appear in this repository.
- Evidence is cited by opaque references.
- Content depending on a release pack is added only after that pack's release.

AI assistants are used throughout. Every main task and correction is logged in [`AI_LOG.md`](AI_LOG.md). The project owner is responsible for everything here.
