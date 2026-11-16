# Veloxa CXO Cockpit

An evidence-grounded executive intelligence cockpit for **Veloxa Technologies**, a synthetic company created for the Hiyaku Systems Hackathon (16–20 Nov 2026).

The target product answers a CEO's questions with:
- short answers that **cite the records behind them**;
- an **evidence grade** for each answer;
- honest **"I don't know"** or **"the sources disagree"** when that is the truth.

It also:
- ranks **what to worry about**, with evidence;
- answers **"what did we know on date X?"** without leaking later material;
- lets you interview **14 employee agents** grounded in the records.

The design is a production-first reference architecture, approved as baseline v1.0.1 in [`ARCHITECTURE.md`](ARCHITECTURE.md). The hackathon build is a configuration of it (§25), and is implemented incrementally.

## Status (Mon 16 Nov 2026, Day 1)

| Area | State |
|---|---|
| Architecture | Approved baseline v1.0.1 |
| **Evidence ledger foundation** | **Working and tested.** PostgreSQL 18 schemas and roles, append-only ledger, FORCE RLS with tenant isolation, startup self-checks, hash-chained audit, least-privilege process separation |
| **Day 1 ingestion** | **Working and tested.** The D1 release pack is admitted as sealed batch 1 (15 files, 112 source records) with content hashes, exact locators and release-time admission. D2–D4 are refused before their release time without being read |
| HTTP API | Health and time metadata only (`/health`, `/v1/meta/clock`, `/v1/meta/releases`) |
| Asking questions, risk briefing, agents, gold tests | **Not implemented yet** (Days 2–4) |
| Frontend | **Deferred.** Technology not chosen; the backend stays frontend-independent (`ARCHITECTURE.md` §19, §26.2) |

## Quick start (one command)

**Prerequisites:** Docker with Docker Compose v2, and the hackathon release packs, extracted to a local folder. That folder contains `Day1_Mon_16Nov`, `Day2_Tue_17Nov`, and so on. The challenge data is **never** stored in this repository. You supply its location at run time, and it is mounted read-only.

```bash
# macOS / Linux
VELOXA_DATA_PATH=/path/to/Veloxa_Hackathon_Release_Packs docker compose up --build
```

```powershell
# Windows PowerShell
$env:VELOXA_DATA_PATH = "C:\path\to\Veloxa_Hackathon_Release_Packs"; docker compose up --build
```

Alternatively, copy `.env.example` to `.env` (never committed), set `VELOXA_DATA_PATH`, and run `docker compose up --build`.

**What starts.** Three services, with least privilege (§9.4):

| Service | Holds | Does |
|---|---|---|
| `db` | — | PostgreSQL 18 with pgvector and btree_gist |
| `init` (one-shot) | The administrator, migrator and ingest credentials; the release packs mounted read-only | Creates roles and extensions, applies migrations, and admits every release pack whose release time (10:00 IST on its day) has passed. Then exits. A pack that fails admission is logged and audited, and `init` still succeeds if an earlier batch is sealed (DEC-24). `init` fails if the data folder is missing, a migration fails, the security self-check fails, or no sealed baseline batch exists (e.g. a fresh database whose D1 failed) |
| `api` (long-running) | **Only** the application-role password; **no** data mount | Starts after `init` succeeds; serves `http://127.0.0.1:8000`. It refuses to start if any privileged credential is in its environment |

If a released pack fails admission, the API keeps serving the latest sealed batch, and reports the pack as `pending_admission` with health `degraded`. Unsealed data is never exposed. If `VELOXA_DATA_PATH` is not set, Compose stops with an explanatory message. Restarting is safe: an admitted pack is recognised by its manifest hash and never admitted twice, including when two admission runs race.

**Check it:**

```bash
curl http://127.0.0.1:8000/health             # {"status":"ok","selfcheck":"pass",...}
curl http://127.0.0.1:8000/v1/meta/releases   # D1 admitted; later packs not yet released
```

The database and API listen on `127.0.0.1` only. The Compose file contains local-development passwords for that localhost-only database. Override them in `.env` for anything else.

## Running the tests

The tests need a PostgreSQL 18 server. The Compose `db` service works:

```bash
python3.12 -m venv .venv && . .venv/bin/activate        # Windows: py -3.12 -m venv .venv; .venv\Scripts\activate
pip install --constraint requirements.lock -e ".[dev]"
VELOXA_DATA_PATH=/path/to/packs docker compose up -d --wait db
export VELOXA_TEST_ADMIN_DSN="postgresql://veloxa_admin:local-dev-admin@127.0.0.1:55432/veloxa"
ruff check .
pytest                       # everything (67 tests), including the real-data test if VELOXA_DATA_PATH is set
pytest -m "not realdata"     # what CI runs: synthetic data only
```

- Use `127.0.0.1`, not `localhost`. On Windows, `localhost` tries IPv6 first and stalls.
- Each test session creates and drops its own throwaway database.
- Test data is **synthetic** (`tests/synthetic_pack.py`).
- The real-data test (`tests/test_d1_realdata.py`) runs only locally. It checks counts, data classes and content hashes against the real Day 1 pack, never values.

The automated test set of at least 25 gold questions (H-15, H-16) does not exist yet. It depends on the answering pipeline (Days 2–4).

## Command line

Inside the container (`docker compose exec app veloxa <command>`) or in the virtual environment:

| Command | Purpose |
|---|---|
| `veloxa migrate` | Create roles and extensions; apply migrations once, in order |
| `veloxa ingest [--pack D1\|released]` | Admit release packs from `VELOXA_DATA_PATH` (default: every released pack) |
| `veloxa status` | Show the clock, the sealed watermark K and each pack's admission state |
| `veloxa selfcheck --role app\|ingest` | Startup self-checks (§9.3) |
| `veloxa init` | Privileged one-shot step: migrate, admit released packs, self-check (needs the privileged credentials) |
| `veloxa serve` | Run the API with the application role only |

`VELOXA_CLOCK` is a **test-only** setting. It is honoured only together with `VELOXA_TEST_MODE=1`, can move the clock back but never forward, and setting it outside test mode is an error.

## What exists

| Path | Contents | Architecture |
|---|---|---|
| `src/veloxa/migrations/` | Bootstrap (role set, `btree_gist`, `pgvector`); `0001_foundation.sql` (schemas, tenant directory, admission batches, raw manifest, source records, audit chain, triggers, RLS, grants); `0002_hardening.sql` (record-to-manifest hash key, time-ordering checks, protected-pay policy). Applied migrations are never rewritten | §6, §8.2, §9.3, §9.4, §15, §21.3 |
| `src/veloxa/ingest/` | File-drop connector: manifest (SHA-256 per file), versioned parsers (xlsx rows, docx, pdf, eml, txt), single-transaction admission and sealing | §7, §8.2 |
| `src/veloxa/releases.py`, `config/release_schedule.toml` | Release schedule and clock; admission decided before any file is opened | §8.2, §25.1 |
| `config/source_catalogue.toml` | Classification of each pack file (kind, source system, data class). Unknown files are rejected | §6.3, §7.2 |
| `src/veloxa/ledger.py` | Visibility at an explicit knowledge point K, the sealed watermark, mapping dates to K | §8.3, §8.4 T1 |
| `src/veloxa/selfcheck.py` | Startup self-checks: role attributes, ownership, RLS/FORCE/restrictive policy, `security_invoker` views, server version | §9.3 |
| `src/veloxa/api.py`, `src/veloxa/cli.py` | API (health and time metadata) and command line | §19 |
| `tests/` | 67 tests: release rules and clock, parsers and manifest, database guarantees, concurrency, API, availability on failed admission, real-data check | §21.2 |
| `Dockerfile`, `docker-compose.yml`, `.github/workflows/ci.yml` | One-command run; CI with lint, tests and a fresh-clone Compose job | §25.1, H-30 |

## Guarantees enforced today (and tested)

- **Release gating.** A pack is admitted only at or after its release time, in order, as one sealed batch. An unreleased pack's folder is never opened.
- **Append-only history.** Source records, the raw manifest and audit events reject UPDATE, DELETE and TRUNCATE. The only change a batch accepts is being sealed, once. Nothing can be added to a sealed batch.
- **Provenance.** Each file is read once, and the same bytes are hashed and parsed. Every record carries:
  - the source path;
  - the SHA-256 of those bytes, which the database requires to equal the raw manifest's hash for that file;
  - a locator (sheet and row, paragraphs, pages or message);
  - the parser version;
  - the release batch and the data class.
- **Distinct times.** `release_at` (scheduled release, the knowledge time), `extracted_at` (when each file was actually read), `admitted_at` and `sealed_at` are separate. The database enforces `release_at ≤ extracted_at ≤ admitted_at ≤ sealed_at`.
- **Tenant isolation.** FORCE RLS with a restrictive tenant policy (USING and WITH CHECK) and transaction-local context. A missing or empty context fails closed.
- **Protected pay.** The application role, used by every API route, can read neither individual payroll rows nor payroll aggregates. Aggregates will be released only through the disclosure auditor (§15), which is not built yet, so they are unreadable for now. A startup self-check fails if the policy is removed or weakened.
- **Least privilege.** The API process never receives the administrator, migrator or ingest credentials.
- **Visibility.** Reads at knowledge point K see only sealed batches up to K. A K beyond the sealed watermark is rejected.
- **No interpretation at ingestion.** Parsers record what the files say. No source-truth, identity or conflict decision is made; those stay with the owner (`TRANSCRIPT_NOTES.md`).

## Current limitations

- Ingestion runs in the app container's start-up sequence, before the API starts. It does not yet use the durable workflow runtime (DBOS) or an isolated document-parsing sandbox (§7.2, §17).
- There is no authentication on the API yet; it binds to `127.0.0.1` only and returns metadata, never record values.
- Not built yet: recorded-time, valid-time and query-mode logic (KNOWN_AT, RECORDED_BY, VALID_AT); knowledge projections; entity resolution; ReBAC/ABAC adapters; and the historical ACL evaluator.
- Release packs must be supplied as an extracted folder, not as the zip file.
- Each pack is held in memory while it is hashed and parsed. This suits the hackathon pack sizes; large packs need streaming.
- From a later pack's release time, `init` tries to admit it. Until that pack's parsers and catalogue rules are added (Day 2 onward), its admission is refused, so the API serves the earlier sealed batches and reports the pack as pending.
- A database created before migration `0002` keeps its earlier `extracted_at` values (history is append-only). Recreate a local development database with `docker compose down --volumes` to get the corrected values.
- The CI workflow has been validated locally (actionlint, plus the same commands). It runs on GitHub after the next push.

## Documentation

| File | Contents |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Approved reference architecture, hackathon profile (§25), deferred decisions (§26), decision register (§27) |
| [`DECISIONS.md`](DECISIONS.md) | Choices that could be defended either way, kept separate from deferred decisions |
| [`ASSUMPTIONS.md`](ASSUMPTIONS.md) | Ground rules, observed facts and assumptions, each with its source, confidence and what would change the owner's mind |
| [`TRANSCRIPT_NOTES.md`](TRANSCRIPT_NOTES.md) | Suspected mishearings and contradictions, with evidence and the owner's conclusions |
| [`AI_LOG.md`](AI_LOG.md) | Tasks given to AI, where it was wrong, and what changed |

**Release gating of documentation.** Content that depends on a release pack is added on that pack's release day (16–19 Nov, 10:00 IST), matching the system's rule that later material must not leak into earlier answers.

**Public-repository safety.**
- No raw challenge data, source quotations, personal names, employee identifiers, individual pay details or financial figures appear in this repository.
- Evidence is cited by opaque references (`EV-nnn`), whose mapping is kept privately.
- `.gitignore` blocks raw source formats, `.env` files and local data.

## Working conventions

- At least two meaningful commits per day, with messages a teammate can follow.
- Review `git status` before every commit; challenge data and secrets must never appear.
- Source truth is the owner's call. AI does not decide which source is true.

## AI use

AI assistants are used throughout, and every main task, error and correction is logged in [`AI_LOG.md`](AI_LOG.md). The project owner is responsible for everything in this repository.
