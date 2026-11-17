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

## Status (Tue 17 Nov 2026, Day 2; Sprint 5)

| Area | State |
|---|---|
| Architecture | Approved baseline v1.0.1 |
| **Evidence ledger foundation** | **Working and tested.** PostgreSQL 18 schemas and roles, append-only ledger, FORCE RLS with tenant isolation, startup self-checks, hash-chained audit, least-privilege process separation |
| **Day 1 ingestion** | **Working and tested.** The D1 release pack is admitted as sealed batch 1 (15 files, 112 source records) with content hashes, exact locators and release-time admission. D2–D4 are refused before their release time without being read |
| **Temporal model** | **Working and tested.** Read basis and query modes (CURRENT, KNOWN_AT, RECORDED_BY, VALID_AT), a date meaning end of day in IST, a SQL predicate builder with bind parameters only, and leakage canaries at every batch boundary |
| **Governed metrics and query tools** | **Working and tested.** Rebuildable projections over sealed batches; declared, versioned metric definitions compiled to parameterised SQL; eight declared query tools (person, headcount, finance totals, payroll total, board statements, timeline, conflicts, text search) |
| **Ask Veloxa v0** (`POST /v1/ask`) | **Working and tested, facts-only mode.** Refusal pre-screen, temporal parse, bounded deterministic planner, authorised execution, evidence pack, deterministic verifier, grade and status, and recording of every answer with its citations and audit event |
| **Risk engine v0 and interim briefing** (`GET /v1/briefing`) | **Partial, tested.** Weight-free dominance comparison over the approved criteria; the D-15 governance gate; immutable briefing snapshots. With D-15 pending, the briefing is an unranked watchlist and `top_three` is null |
| **Evidence detectors C1–C4** (`src/veloxa/signals/`) | **Implemented as far as approved policy and admitted evidence allow; tested.** A typed signal contract and registry; C1 commitment, delay and status observations (the C1 measure stays unquantified), now with revised deadlines and a dated history; meeting-summary figure observations for C4 (labels kept as written, no inferred period or unit); the approved C2 calculation (D15-04) with C2 reported as missing evidence, because no admitted record states a forward financial consequence; C3 evidence validation, with C3 measures blocked pending Q3, Q9 and Q10; C4 discrepancy capture, where unapproved mappings stay inactive. Signals are recorded append-only (migration `0006`) and shown in the briefing by criterion with an explicit status. No scores, priorities, guardrails or probabilities |
| **Historical ACL evaluator (C32)** (`src/veloxa/acl/`) | **Implemented and tested on synthetic history.** POINT_IN_TIME and ELIGIBILITY_DURING_WINDOW over admitted, bitemporal ACL, identity and membership history (migration `0008`); ACL-H1 to ACL-H6 pass. The real Day 1 pack carries no ACL history, so every real evaluation is `unknown` (never allowed); e-mail parties are not ACL evidence |
| **Disclosure auditor (C19)** (`src/veloxa/disclosure/`) | **Implemented and tested on synthetic payroll; release OFF.** One gate for every protected aggregate: the exact core decision on M2 person-segments, with the principal's own values as background; an append-only ledger per tenant, principal and measure (migration `0009`), with idempotent retries and serialized decisions. A payroll value can come only from a database function that requires an allowed decision of the same principal and an owner-recorded release. No release is recorded, so payroll stays refused on every channel, including the real Day 1 data. Still missing: the sdcTable oracle (no R runtime), and the production identity, ReBAC and ABAC prerequisites |
| **Current authorization (C02, C20, C21)** (`src/veloxa/authz/`) | **Contracts implemented and tested; not yet wired into the API.** A provider-neutral token validator (signature, algorithm, issuer, audience, expiry, tenant, typed subject, token revocation, audited); ReBAC as PostgreSQL relationship tuples in a SpiceDB-compatible shape (migration `0010`); ABAC policy `abac/1` (deny overrides, missing attributes deny). No identity provider is selected, and SpiceDB and OPA are not run (§25.1). C19 now takes individually authorised values and readable payroll documents into account, decided by current authorization only |
| **Entity and identity resolution (C12)** (`src/veloxa/resolution/`) | **Implemented and tested.**<br>• Registries (migrations `0011`, `0012`): source identities by context, with origin and observation time; entities anchored by one identity (a person only by an HR record); context-scoped aliases with provenance, retirement history and conflict detection.<br>• Candidates carry an explicit rule classification and evidence, never a score.<br>• Decisions are append-only and versioned: each references the decision it supersedes, cites evidence, can be replayed idempotently, and is audited.<br>• **Approval guard:** only synthetic test identities can be approved. The API needs a ReBAC `resolve` relation, the code refuses, and a database trigger refuses. Every real candidate stays unresolved, and identities grant nothing.<br>• `veloxa identity-sync` registers admitted HR and e-mail identities, with candidates, and decides nothing |
| **Source contracts** (`src/veloxa/ingest/`) | **Generic CSV reader, and typed contracts for the D2 people sources** (hiring, leave, team pulse), all tested:<br>• the hiring status is a snapshot with its own date, and notes are restricted;<br>• leave is never availability;<br>• pulse comment text is never kept.<br>Parsing is not admission: these sources have no approved data class yet. Contracts derived from Day 3 material are published only after its release |
| **Conflict register** (`GET /v1/conflicts`) | **Implemented and tested.** Every source disagreement visible at a read basis, at any time mode, with each side's record version, batch K, source path, recorded time and valid time. Entries stay open: no value is chosen |
| **Requester authentication** | **Fails closed.** No identity provider is integrated, so `/v1/ask` and `/v1/briefing` return 401 unless the explicit local development fixture is enabled |
| HTTP API | `/health`, `/v1/meta/clock`, `/v1/meta/releases`, `POST /v1/ask`, `GET /v1/briefing`, `GET /v1/briefing/{snapshot_id}`, `GET /v1/today` (the opening view: at most three evidence-backed items, unranked; voice note T6a, see `voice_notes/T6a.md`), `GET /v1/conflicts`, `GET /v1/identity/queue`, `POST /v1/identity/requests`, `POST /v1/identity/decisions` (C12; closed unless the principal holds the `resolve` relation), `GET /v1/agents`, `POST /v1/agents/{id}/sessions`, `POST /v1/sessions/{id}/messages`, `POST /v1/sessions/{id}/close`, `GET /v1/agents/{id}/memories` (agents: see `docs/agents/`) |
| Gold questions (`tests/gold`) | **Runner public; cases private** (their expected answers come from the challenge material). 30 cases on the admitted D1 data pass locally. Cases that depend on a pack not yet admitted are kept in a private pending set |
| **Employee-agent runtime (C23/C24)** (`src/veloxa/agents/`, branch `feat/sprint6-shared-agent-runtime`) | **Implemented and tested on synthetic agents.**<br>• One shared runtime; agents are versioned configuration.<br>• Declared read-only tools.<br>• Evidence is limited to KB ∩ AZ ∩ POL.<br>• Five-tier memory, isolated by tenant, agent and interviewer (migration `0013`).<br>• Idempotent, audited turns.<br>• All 14 slots are unassigned and **blocked**, pending the owner's mapping.<br>• Cedar (C22) and a model provider (C27) are **not deployed**: an in-process agent-tool policy and a facts-only composer stand in.<br>See `docs/agents/` |
| Ranked briefing (D-15), model planner and composer | **Not implemented yet** (weighted ranking only after the owner approves D-15) |
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
| `init` (one-shot) | The administrator, migrator and ingest credentials; the release packs mounted read-only | Creates roles and extensions and applies migrations (each committed before anything else runs). On a **fresh** database only, it admits the authorised baseline pack (D1). It **never admits a later pack**, even once released: that is an explicit operation (below). Then it builds projections, self-checks and exits. `init` fails if a migration or the self-check fails, or if a fresh database has no data folder or its baseline admission fails (nothing to serve; DEC-24 availability otherwise) |
| `api` (long-running) | **Only** the application-role password; **no** data mount | Starts after `init` succeeds; serves `http://127.0.0.1:8000`. It refuses to start if any privileged credential is in its environment |

**Admission is explicit and authorised.** A released pack is admitted only by `veloxa ingest --pack <id>`, and only if [`config/admission_authorisations.toml`](config/admission_authorisations.toml) lists it with the owner decision that approves its catalogue rules and data classes (today: D1 only). Anything else is refused before a file is opened, audited with a reason code, and commits nothing. Until then the API keeps serving the latest sealed batch and reports the pack as `awaiting_authorisation` (or `pending_admission` if it is authorised but failed), with health `degraded`. Unsealed data is never exposed. A source re-released in a later pack is a **correction**: a new record version that replaces the old one from its own batch onward, while earlier knowledge points still see the original (T8). If `VELOXA_DATA_PATH` is not set, Compose stops with an explanatory message. Restarting is safe: it admits nothing, and an admitted pack is recognised by its manifest hash and never admitted twice, including when two admission runs race.

**Check it:**

```bash
curl http://127.0.0.1:8000/health             # {"status":"ok","selfcheck":"pass",...}
curl http://127.0.0.1:8000/v1/meta/releases   # D1 admitted; later packs not yet released
curl -X POST http://127.0.0.1:8000/v1/ask -H "content-type: application/json" \
     -d '{"question": "What is our headcount?"}'      # 401 not_authenticated: requester endpoints fail closed
```

**Requester authentication.**
- No identity provider is integrated yet, so `/v1/ask` and `/v1/briefing` refuse every request with 401 by default. No identity, title or role is assumed, and none is accepted from the request.
- **For local development only**, an explicit fixture can be enabled in `.env`. It needs all three settings, has no defaults, and the API refuses to start if it is half-configured:
  - `VELOXA_DEV_AUTH=1`;
  - `VELOXA_DEV_TOKEN`, a random secret of at least 32 characters;
  - `VELOXA_DEV_PRINCIPAL`, the employee ID the fixture acts as.
- With the fixture enabled, `/health` reports `"auth": "development_fixture"`, and requests send the token:

```bash
curl -X POST http://127.0.0.1:8000/v1/ask -H "Authorization: Bearer $VELOXA_DEV_TOKEN" \
     -H "content-type: application/json" -d '{"question": "What is our headcount by department?"}'
curl -H "Authorization: Bearer $VELOXA_DEV_TOKEN" "http://127.0.0.1:8000/v1/briefing?as_known_at=2026-11-16"
```

Never enable the fixture on a shared or production deployment. Production needs the identity-provider integration of `ARCHITECTURE.md` §19.1 (see *Current limitations*).

`/v1/ask` returns the `Answer` contract (`ARCHITECTURE.md` §19.4):
- `status`, with precedence refused > abstained > conflict > qualified > answered;
- `interpretation` ("I read this as …"), including the time basis;
- `grade` A–D with reasons, and `qualifiers`;
- `claims`, each citing fact IDs and record IDs, with its verification result;
- `facts`, `evidence` (excerpt, source path, locator, data class, batch) and `unknowns`;
- `read_basis` with a banner, and an `audit_id`.

A refusal is a normal answer with `status: "refused"` and a policy ID, reason and alternative. Typed errors are `not_authenticated` (401), `not_authorised` (403) and `invalid_time` (422).

**Facts-only mode.** v0 runs the architecture's degraded path (C16): the planner is deterministic, and each claim is a verbatim rendering of a fact or excerpt that a declared operation returned. Answers are therefore at most `qualified`. The model planner and composer will run behind the same stages; the verifier and grading stay the same.

**Principal.** The authenticated principal must resolve to exactly one person in the latest sealed HR export, or the request gets 403. Emails are usable as evidence only for the people who were party to them.

**Briefing (`GET /v1/briefing`), interim while D-15 is pending.** It accepts the time parameters `as_known_at`, or `mode` with `as_of`; ranking options are never accepted. It returns:
- `ranking_mode: "unweighted_partial_order"`, `owner_decision_status: "D-15 pending"`, `top_three: null` and an explanation;
- a `watchlist` in `risk_id` order. That order is a stable technical order and **carries no priority**.
- Each risk carries:
  - its description, evidence (record IDs, source paths, locators, batch) and time context;
  - its value on each criterion: an interval with its measure definition, or missing, uncertain or not applicable;
  - its unknowns;
  - its comparison result: which risks demonstrably dominate it, which it dominates, trade-offs, and which risks are indistinguishable from it or cannot be compared with it, with an explanation.
- `comparisons`, `signals_needing_data` and the read basis;
- `evidence_signals`: the C1–C4 detector observations grouped by criterion, each with its status (`active`, `provisional`, `unquantified`, `unmapped_requires_owner_approval`, `blocked_owner_decision` or `missing_evidence`), evidence records, temporal fields, assumptions kept separate from observed facts, missing-data reasons, the owner decisions it waits for, and why other signals were withheld. These are **not risks, scores or priorities**, and every criterion states how far it can be measured today (C5 is time context only). The snapshot references the recorded detector run.

A risk dominates another only if it is demonstrably at least as bad on every applicable criterion, with identical measure definitions, and worse on at least one. Missing evidence is never read as zero. "Not dominated" and "dominated" are not priority tiers.

Every briefing is stored as an append-only snapshot with the D-15 policy status and version in force. `GET /v1/briefing/{snapshot_id}` replays it exactly as served. The owner's D-15 choices are listed, blank, in [`docs/D-15_owner_decision_sheet.md`](docs/D-15_owner_decision_sheet.md).

The database and API listen on `127.0.0.1` only. The Compose file contains local-development passwords for that localhost-only database. Override them in `.env` for anything else.

## Running the tests

The tests need a PostgreSQL 18 server. The Compose `db` service works:

```bash
python3.12 -m venv .venv && . .venv/bin/activate        # Windows: py -3.12 -m venv .venv; .venv\Scripts\activate
pip install --constraint requirements.lock -e ".[dev]"
VELOXA_DATA_PATH=/path/to/packs docker compose up -d --wait db
export VELOXA_TEST_ADMIN_DSN="postgresql://veloxa_admin:local-dev-admin@127.0.0.1:55432/veloxa"
ruff check .
python scripts/public_scan.py   # public safety scan: exits 1 on any finding (CI runs it as a gate)
pytest                       # everything (407 tests), including the real-data tests if VELOXA_DATA_PATH is set
pytest -m "not realdata"     # what CI runs: synthetic data only
```

- Use `127.0.0.1`, not `localhost`. On Windows, `localhost` tries IPv6 first and stalls.
- Each test session creates and drops its own throwaway database.
- Test data is **synthetic** (`tests/synthetic_pack.py`).
- The real-data tests (`tests/test_d1_realdata.py`) run only locally. They check counts, data classes, content hashes and answer structure against the real Day 1 pack, never values.

The automated set of at least 25 gold questions (H-15, H-16) does not exist yet. The first gold questions are added with the Day 2 pack, after its release.

## Command line

Inside the container (`docker compose exec app veloxa <command>`) or in the virtual environment:

| Command | Purpose |
|---|---|
| `veloxa migrate` | Create roles and extensions; apply migrations once, in order |
| `veloxa ingest [--pack D1\|released]` | Explicitly admit release packs from `VELOXA_DATA_PATH`; only released packs listed in `config/admission_authorisations.toml` (default: every released, authorised pack) |
| `veloxa status` | Show the clock, the sealed watermark K and each pack's admission state |
| `veloxa selfcheck --role app\|ingest` | Startup self-checks (§9.3) |
| `veloxa init` | Privileged one-shot step: migrate; bootstrap a fresh database with the authorised baseline pack; never admit a later pack; self-check (needs the privileged credentials) |
| `veloxa agents-seed` | Publish the 14 proposed agent slots, unassigned and blocked (configuration role) |
| `veloxa identity-sync` | C12: register identities and rule candidates from every sealed batch (projection role). It decides nothing and prints counts only |
| `veloxa serve` | Run the API with the application role only |
| `veloxa policy record-d15 --file <toml>` | Record an owner-approved D-15 decision as the next policy version (privileged; the API cannot do this). It does not switch weighted ranking on |

`VELOXA_CLOCK` is a **test-only** setting. It is honoured only together with `VELOXA_TEST_MODE=1`, can move the clock back but never forward, and setting it outside test mode is an error.

## What exists

| Path | Contents | Architecture |
|---|---|---|
| `src/veloxa/migrations/` | Bootstrap (role set, `btree_gist`, `pgvector`); `0001_foundation.sql` (schemas, tenant directory, admission batches, raw manifest, source records, audit chain, triggers, RLS, grants); `0002_hardening.sql` (record-to-manifest hash key, time-ordering checks, protected-pay policy); `0003_projections.sql` (read models, projection role, protected-pay policies); `0004_answers.sql` (append-only answers and citations); `0005_briefing.sql` (D-15 decision register and briefing snapshots); `0012_entity_resolution_history.sql` (C12: observation time, alias provenance and retirement, decision requests, previous-decision references, replay keys, derived state views, the person-anchor guard); `0011_entity_resolution.sql` (C12 registries, candidates and the append-only decision log with the synthetic-only approval guard); `0010_current_authz.sql` (relationship tuples, revocations, revoked tokens, the payroll-document structure function); `0009_disclosure.sql` (C19 ledger, owner release table, the payroll-structure function and the only payroll value function); `0008_acl_history.sql` (projected ACL, identity, membership, resource-existence and ACL-state history, bitemporal, read-only for the application role); `0007_signal_time.sql` (the read basis R and V of each detector run, upper(R) and V of each signal; the database rejects RECORDED_BY signals recorded after the cutoff and VALID_AT signals citing facts of unknown or non-holding valid time); `0006_signals.sql` (append-only detector runs and signals; the database rejects a signal citing a record not admitted and sealed at its K, lineage beyond K, or a number on a missing, unmapped, blocked or unquantified signal). Applied migrations are never rewritten | §6, §8.2, §9.3, §9.4, §15, §21.3 |
| `src/veloxa/ingest/` | File-drop connector: manifest (SHA-256 per file), versioned parsers (xlsx rows, docx, pdf, eml, txt), single-transaction admission and sealing | §7, §8.2 |
| `src/veloxa/releases.py`, `config/release_schedule.toml` | Release schedule and clock; admission decided before any file is opened | §8.2, §25.1 |
| `config/source_catalogue.toml` | Classification of each pack file (kind, source system, data class). Unknown files are rejected | §6.3, §7.2 |
| `src/veloxa/ledger.py` | Visibility at an explicit knowledge point K, the sealed watermark, mapping dates to K | §8.3, §8.4 T1 |
| `src/veloxa/selfcheck.py` | Startup self-checks: role attributes, ownership, RLS/FORCE/restrictive policy, `security_invoker` views, server version | §9.3 |
| `src/veloxa/ingest/contracts.py` | Validation contracts for row exports (required columns, uniqueness, dates, integers, arithmetic identities); a violation refuses the whole pack | §7.2 |
| `src/veloxa/temporal.py`, `tests/canary.py` | Read basis, query modes and the predicate builder; the leakage canary harness that every new surface registers with | §8 |
| `src/veloxa/projector.py`, `metrics.py`, `config/metrics.toml` | Projections over sealed batches; governed metric definitions and their compiler | §6.2, §11 |
| `src/veloxa/tools.py` | The declared query operations a planner may call, each returning facts with lineage and evidence | §13.1–13.2 |
| `src/veloxa/ask/` | Ask Veloxa v0: refusal catalogue (`policy.py`), temporal parse, planner, verifier and pipeline | §13, §19.4 |
| `src/veloxa/risk/` | Risk engine v0: criteria vocabulary (`model.py`), weight-free comparison (`compare.py`), the approved-mapping C4 rule (`detectors.py`), the D-15 gate (`governance.py`), the briefing and its snapshots (`briefing.py`) | §16, §19.3 |
| `src/veloxa/acl/` | C32: projection of admitted ACL history and the historical ACL evaluator | §10.2, §10.7 |
| `src/veloxa/disclosure/` | C19 exact decision core and the disclosure gate (`gate.py`) | §15 |
| `src/veloxa/authz/` | Current authorization: token validation (`identity.py`), ReBAC and ABAC (`current.py`) | §10.1, §10.3, §25.1 |
| `src/veloxa/resolution/` | C12: candidate rules (`rules.py`), registries and decisions (`store.py`), and admitted-evidence integration (`integrate.py`) | §7.5 |
| `src/veloxa/signals/` | Evidence detectors (plus `figures.py` summary figures and `adapters.py` C2/C3 source contracts): the output contract (`contract.py`), evidence verification at K (`evidence.py`), C1 (`c1_delivery.py`), C2 (`c2_financial.py`), C3 (`c3_continuity.py`), C4 (`c4_integrity.py`) and the registry with idempotent recording (`registry.py`) | §16.1, §16.2, §16.5, §16.6, §8.4 |
| `config/figure_mappings.toml` | Owner-approved pairings of reported figures with governed metrics (none approved yet) | §16.2 C4 |
| `src/veloxa/auth.py` | Requester authentication: fail closed by default; explicit local development fixture | §19.1 |
| `src/veloxa/api.py`, `src/veloxa/cli.py` | API (health, time metadata, ask, briefing) and command line (`signals` prints detector counts only) | §19 |
| `tests/` | 465 tests collected (17 Nov 2026), plus the private gold cases when present: release rules and clock, parsers, contracts, database guarantees, concurrency, temporal modes and leakage canaries, metrics and tools, the ask pipeline, risk comparison, D-15 gate, briefing snapshots, authentication, the API, availability on failed admission, the detector contract and the C1–C4 pipelines, the signal store, C12 resolution and its guards, source contracts, the conflict register, real-data checks | §21.2 |
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
- **Protected pay.** The application role, used by every API route, can read neither individual payroll rows nor payroll aggregates. Aggregates can be released only through the disclosure auditor (§15, C19). It is built, but no owner release is recorded, so they stay unreadable. A startup self-check fails if the policy is removed or weakened.
- **Least privilege.** The API process never receives the administrator, migrator or ingest credentials.
- **Visibility.** Reads at knowledge point K see only sealed batches up to K. A K beyond the sealed watermark is rejected.
- **No interpretation at ingestion.** Parsers record what the files say. No source-truth, identity or conflict decision is made; those stay with the owner (`TRANSCRIPT_NOTES.md`).

## Current limitations

- Ingestion runs in the app container's start-up sequence, before the API starts. It does not yet use the durable workflow runtime (DBOS) or an isolated document-parsing sandbox (§7.2, §17).
- **Authentication is not production-ready.** Requester endpoints fail closed. The only way in is the local development fixture, a single static token mapped to one configured employee. Production still needs:
  - identity-provider integration (bearer tokens validated against the provider: issuer, audience, signature, expiry);
  - principal resolution through the tenant directory (`user:<id>`, groups, §10.3) instead of the HR export;
  - ReBAC/ABAC authorization (§10);
  - token revocation and session handling.
- **The risk engine is incomplete.**
  - The detectors produce evidence signals, not risks: on the real Day 1 pack, C1 has observations but an unquantified measure (no open-scope or throughput records; Q7 not approved), C2 is missing evidence, C3 is missing evidence and blocked, and C4 figures are not comparable or unmapped. The watchlist therefore stays empty until an approved mapping or measurable criterion exists.
  - C1 extraction uses deterministic lexical rules (no model), links statements only by an identical subject name, and does not read e-mail. Figures spoken in meeting summaries are not extracted.
  - There is no weighted ranking, no guardrail tiers and no probabilities. These wait for D-15, then a separately approved implementation.
- Projections carry no recorded-time or valid-time columns, so governed metrics and the Ask tools accept CURRENT and KNOWN_AT only; RECORDED_BY and VALID_AT are refused rather than silently unfiltered. The evidence detectors support all four modes: they apply R and V per fact, from the source's own date (else R is bounded by extraction time E) and stated period (else V is unknown and the fact never answers VALID_AT).
- `/v1/ask` runs in facts-only mode (deterministic planner, no model composition, no model entailment pass). No disclosure release is recorded, so payroll aggregates are refused. Some filters are not parsed yet: an expense category (for example travel), a month on ledger spend, and "which vendors" without by/per/each. When more matching facts exist than one answer shows, the answer says it is incomplete.
- Not built yet: entity resolution, ReBAC/ABAC adapters, the historical ACL evaluator and the agents.
- Release packs must be supplied as an extracted folder, not as the zip file.
- Each pack is held in memory while it is hashed and parsed. This suits the hackathon pack sizes; large packs need streaming.
- A released later pack stays unadmitted until the owner authorises it (catalogue rules and data classes); the API serves the earlier sealed batches and reports it as `awaiting_authorisation`.
- A database created before migration `0002` keeps its earlier `extracted_at` values (history is append-only). Recreate a local development database with `docker compose down --volumes` to get the corrected values.

## Documentation

| File | Contents |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Approved reference architecture, hackathon profile (§25), deferred decisions (§26), decision register (§27) |
| [`DECISIONS.md`](DECISIONS.md) | Choices that could be defended either way, kept separate from deferred decisions |
| [`ASSUMPTIONS.md`](ASSUMPTIONS.md) | Ground rules, observed facts and assumptions, each with its source, confidence and what would change the owner's mind |
| [`TRANSCRIPT_NOTES.md`](TRANSCRIPT_NOTES.md) | Suspected mishearings and contradictions, with evidence and the owner's conclusions |
| [`AI_LOG.md`](AI_LOG.md) | Tasks given to AI, where it was wrong, and what changed |
| [`docs/D-15_owner_decision_sheet.md`](docs/D-15_owner_decision_sheet.md) | The owner's pending risk-ranking decisions, with every choice left blank |

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
