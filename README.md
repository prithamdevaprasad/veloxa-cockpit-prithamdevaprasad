# Veloxa CXO Cockpit

An evidence-grounded executive intelligence cockpit for **Veloxa Technologies**, a synthetic company created for the Hiyaku Systems Hackathon (16–20 Nov 2026).

It answers a CEO's questions with:
- short answers that **cite the records behind them**;
- an **evidence grade** for each answer;
- honest **"I don't know"** or **"the sources disagree"** when that is the truth.

It also:
- ranks **what to worry about**, with evidence;
- answers **"what did we know on date X?"** without leaking later material;
- lets you interview **14 employee agents** grounded in the records.

The design is a production-first reference architecture. The hackathon build is a configuration of it, not a separate design (see [`ARCHITECTURE.md`](ARCHITECTURE.md) §25).

## Status

| Area | State |
|---|---|
| Architecture | **Approved baseline v1.0.1** (approved 16 Nov 2026): [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| Documentation | All six required files are present and updated as release packs are published |
| Implementation | Starting. This README is updated as each capability lands |
| One-command setup | **Not available yet.** It will be documented here when the first runnable slice lands (ADR-23) |
| Test set | **Not available yet.** At least 25 gold questions, including at least 5 refusal or uncertainty cases, runnable offline with one command (H-15, H-16) |

## Setup and running (planned, per `ARCHITECTURE.md` §25.1)

- **Runtime.** Docker Compose, with the application and PostgreSQL 18 (with `btree_gist` and `pgvector`).
- **Challenge data is never committed.** The setup command will take the path to the release-pack zip or folder, verify a manifest of content hashes, and fail with a one-line instruction if the data is missing.
- **Model access.** Claude, through the in-house `LLMClient`. Provider keys come from a local `.env`, which is never committed (an `.env.example` will list the variable names). Tests replay recorded model responses, so the test command needs no API key.

The exact commands will be added here once they exist and have been tested from a fresh clone.

## Documentation

| File | Contents |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Approved reference architecture, hackathon profile (§25), deferred decisions (§26), decision register (§27) |
| [`DECISIONS.md`](DECISIONS.md) | Choices that could be defended either way (data model, storage, isolation, time, agent memory, refusals, risk, workflows), kept separate from deferred decisions |
| [`ASSUMPTIONS.md`](ASSUMPTIONS.md) | Ground rules, observed facts and assumptions, each with its source, confidence and what would change the owner's mind |
| [`TRANSCRIPT_NOTES.md`](TRANSCRIPT_NOTES.md) | Suspected mishearings and contradictions, with evidence and the owner's conclusions |
| [`AI_LOG.md`](AI_LOG.md) | Tasks given to AI, where it was wrong, and what changed |

**Release gating.** The challenge material is released in four packs (D1–D4, 16–19 Nov 2026, 10:00 IST). Documentation that depends on a pack is added on that pack's release day, never earlier, matching the cockpit's own rule that later material must not leak into earlier answers.

**Public-repository safety.** No raw challenge data, source quotations, personal names, employee identifiers, individual pay details or financial figures appear in this repository. Evidence is cited by opaque references (`EV-nnn`), whose mapping to source locations is kept privately.

## Repository layout (planned)

Directories are added as implementation lands, following the component boundaries in `ARCHITECTURE.md` §5 and §20.

| Path | Purpose | Architecture |
|---|---|---|
| `docs/voice-notes/` | Written response to each CEO voice note, one file per note, committed on its release day | H-20 |
| `db/migrations/` | PostgreSQL 18 schemas (`ledger`, `judgement`, `knowledge`, `projection`, `policy`, `runtime`, `audit`), RLS and roles | §6, §9 |
| `src/` | Core modules: temporal model, ledger and admission, policy and authorization, governed metrics, retrieval, answer pipeline and verifier, disclosure auditor, risk engine, agent runtime, `LLMClient` | §5, §8–§18 |
| `workers/` | Connector, projection/indexing and document-parsing workers | §20.1 |
| `config/` | Judgement artefacts: metric definitions, risk model, refusal catalogue, persona specs | §11, §13.6, §14.4, §16 |
| `tests/` | Deterministic, RLS-conformance, leakage-canary, privacy, retrieval-leak, historical-ACL and agent suites | §21.2 |
| `evals/` | Gold questions and recorded model responses | H-15, H-16 |
| `scripts/` | Setup and pre-commit release checks | §25.1 |

## Working conventions

- **Commits:** at least two meaningful commits per day, with messages a teammate can follow.
- **No secrets, no challenge data:** enforced by `.gitignore`. Review `git status` before every commit.
- **Source truth is the owner's call:** AI does not decide which source is true. Conflict conclusions are written by the owner in `TRANSCRIPT_NOTES.md`.

## AI use

AI assistants are used throughout, and every main task, error and correction is logged in [`AI_LOG.md`](AI_LOG.md). The project owner is responsible for everything in this repository.
