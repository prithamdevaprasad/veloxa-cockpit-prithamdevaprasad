# DECISIONS

**Release status: material released up to D1 (Mon 16 Nov 2026).** References to entries that depend on later release packs are added when those packs are released.

These are the choices that could be defended either way: data model, storage, isolation and authorization, time, agent memory, disclosure, what the cockpit refuses to answer, risk ranking, workflows and models (`email.md` line 71). For each one, this file gives:

- the selected approach;
- the alternatives that were considered;
- the trade-offs;
- the status;
- the rationale as documented.

**Rules for this file**

- **Approved and deferred decisions are kept apart.** Part B holds approved decisions. Part C holds deferred decisions, which are **not** approved, and no default stated there is an approval.
- **Two kinds of rationale.** *Documented rationale* summarises the reasoning recorded in `ARCHITECTURE.md` and the private research behind it, which AI prepared. *Owner rationale* is the owner's own reasoning. It is filled in only with words the owner has actually written. Where none has been written, the field reads **"Owner to write"**.
- **Revise, don't delete.** A changed decision gets a dated revision line. The earlier text stays. Superseded recommendations are listed in Part D so they are not reintroduced.
- Source-truth conclusions (which source to believe) are **not** decisions in this file. They belong to the owner in `TRANSCRIPT_NOTES.md`.

---

## Part A. Approval record (documented owner statements)

These are the only owner statements this file relies on. They are quoted from the owner's instructions of 16 Nov 2026.

| Date | Owner statement (verbatim) | Effect |
|---|---|---|
| 16 Nov 2026 | "The hackathon deadline is an implementation and delivery constraint, not an architectural design constraint." | Production-first override: the architecture is chosen for a production product, and the hackathon is a profile of it (`ARCHITECTURE.md` §25) |
| 16 Nov 2026 | "I have decided to approve the production-first architecture direction documented in the latest research. I do not want another round of broad architectural reconsideration or unnecessary redesign." | The baseline in `ARCHITECTURE.md` v1.0 is approved |
| 16 Nov 2026 | "Treat the production-first reference architecture as the approved architectural baseline. … In particular, preserve:" PostgreSQL 18 as the system of record; cell-based multi-tenancy and RLS; append-only ledger and sealed admission sequencing; admission, recorded and valid time; bitemporal projections and versioned corrections; source ACL ingestion and permission history; SpiceDB/ReBAC, OPA/ABAC and Cedar tool authorization architecture; governed metrics and policy compilation; hybrid retrieval and evidence provenance; independent claim verification; governed employee-agent memory and interviewer isolation; payroll disclosure auditing; evidence-backed risk prioritisation; DBOS for the initial implementation/pilot and Temporal as the scale option; in-house LLMClient policy; modular core, connector workers and indexing workers; tenant directory, cell routing and migration strategy | The decisions in Part B |
| 16 Nov 2026 | "Do not invent final answers for genuinely unresolved choices." Approximate disclosure handling (D-22) is to be kept in the deferred section | Part C |
| 16 Nov 2026 | "Do not copy private research documents, the original challenge ZIP, employee data, payroll, emails, transcripts or source exports into the repository." | DEC-03 |
| 16 Nov 2026 | "I will personally decide the frontend direction in a later phase." | Frontend deferred (Part C) |
| 17 Nov 2026 | Frontend direction chosen when asked: "React + Vite + TS" for the CEO cockpit, and Playwright added as a test-only dependency for browser tests | `frontend/` (Sprint 7); the backend contracts of §19 are unchanged |
| 16 Nov 2026 | Historical ACL instruction: "Current interviewer authorization is always mandatory. Historical permissions cannot bypass current revocation." "Historical eligibility does not prove the employee actually read something." "Missing history returns `unknown` and fails closed." Preserve `KB ∩ AZ ∩ POL` | DEC-06, DEC-13 (`ARCHITECTURE.md` v1.0.1) |

| 16 Nov 2026 | "Backend stack: Approve Python 3.12, FastAPI, psycopg 3 and plain-SQL migrations as the production implementation technology direction." | DEC-23 |
| 16 Nov 2026 | "API availability: If a later release pack fails ingestion, keep the API running using the latest successfully sealed batch. Never expose unsealed data." | DEC-24 |
| 16 Nov 2026 | "A-024: Approve the D1 data classification mapping, with strict fail-closed handling." "A-025: Approve the corrected version using actual extraction timestamps." "A-026: Approve that D1 templates are not evidence." | `ASSUMPTIONS.md` |
---

## Part B. Approved decisions

### Data model and storage

#### DEC-01 · PostgreSQL 18 is the system of record at every stage
- **Selected:** one PostgreSQL 18 cluster per cell, with schemas `raw_index`, `ledger`, `judgement`, `knowledge`, `projection`, `policy`, `runtime` and `audit`. Raw payloads go to object storage. The hackathon runs the same PostgreSQL 18 (ADR-01; §6).
- **Alternatives:**
  - SQLite for the hackathon, migrating later (the original research plan);
  - XTDB for native bitemporality;
  - distributed SQL (CockroachDB or Yugabyte) from the start.
- **Trade-offs:**
  - Docker becomes mandatory for the one-command setup, and tests need an ephemeral database.
  - In return: RLS, range types with exclusion constraints, temporal keys, pgvector and real concurrency from day one, with no later data-layer migration.
  - XTDB's system time is ingest time, so it would not remove the need for application-defined time axes.
  - Distributed SQL adds operational cost before any cell outgrows one cluster.
- **Status:** Approved (16 Nov 2026).
- **Documented rationale:** the production requirements (RLS, `tstzrange` exclusion constraints, `WITHOUT OVERLAPS`, filtered vector search) all exist in PostgreSQL 18. Starting on SQLite would create the migration debt the owner ruled out.
- **Owner rationale:** Owner to write.

#### DEC-02 · Append-only evidence ledger; everything derived is a rebuildable projection
- **Selected:**
  - Source-record versions, ACL history and identity records are insert-only. Corrections are new versions.
  - Facts, entities, conflicts, read models, ACL projections and indexes are projections, rebuildable to any sealed admission point.
  - Lineage runs raw object → record version → fact → claim → answer (ADR-04; §6, §7.4).
- **Alternatives:**
  - mutable tables with audit triggers;
  - document storage of extracted claims;
  - database system-versioning.
- **Trade-offs:**
  - More tables and a projection pipeline to maintain.
  - In return: reproducible answers at any past knowledge point, and loss of a projection is recoverable. System-versioning records the wrong time axis.
- **Status:** Approved.
- **Documented rationale:** reproducibility and audit (P3, P11). Mutable ranges on a ledger table were found to make "as known at K" unstable (research r3).
- **Owner rationale:** Owner to write.

#### DEC-03 · Challenge data is supplied by path, never committed
- **Selected:**
  - The setup command takes the release-pack location, verifies a manifest and hashes, and fails with a one-line instruction if the data is missing.
  - The repository contains no raw challenge data and no private research (ADR-23; §25.1).
- **Alternatives:**
  - commit the data (ruled out by the owner);
  - fetch it from a private URL (fragile for graders).
- **Trade-offs:** the grader must place the zip before running the command, so the README must say so first.
- **Status:** Approved; the exact command is to be fixed at implementation.
- **Documented rationale:** the owner's instruction (Part A).
- **Owner rationale:** Owner to write (the instruction gives the rule but no reason).

### Tenant isolation and authorization

#### DEC-04 · Cell-based multi-tenancy with hardened RLS
- **Selected:**
  - Pooled cells (shared schema, `tenant_id` on every row, RLS as a mandatory backstop); silo cells for high-assurance tenants.
  - The RLS model: FORCE RLS; a restrictive tenant policy; transaction-local context; a non-owner app role without BYPASSRLS; `security_invoker` views; allow-listed definer functions; tenant-scoped keys; startup self-checks (ADR-02, ADR-07; §9).
- **Alternatives:**
  - schema-per-tenant;
  - database-per-tenant everywhere;
  - application-level filtering only;
  - session-level tenant context with session pooling.
- **Trade-offs:**
  - Per-transaction context, tenant-leading indexes and a conformance suite to maintain.
  - RLS defends against application bugs, **not** against a compromised application server, so silo cells exist for that case.
- **Status:** Approved. Two parts are deferred: the silo-cell criteria and per-tenant roles (D-11b), and data-class restrictive policies in RLS (D-11a).
- **Documented rationale:** retrofitting `tenant_id` and RLS later touches every table. A cross-tenant leak of HR and finance data would be existential.
- **Owner rationale:** Owner to write.

#### DEC-05 · Layered authorization; ACL history owned by Veloxa
- **Selected:**
  - Authorization runs in layers: RLS → ReBAC (SpiceDB, behind `RelationshipStore`) → ABAC (OPA, behind `PolicyDecisionPoint`) → agent-tool policy (Cedar) → disclosure auditor → model permissions → output validation.
  - Every executor call is authorized before execution, and nothing unauthorized enters a prompt.
  - ACL, identity and group-membership **history** lives in Veloxa's PostgreSQL ledger. SpiceDB holds only the derived current projection (ADR-06; §10).
- **Alternatives:**
  - application-coded RBAC only;
  - a single policy engine for everything;
  - post-filtering results;
  - OpenFGA or Cerbos instead of SpiceDB.
- **Trade-offs:**
  - Three external engines to integrate behind interfaces.
  - SpiceDB is vendor-led, not a CNCF project (governance vs capability).
  - The hackathon runs these engines in-process behind the same interfaces (§25.1).
- **Status:** Approved.
- **Documented rationale:** permission fidelity to source systems is what makes cross-department intelligence safe and what grounds the agents. ReBAC engines keep only short history, so they cannot be the permission-history store.
- **Owner rationale:** Owner to write.

#### DEC-06 · Current authorization is separate from historical ACL evaluation
- **Selected** (`ARCHITECTURE.md` v1.0.1, §10.7):
  - **Current authorization** of the requester is mandatory for every output. It is the only mechanism that grants access.
  - **Historical ACL evaluation** (C32, `HistoricalAclEvaluator`) answers what a source principal could access in the past, in one of two modes, `POINT_IN_TIME` or `ELIGIBILITY_DURING_WINDOW`. It can only narrow what is used.
  - Effective decision = current AND historical. A historical `allowed` never restores revoked access, and `unknown` counts as denied.
  - Eligibility is not proof of reading.
  - Historical evaluation never calls SpiceDB.
  - The result separates the historical instant (`historical_at`) from the evaluation run time (`evaluated_at`). In **both** modes, only instants at which the resource is known to exist can qualify:
    - known not to exist → `denied` (`resource_not_available`);
    - existence unknown → `unknown` (`resource_availability_unknown`);
    - a first observation is never treated as creation.
    
    (Correction pass and owner decision, 16 Nov 2026.)
- **Alternatives:**
  - evaluate everything against the current ReBAC projection (wrong for historical questions);
  - keep history in the ReBAC engine (its snapshot retention is short);
  - point-in-time evaluation only (the v1.0 clarification; superseded by adding the window mode on the owner's instruction);
  - treat historical eligibility as evidence of knowledge.
- **Trade-offs:**
  - A Veloxa-built evaluator with its own test suite (ACL-H1 to ACL-H6).
  - Window evaluation needs range intersection over the projections.
  - A maximum lookback still has to be set (D-10b).
- **Status:** Approved as a clarification of DEC-05 and DEC-13. No ADR reopened.
- **Documented rationale:** the v1.0 text could be read as authorizing the interviewer "at K" from history, which would allow revoked access to return (AI_LOG E16).
- **Owner rationale:** the owner's instruction states the rules (Part A). Further reasoning: Owner to write.

#### DEC-07 · Vector tenancy and ACL filtering inside SQL
- **Selected:**
  - Tenant-partitioned chunk tables with one vector index per partition; ACL, admission and data-class columns filtered in SQL.
  - An exact-search path for small authorized sets.
  - "Authorize before anything" for residual checks.
  - Dense retrieval is **disabled** in the hackathon profile (ADR-08; §12).
- **Alternatives:**
  - a shared index with iterative scans;
  - a dedicated vector database with payload filters.
- **Trade-offs:**
  - pgvector applies filters *after* the approximate scan, so recall loss and timing channels are managed by partitioning, not removed.
  - Tuning values are not set (D-12a).
- **Status:** Approved (tuning deferred).
- **Documented rationale:** verified pgvector behaviour. In this dataset, near-identical names refer to different entities, and the corpus fits the whole-scope strategy.
- **Owner rationale:** Owner to write.

### Temporal model

#### DEC-08 · Three time axes and four explicit query modes
- **Selected:**
  - Admission (K), source-recorded (R, an interval with a basis) and valid (V) time.
  - The modes CURRENT, KNOWN_AT, RECORDED_BY and VALID_AT, always echoed in the answer.
  - A date-only input means the end of that day in the tenant's time zone (A-003).
  - "What did we know on X?" before any admission returns "nothing had been admitted" and *offers* RECORDED_BY. It is never silently re-interpreted (ADR-03; §8).
- **Alternatives:**
  - one "as-of" parameter (the earliest research; it conflated two questions);
  - KNOWN_AT only;
  - database system-versioning.
- **Trade-offs:**
  - RECORDED_BY excludes undated transcripts.
  - Some fields need implied-recording assumptions (A-005).
  - Every derived object must carry its admission point.
- **Status:** Approved.
- **Documented rationale:** a must-have ("what did we know on date X", `email.md` line 54). In production, it is what makes executive answers reproducible and auditable.
- **Owner rationale:** Owner to write.

#### DEC-09 · K is a sealed, commit-ordered admission sequence
- **Selected:**
  - K is a strictly increasing `batch_seq` per tenant, assigned by a single sequencer per cell and sealed only after the batch's records, ACLs and cursor state have committed.
  - The hackathon's four packs are batches 1–4, sealed at 10:00 IST on their release days (ADR-04; §8.2; GR-01).
- **Alternative:** an `admitted_at` timestamp column default (research r2).
- **Trade-offs:** a single writer per cell, which is not on the interactive path.
- **Status:** Approved.
- **Documented rationale:** PostgreSQL transaction timestamps are assigned before commit and are not commit-ordered, so "as known at K" could otherwise change after the fact.
- **Owner rationale:** Owner to write.

### Answering

#### DEC-10 · A multi-stage pipeline over declared operations and governed metrics
- **Selected:**
  - The stages run in this order: pre-screen → temporal parse → bounded planner (declared operations only) → authorized execution → evidence compilation → composition → independent verification → grading.
  - All numbers come from versioned metric definitions, compiled to parameterized SQL. The model never authors SQL (ADR-05, ADR-09, ADR-21; §11, §13).
- **Alternatives:**
  - one full-context model call;
  - a router over a fixed tool list;
  - text-to-SQL;
  - open agentic tool loops;
  - an agent framework in the core.
- **Trade-offs:**
  - More components and more latency per answer.
  - The governed-metrics layer is new code Veloxa owns.
  - Whole-scope context is kept as a strategy for small scopes.
- **Status:** Approved.
- **Documented rationale:** answer correctness and a bounded policy surface. The frameworks reviewed were rejected for the core because of release churn and guardrails that run alongside an action instead of before it.
- **Owner rationale:** Owner to write.

#### DEC-11 · An independent verifier that can only drop or downgrade
- **Selected:**
  - Claims are decomposed into atomic claims. Each is checked against its *own* citations by deterministic checks (existence, visibility, numbers, entities, policy, hedges) and by a three-valued entailment pass.
  - The verifier never adds or upgrades a claim (ADR-10; §13.4).
- **Alternatives:**
  - no verifier;
  - pooled-context faithfulness scoring (kept only as an offline cross-check);
  - model self-rated confidence.
- **Trade-offs:** cost and latency per answer. Qualitative entailment cannot be proven, only measured.
- **Status:** Approved.
- **Documented rationale:** citation integrity (NFR-04) and fail-safe behaviour.
- **Owner rationale:** Owner to write.

#### DEC-12 · Evidence grades A–D with qualifiers; deterministic status
- **Selected:**
  - Grades express the strength of evidence, not a probability.
  - Qualifiers (e.g. "as at extraction") never lower the grade.
  - Status precedence: refused > abstained > conflict > qualified > answered.
  - Model-stated confidence is never used (ADR-11; §13.5).
- **Alternatives:**
  - model-verbalised confidence;
  - numeric probabilities;
  - a single confidence score.
- **Trade-offs:** no calibrated likelihoods until production feedback provides labelled outcomes.
- **Status:** Approved.
- **Documented rationale:** verbalised model confidence is overconfident in the cited studies. The challenge asks for "a confidence level" plus honest "I don't know" or "the sources disagree" (`email.md` lines 49–52).
- **Owner rationale:** Owner to write.

### Agent knowledge and memory

#### DEC-13 · Employee agents answer only from KB ∩ AZ ∩ POL
- **Selected:**
  - One shared agent runtime.
  - Each agent turn uses only what the represented employee could know (KB: historical eligibility over a window ending at the read basis or the agent's cutoff, labelled `participant`, `possible` or `role_plausible`), intersected with what the interviewer may see now (AZ: current authorization) and tenant policy.
  - No credentials and no actions.
  - Output is labelled as an AI representation (ADR-12; §14.2–§14.3).
- **Alternatives:**
  - agents acting with the employee's permissions (superseded: it is impersonation and enables the proxy attack);
  - 14 independent agents;
  - a supervisor/specialist hierarchy.
- **Trade-offs:**
  - Agents know less than a "free" persona would.
  - Access-only items must be hedged ("I'd have had access to…").
  - The response to withheld knowledge is deferred (D-13a).
- **Status:** Approved; clarified in v1.0.1.
- **Documented rationale:** KB \ AZ (the employee could know it, the interviewer may not see it) is the proxy attack. AZ \ KB (the interviewer may see it, the employee couldn't know it) breaks the agent's realism. Access does not prove reading.
- **Owner rationale:** the owner's instruction (Part A). Further reasoning: Owner to write.

#### DEC-14 · Governed agent memory, isolated per interviewer
- **Selected:**
  - Working, episodic, semantic, interview and reflective memory tiers, with validity intervals and provenance.
  - Facts are expired, never deleted. Invalidation is audited.
  - `tenant_id`, `agent_id` and `interviewer_id` are separate RLS-enforced columns.
  - By default, memories from one interviewer's sessions are not available in another's (ADR-12; §14.5).
- **Alternatives:**
  - an external memory product as the system of record;
  - shared memory across interviewers;
  - no persistent memory.
- **Trade-offs:** more schema. Shared memories for declared groups are deferred (D-14b).
- **Status:** Approved.
- **Documented rationale:** memory must obey authorization and time like every other store. The external products reviewed could not.
- **Owner rationale:** Owner to write.

#### DEC-15 · Invented personal lives only in synthetic tenants, each logged
- **Selected:**
  - Persona details (voice, life details) exist only when `synthetic = true`.
  - Each detail has an assumption ID (A-101 onwards) and is `assumption`-class data, never citable as evidence.
  - Sensitive categories and invented business facts are forbidden (§14.4).
- **Alternatives:**
  - free model-generated personas;
  - no invented personal life (would fail the must-have, `email.md` line 48).
- **Trade-offs:** the persona specs must be authored or approved by the owner (none written yet).
- **Status:** Approved.
- **Documented rationale:** satisfies the must-have without fiction ever becoming evidence. Persona invention is disabled for real tenants.
- **Owner rationale:** Owner to write.

### Disclosure and refusal

#### DEC-16 · A formal disclosure auditor for payroll aggregates
- **Selected:**
  - A per-principal disclosure ledger of released aggregates, recording structure, never values.
  - A value-independent row-space test under a stable-pay linkage model, covering every channel (answers, evidence views, agents, exports, cache reads) and background-knowledge rows.
  - A small-group test (k).
  - MAX, MIN, MEDIAN and variance are refused. Arithmetic is exact. The auditor fails closed (ADR-13; §15).
- **Alternatives:**
  - a per-query k-threshold only;
  - rounded latest-quarter totals;
  - a pairwise cohort-difference rule;
  - differential privacy everywhere.
- **Trade-offs:**
  - Only *exact* disclosure is theorem-backed. Interval (partial) disclosure is not covered (D-22, deferred).
  - Some legitimate totals will be refused or coarsened.
- **Status:** Approved. D-14a (parameters) and D-22 remain deferred.
- **Documented rationale:**
  - The CFO's statement that individual salaries must never reach the CEO ([EV-048 · T2 · D1]; A-017).
  - The research showed that simpler rules (per-query thresholds, rounding, pairwise cohort differences) miss multi-period inference.
- **Owner rationale:** Owner to write.

#### DEC-17 · What the cockpit refuses to answer
- **Selected** (§13.6; deterministic pre-screen before any model call). The cockpit refuses:
  - individual compensation for non-privileged principals;
  - ranking or predicting individuals' attrition, performance or sentiment;
  - reading or summarising private communications for monitoring;
  - reasons for personal or sensitive records;
  - commit-count or activity-timing productivity measures;
  - any action in a business system.
  
  Every refusal carries a reason and a legitimate alternative (e.g. project-level continuity risk instead of an individual attrition ranking).
- **Alternatives:**
  - answer with a warning;
  - rely on model guardrails alone;
  - restrict by role only.
- **Trade-offs:**
  - Some executive requests are declined outright, so the alternative has to be useful.
  - Activity-timing signals that might correlate with risk are excluded on purpose (P7).
- **Status:** Approved as part of the baseline. **The written responses to voice notes T6a–T6d** (`email.md` line 56) are separate owner deliverables, each due on its release day. Nothing here pre-writes them.
- **Documented rationale:** people-data minimisation (P7) and the CFO's constraint (A-017). An individual attrition profile would be the surveillance the platform is designed to avoid.
- **Owner rationale:** Owner to write.

### Risk model

#### DEC-18 · Evidence-backed risk ranking without false precision
- **Selected:**
  - Detectors produce signals, grouped by business object.
  - Five impact criteria (commitment, financial, continuity, information integrity, time proximity), scored as intervals.
  - Dominance ranking with a few explicit guardrail tiers and declared ties.
  - A probability is shown only if the evidence gates pass; otherwise a labelled deterministic bound is shown.
  - People criteria use allocation records only (ADR-14; §16).
- **Alternatives:**
  - fixed tiers only;
  - impact × urgency on ordinal scales;
  - an ungated Monte Carlo;
  - model-judged ranking;
  - anomaly detection.
- **Trade-offs:**
  - Some risks are shown as "not distinguishable" or "unquantified".
  - The weights are value judgements still to be set (D-15).
- **Status:** Approved (parameter values deferred).
- **Documented rationale:**
  - Ordinal multiplication misranks risks (Cox 2008).
  - A short or non-stationary throughput history makes a simulated delivery probability falsely precise, which is why the gates exist. Whether the supplied data passes them is assessed when the relevant exports are released.
- **Owner rationale:** Owner to write.

### Workflow and model architecture

#### DEC-19 · DBOS first, Temporal at scale, behind `ActivityRunner`
- **Selected:**
  - DBOS (durable workflows as a library, state in PostgreSQL) for the initial implementation and pilot. Temporal when the scale or isolation triggers fire (§17.5).
  - Activities are idempotent, with pinned step names and an outbox (ADR-15; §17).
- **Alternatives:**
  - Temporal from the pilot;
  - a PostgreSQL job queue only;
  - Celery with Redis;
  - data orchestrators.
- **Trade-offs:** DBOS runs inside the application process, with no worker isolation. The migration changes the runner, not the activities.
- **Status:** Approved (outbox transport unselected).
- **Documented rationale:** durable execution without an extra service early on, while keeping a path to Temporal.
- **Owner rationale:** Owner to write.

#### DEC-20 · `LLMClient` owns model policy
- **Selected:**
  - One in-house path to every model, enforcing:
    - routing by stage;
    - residency and model permissions by data class;
    - redaction;
    - **pre-call budget reservations** in PostgreSQL;
    - schema-validated outputs;
    - record/replay for tests.
  - Claude via the Anthropic API. Model identifiers are verified at implementation. Any gateway proxy is optional and unselected (ADR-16; §18).
- **Alternatives:**
  - an LLM gateway proxy as the key-holding perimeter and budget enforcer (superseded);
  - a managed gateway.
- **Trade-offs:** Veloxa owns this policy code.
- **Status:** Approved (proxy unselected).
- **Documented rationale:** the advisory history of the gateway reviewed, including a compromised package release, made it unsuitable as the key-holding perimeter. Budgets must be hard limits enforced before a call.
- **Owner rationale:** Owner to write.

#### DEC-21 · Modular core with separate workers; tenant directory and cell routing from day one
- **Selected:**
  - A modular core (one transactional domain), with separately deployed connector, indexing/projection and parsing workers.
  - Off-the-shelf authorization, workflow and telemetry services.
  - A tenant directory and cell router, plus a tenant-move strategy (ADR-17, ADR-18; §20, §24.3).
- **Alternatives:**
  - fine-grained microservices from the start;
  - a single monolith with in-process workers in production.
- **Trade-offs:** some deployables from day one. Further services are extracted only on defined triggers.
- **Status:** Approved.
- **Documented rationale:** the no-leakage and authorization invariants are easier to prove in one process. Connector workers hold source credentials and need a separate security posture.
- **Owner rationale:** Owner to write.

#### DEC-22 · Frontend-independent API contracts; evaluation with recorded model responses
- **Selected:**
  - JSON contracts for journeys J1–J9 (§19). The frontend technology is not chosen.
  - Evaluation layers (§21.2) cover:
    - deterministic suites;
    - RLS conformance;
    - leakage canaries;
    - privacy and retrieval-leak suites;
    - the historical ACL suite;
    - gold Q&A replaying **recorded** model responses, so the test command needs no API key;
    - agent suites (ADR-19, ADR-20).
- **Alternatives:**
  - template-only offline answers (these test the wrong thing);
  - live-model-only tests (these fail without a key);
  - designing the API around a chosen frontend.
- **Trade-offs:** recordings must be refreshed when prompts or models change. Live evaluation runs on demand.
- **Status:** Approved (frontend and streaming transport deferred).
- **Documented rationale:** `email.md` line 55 requires ≥ 25 test questions run with one command. The owner reserved the frontend decision.
- **Owner rationale:** Owner to write.


### Implementation and operations

#### DEC-23 · Backend implementation stack
- **Selected:** Python 3.12, FastAPI, psycopg 3 and plain-SQL migrations (applied once, in order, by a small in-house runner; an applied migration is never rewritten).
- **Alternatives:**
  - another language runtime (e.g. TypeScript or Go);
  - an ORM with generated migrations;
  - a migration framework.
- **Trade-offs:**
  - Plain SQL keeps PostgreSQL 18 features (RLS policies, triggers, exclusion constraints, `security_invoker` views) explicit and reviewable, at the cost of writing more SQL by hand.
  - Python matches the contract sketches in `ARCHITECTURE.md` §5.3 and the Python SDKs of the integration candidates.
- **Status:** **Approved by the owner, 16 Nov 2026**, as the production implementation technology direction (Part A). Previously recorded in Part C as pending confirmation.
- **Documented rationale:** consistent with the approved architecture's Python contract sketches; the Day 1 foundation is built and tested on this stack.
- **Owner rationale:** Owner to write.

#### DEC-24 · Availability when a later release pack fails admission
- **Selected:**
  - If a later release pack fails admission, the API keeps running on the latest successfully sealed batch.
  - The failed pack is reported as `pending_admission` and health as `degraded`.
  - The failure is audited (pack and reason code only).
  - A failed admission commits nothing, and **unsealed data is never exposed**.
  - The one-shot `init` step still fails, so the API does not start, if the data folder is missing, a migration fails, the security self-check fails, or **no sealed baseline batch exists** (e.g. a fresh database whose first pack failed admission). An existing database with D1 sealed keeps serving D1 when a later pack fails.
- **Alternatives:**
  - stop the API until every released pack is admitted (the earlier fail-closed behaviour);
  - admit the parts of a pack that parse.
- **Trade-offs:**
  - The cockpit stays available, but its answers lag a released pack until the pack is admitted; the pending state is reported rather than hidden.
  - Partial admission was not taken: it would break the rule that a batch is admitted and sealed whole.
- **Status:** **Approved by the owner, 16 Nov 2026** (Part A).
- **Documented rationale:** consistent with `ARCHITECTURE.md` §8.2 (only sealed batches are visible) and §22.1 (a failed batch does not seal, and answers carry "data as of" qualifiers).
- **Owner rationale:** Owner to write.

---

## Part C. Deferred decisions (not approved)

These are **not** decided. Where `ARCHITECTURE.md` §26 states a default, the default governs implementation until the item is closed. It is not an approval.

| ID | Decision | Options or current position | Closed by | Owner decision |
|---|---|---|---|---|
| D-22 | Approximate (interval) disclosure in the auditor | **No option selected.** (a) accept and document the residual risk; (b) probabilistic simulatable auditing; (c) interval checks for pre-defined tables only; (d) differential-privacy noise for non-privileged people aggregates | Owner risk-appetite decision; privacy review | |
| D-13a | How an agent responds to withheld knowledge | Default: uniform non-disclosure ("That's not something I can get into here") | Owner confirmation | |
| D-13b | Work-message excerpts for executives (messages between other employees) | No default: business-sentence excerpts (date only), or exclusion | Owner decision; privacy review | |
| D-14a | Disclosure parameters: *k*; group ledgers | Default *k* = 5; per-principal ledgers only. | Owner confirmation | |
| D-14b | Shared interviewer memories for declared groups | Off by default | Tenant policy | |
| D-15 | Risk weights, anchors, guardrail tiers, gate thresholds | Structure approved; values not set. To be committed before later material is analysed | Owner value judgement | |
| D-11a | Data-class restrictive policies in RLS keyed on role | Not adopted by default | Security review | |
| D-11b | Silo-cell criteria; per-tenant database roles | Per contract | Product/security policy | |
| D-12a | Vector partitioning, over-fetch, exact-search threshold, scan limits | Not set (dense retrieval off in the hackathon) | Benchmarks | |
| D-10a | Maximum ACL staleness per data class | Not set | Security review | |
| D-10b | Maximum lookback for `ELIGIBILITY_DURING_WINDOW` | Value not set. Default window: from the represented person's earliest identity link to min(t_h, knowledge cutoff) | Owner confirmation; privacy review | |
| D-08a | Per-knowledge-point lexical index vs accepting corpus-wide ranking statistics | Default: accept and document | Owner confirmation | |
| D-06a | Erasure design (crypto-shredding + tombstones) | Direction approved; design open | Design and legal review | |
| — | Frontend technology, visual system, layout; streaming transport | Undecided ("I will personally decide the frontend direction in a later phase"). Not needed for the Day 1 deliverables; a recommendation is to be prepared before the answering and briefing work on Day 2 | Owner | |
| — | Backend implementation stack | **Closed: approved by the owner on 16 Nov 2026, see DEC-23.** Previously: Python 3.12, FastAPI, psycopg 3, plain-SQL migrations applied by a small in-house runner. Chosen to match the Python contract sketches in `ARCHITECTURE.md` §5.3 and the Python SDKs of the integration candidates. Not recorded as an approved decision | Owner confirmation | |
| — | Unselected implementation options: trace store, gateway proxy, outbox transport, document parser, connector libraries, graph projection, audit anchoring, model tiering, cache timing | See §26.2 | Owner / benchmarks | |

**Pending owner actions arising from the documentation** (process items, not architecture):

1. Conclusions for every item in `TRANSCRIPT_NOTES.md`.
2. Confirming or editing each draft in `ASSUMPTIONS.md`, especially A-011 (the 14 agents) and A-016 (the access stand-in).
3. Whether to commit `TRANSCRIPT_NOTES.md` and `ASSUMPTIONS.md` entries that rest on D2–D4 material before those packs' release days.
4. The T6a–T6d voice-note responses.

---

## Part D. Superseded recommendations (do not reintroduce)

| Superseded | Replaced by |
|---|---|
| SQLite for the hackathon; LLM-free template answers as the test path | DEC-01; DEC-22 |
| One full-context model call as the answering design; a router over a fixed tool list | DEC-10 |
| A single "as-of" parameter; silent re-interpretation of pre-admission dates | DEC-08 |
| Knowledge time as a timestamp column; mutable ranges in the ledger | DEC-09, DEC-02 |
| Session-level tenant context; RLS without FORCE or `WITH CHECK` | DEC-04 |
| "Pre-filtering inside the vector index" | DEC-07 |
| Agents acting as principals with employees' permissions | DEC-13 |
| Historical ACL evaluation at a single instant only, with access treated as knowledge | DEC-06, DEC-13 (v1.0.1) |
| A pairwise cohort-difference rule; rounded latest-quarter payroll as a privacy control | DEC-16 |
| Impact × urgency scores; ungated Monte Carlo; after-hours activity metrics | DEC-18 |
| An LLM gateway proxy as the key-holding perimeter and budget enforcer | DEC-20 |
| Temporal required from the pilot | DEC-19 |

---

## Revision log

| Date | Change | By |
|---|---|---|
| 16 Nov 2026 | Created from `ARCHITECTURE.md` v1.0.1 (§26, §27) and the documented owner statements in Part A. Every owner-rationale field is left for the owner | AI (Claude Code), for owner review |
| 16 Nov 2026 | **Correction pass:** <br>• DEC-06 notes the `historical_at`/`evaluated_at` split and the resource-availability bound; <br>• DEC-18's rationale no longer states a result derived from D3 material; <br>• the D-14a note no longer quotes a D2 figure. <br>No decision added, approved or changed in substance; D-22 still unselected | AI (Claude Code), for owner review |
| 16 Nov 2026 | **Day 1 public edition:** references to later-pack entries removed (DEC-16, DEC-18, D-14a), and the DEC-16 evidence citation made opaque. No decision changed | AI (Claude Code), for owner review |
| 16 Nov 2026 | Part C: the backend implementation stack recorded as in use, pending owner confirmation; the frontend row notes it is not needed for Day 1. No decision approved or changed | AI (Claude Code), for owner review |
| 16 Nov 2026 | Owner approvals recorded: DEC-23 (backend stack) and DEC-24 (availability) added to Part B, verbatim statements added to Part A, and the Part C backend row closed. Owner rationale left for the owner. No frontend decision made | Owner decision, recorded by AI (Claude Code) |
| 16 Nov 2026 | DEC-24 refined on the owner's instruction: `init` requires at least one sealed baseline batch before the API may start | Owner decision, recorded by AI (Claude Code) |
