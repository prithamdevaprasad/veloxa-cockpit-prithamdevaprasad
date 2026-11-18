# Local development reference

This is the companion to [`README.md`](README.md), which covers setup, start, demo, tests, troubleshooting and cleanup. This file holds the detail.

## Command line

Run these in the activated `.venv`, or inside a container with `docker compose exec api veloxa <command>`. Each command uses the database in `VELOXA_DB_HOST` / `VELOXA_DB_PORT` / `VELOXA_DB_NAME` with the role it needs.

| Command | Role | Purpose |
|---|---|---|
| `veloxa migrate` | admin + migrator | Create roles and extensions; apply migrations once, in order. A changed applied migration is refused |
| `veloxa init` | admin, migrator, ingest | Privileged one-shot (the Compose `init` service): migrate, bootstrap a **fresh** database with the authorised baseline pack, self-check. It never admits a later pack |
| `veloxa ingest [--pack D1\|released]` | ingest | Admit released packs listed in `config/admission_authorisations.toml`; anything else is refused before a file is opened |
| `veloxa project [--rebuild]` | projection | Build the read models for sealed batches |
| `veloxa status` | app | Clock, sealed watermark K and each pack's state |
| `veloxa selfcheck --role app\|ingest` | app / ingest | Startup self-checks: role attributes, ownership, RLS/FORCE/restrictive policies, `security_invoker` views, server version |
| `veloxa signals` | app | Run the C1–C4 detectors; prints counts only |
| `veloxa agents-seed` | projection | Publish the 14 agent slots, unassigned and blocked |
| `veloxa identity-sync` | projection | Register identities and rule candidates from sealed batches; decides nothing |
| `veloxa workflow-pack --pack D1 [--key K]` | ingest + projection | Run the durable pack pipeline (DBOS); needs `VELOXA_WORKFLOW_DATABASE_URL` |
| `veloxa workflow-status <id>` | — | A workflow's status (never its inputs or outputs) |
| `veloxa workflow-retention [--apply]` | — | DBOS record retention: dry run by default; `--apply` is refused without an owner decision (`docs/workflows/WORKFLOWS.md`) |
| `veloxa backup --out <dir>` | admin | Verified logical backup (read-only on the source) plus a manifest with a content fingerprint |
| `veloxa restore-drill --dump <file>` | admin | Restore into a new disposable database, compare fingerprints, drop it |
| `veloxa policy record-d15 --file <toml>` | admin | Record an owner-approved D-15 decision as the next version (does not switch ranking on) |
| `veloxa serve [--host] [--port 8000]` | **app only** | The API. It refuses to start if a privileged credential is present |

`VELOXA_CLOCK` is a **test-only** setting. It is honoured only with `VELOXA_TEST_MODE=1`, and it can move the clock back but never forward.

## Scripts

| Script | Purpose |
|---|---|
| `scripts/demo_env.py [--port 8765] [--keep] [--cleanup]` | The synthetic demo API on a disposable database (also the Playwright backend) |
| `scripts/load-env.ps1` | Load `.env` into the current PowerShell process; prints names, never values |
| `scripts/public_scan.py` | Confidentiality scan; any finding exits 1 |
| `scripts/dependency_audit.py` | pip-audit (throwaway venv) and npm audit |
| `scripts/load_test.py` | Local load and concurrency probe (loopback only); see `docs/operations/LOAD_AND_CONCURRENCY.md` |

## Linux and containers

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install --constraint requirements.lock -e ".[dev]"
(cd frontend && npm ci)
VELOXA_DATA_PATH=/path/to/Veloxa_Hackathon_Release_Packs docker compose up --build --detach --wait
curl -fsS http://127.0.0.1:8000/health
```

**What Compose starts.** It runs three services, with least privilege:

| Service | Holds | Does |
|---|---|---|
| `db` | — | PostgreSQL 18 on `127.0.0.1:55432` |
| `init` (one-shot) | The admin, migrator and ingest credentials; the packs, mounted read-only | `veloxa init`, then exits |
| `api` | **Only** the application-role password; no data mount | `veloxa serve` on `127.0.0.1:8000`, after `init` succeeds |

Use `-p <name>` with `VELOXA_DB_PUBLISH_PORT` and `VELOXA_API_PORT` to run an isolated trial project with its own volume.

**CI** (`.github/workflows/ci.yml`) runs four jobs:
- `lint-and-test`: the confidentiality scan, its planted-finding self-test, ruff and pytest (synthetic only);
- `fresh-clone-compose`;
- `frontend`: build plus the Playwright journeys;
- `dependency-audit`.

## Durable workflows (DBOS)

DBOS runs **in process**: no separate server or worker is needed. Its state lives in a **separate** system database:

```powershell
$env:VELOXA_WORKFLOW_DATABASE_URL = "postgresql://veloxa_admin:<password>@127.0.0.1:55432/veloxa_dbos"   # create the database first
veloxa workflow-pack --pack D1
veloxa workflow-status <workflow_id>
veloxa workflow-retention                  # dry run
```

See [`docs/workflows/WORKFLOWS.md`](docs/workflows/WORKFLOWS.md) for privacy (only opaque IDs are persisted), recovery and retention.

## Model configuration (replay adapter only)

| Variable | Meaning |
|---|---|
| `VELOXA_MODEL_PROVIDER` | An implemented adapter. Only `replay` exists; unset means DENY_ALL |
| `VELOXA_MODEL_NAME` | Model identifier recorded on every call |
| `VELOXA_MODEL_REGION` | Permitted region (residency); the adapter must match |
| `VELOXA_MODEL_STAGES` | Comma-separated permitted stages, e.g. `agent` |
| `VELOXA_MODEL_ALLOWED_CLASSES` | Comma-separated data classes that may be sent |
| `VELOXA_MODEL_MAX_ESTIMATE_TOKENS` | Per-call token ceiling |
| `VELOXA_MODEL_REPLAY_DIR` | Directory of recorded responses (`replay` only) |

**Behaviour:**
- **Budgets:** these are owner-set rows in `runtime.llm_budget` (tenant, month). With no row, every call is refused before it is made.
- **What a call record keeps:** stage, provider, model, request hash, data classes and token counts. It never keeps prompt or response text.
- **Fail closed:** a missing setting keeps the model disabled, and `/health` names it.
- **No API key exists:** no real provider adapter exists, so there is no key variable.

## API (`/v1`, JSON)

**Requester-facing endpoints fail closed** (401) without a principal.
- No identity provider is integrated.
- The C02 validator, with JWKS rotation, exists but is not wired to any IdP.
- Locally, the development fixture supplies the principal.

| Endpoint | Purpose |
|---|---|
| `GET /health` | Status, self-check, pending or awaiting packs, auth mode, model status |
| `GET /v1/meta/clock`, `GET /v1/meta/releases` | Clock; each pack's release and admission state |
| `POST /v1/ask` | Ask (`Answer` contract below) |
| `GET /v1/today`, `GET /v1/briefing`, `GET /v1/briefing/{snapshot_id}` | Opening view; unranked briefing; exact replay of a served briefing |
| `GET /v1/conflicts` | Conflict register; every side with provenance and time; never resolved by Veloxa |
| `GET /v1/timeline` | Record events with their sources |
| `GET /v1/risks/history` | How risks appeared, changed, resolved or reopened across knowledge points |
| `GET /v1/scenarios/commitments`, `POST /v1/scenarios/commitment-shift` | Deterministic scenarios (no probability) |
| `GET /v1/dependencies` | Evidence dependency graph and early warnings (unranked) |
| `GET /v1/agents`, `POST /v1/agents/{id}/sessions`, `POST /v1/sessions/{id}/messages`, `POST /v1/sessions/{id}/close` | Agent interviews (idempotent messages) |
| `GET /v1/agents/{id}/sessions`, `GET /v1/sessions/{id}`, `GET /v1/agents/{id}/memories` | The caller's own session history and memories |
| `GET /v1/identity/queue`, `POST /v1/identity/requests`, `POST /v1/identity/decisions` | C12 identity resolution; closed unless the principal holds `resolve`. Only synthetic identities can be approved |

**Time parameters:**
- `as_known_at=<date>`, or `mode` plus `as_of`;
- modes: CURRENT, KNOWN_AT, RECORDED_BY, VALID_AT;
- a date means the end of that day in IST.

**The `Answer` contract** (ARCHITECTURE.md §19.4):
- `status`, with precedence refused > abstained > conflict > qualified > answered;
- `interpretation`;
- `grade` A–D with reasons;
- `qualifiers`;
- `claims`, each citing fact and record IDs with its verification;
- `facts`, `evidence` and `unknowns`;
- `read_basis` with a banner;
- `audit_id`.

A refusal is a normal answer, `status: "refused"`, with a policy ID, reason and alternative. Typed errors are 401 `not_authenticated`, 403 `not_authorised`, 422 `invalid_time`, 413, 415 and 429 (with `Retry-After`).

**Briefing.** While D-15 is pending, the briefing is an unranked watchlist (`top_three: null`) with a dominance-only comparison. Missing evidence is never read as zero.

## Guarantees enforced and tested

- **Release gating and admission.** A pack is admitted only after its release time, in order, as one sealed batch, and only if it is authorised. An unreleased or unauthorised pack's folder is never opened.
- **Ingestion isolation.** Every check refuses the whole pack, commits nothing, and audits a reason code only:
  - size, total and count limits, checked before reading;
  - content must match its type;
  - zip-archive guards;
  - parsing in a separate, time-boxed process.
- **Append-only history.** Source records, the manifest, audit events, agent records and model-call records reject UPDATE, DELETE and TRUNCATE.
- **Provenance.** Each file is read once, and the same bytes are hashed and parsed. Every record carries its path, SHA-256, locator, parser version, batch and data class.
- **Tenant and principal isolation.** FORCE RLS with restrictive tenant policies. On top of that:
  - agent memory is scoped per agent and interviewer;
  - an interviewer sees only their own sessions, requests and executions.
- **Protected pay.** The application role cannot read payroll. Aggregates are released only through the disclosure auditor, and no release is recorded. Individual-pay questions are refused whether the person is named by name, ID or role.
- **Least privilege.** The API process never holds the admin, migrator or ingest credentials.
- **No interpretation at ingestion.** Source truth, identity and conflict decisions stay with the owner.

## Module map

| Path | Contents |
|---|---|
| `src/veloxa/migrations/` | Bootstrap plus `0001`–`0016` (forward-only; never rewritten) |
| `src/veloxa/ingest/` | Manifest, parsers, contracts, isolation (`isolation.py`), admission pipeline |
| `src/veloxa/releases.py`, `config/` | Release schedule, source catalogue, admission authorisations, metrics, figure mappings |
| `src/veloxa/ledger.py`, `temporal.py`, `projector.py`, `metrics.py`, `tools.py` | Visibility at K, time modes, projections, governed metrics, declared query tools |
| `src/veloxa/ask/` | Refusal catalogue, temporal parse, planner, verifier, pipeline |
| `src/veloxa/risk/` | Comparison, D-15 gate, briefing, lifecycle, scenarios, dependency graph (`graph.py`) |
| `src/veloxa/signals/` | C1–C4 detectors and their registry |
| `src/veloxa/acl/`, `authz/`, `disclosure/`, `resolution/` | Historical ACL; C02 (with JWKS), ReBAC, ABAC; C19 auditor; C12 resolution |
| `src/veloxa/agents/` | Shared agent runtime, registry, memory, tools |
| `src/veloxa/llm/` | Governed model client and configuration |
| `src/veloxa/workflows/` | DBOS runner, catalogue, retention |
| `src/veloxa/ops/backup.py`, `gateway.py`, `telemetry.py` | Backups and drills; gateway controls; logs, spans, metrics |
| `frontend/` | The cockpit (React + Vite + TypeScript; Playwright journeys in `frontend/tests`) |
| `tests/` | pytest suite (synthetic data); real-data and gold tests run only locally |

## Company intelligence (migration 0021)

| Module | Role |
|---|---|
| `veloxa.knowledge` | Deterministic claims per sealed batch and the Conflict Registry (explicit precedence rule R-HR-SOR-01; everything else `needs_review`) |
| `veloxa.agents.people`, `veloxa.agents.persona` | The 14 people mapped to HR records (K-01/K-02, hackathon tenant); evidence-only workload; the Assumption Ledger (`scope = persona`, never evidence) |
| `veloxa.ask.confidence` | Deterministic per-claim confidence (High / Moderate / Low / Unknown / Conflicting), specialist routing, answer-derived follow-ups |
| `veloxa.risk.ranking`, `veloxa.risk.worry` | D-15 v1 weighted severity intervals; unknown = full range; overlap = shared rank; sensitivity flag |
| `veloxa.findings` | Event and daily findings, verification before notification, department queues, lifecycle events |
| `veloxa.connectors` | Read-only Jira adapter feeding the data room (mocked tests; disabled without configuration) |
| `veloxa.llm.gemini`, `veloxa.ask.wording` | Optional Gemini wording experiment behind `GovernedLLMClient`; synthetic tenants only; deterministic acceptance check |

**Scripts:** `scripts/company_lab.py`, `scripts/run_acceptance.py`, `scripts/gemini_ab.py`.

## Current limitations

- Production identity (OD-E), a model provider (OD-B), hosting, secrets management, PITR, a trace store and image CVE scanning are **not** in place.
  - See `docs/operations/`.
  - The private owner decision register lists each item.
- Projections carry no recorded-time or valid-time columns. Governed metrics and Ask tools therefore accept CURRENT and KNOWN_AT only; the other modes are refused, not silently unfiltered.
- The facts-only Ask: some filters are not parsed yet. When more matching facts exist than one answer shows, the answer says it is incomplete.
- Packs are held in memory while hashed and parsed, within the C06 limits.
- The process-isolated parser adds roughly one process start per pack. On Windows the full pytest suite takes about 3 minutes.
