# Veloxa CXO Cockpit — Architecture

| | |
|---|---|
| **Document status** | Approved architectural baseline (production-first), version 1.0.1 |
| **Baseline approved** | 16 Nov 2026, by the project owner |
| **Revision** | 1.0.1 (16 Nov 2026): clarification of current authorization vs historical ACL evaluation, with same-day corrections. No ADR reopened; see §27.5 |
| **Release status** | Material released up to D1 (16 Nov 2026). Content that depends on later release packs is added on those packs' release days |
| **Scope** | Reference architecture for a multi-tenant, evidence-grounded executive intelligence platform. §25 describes the hackathon implementation profile as a configuration of this architecture |
| **Change control** | Changes are recorded in the decision register (§27). Items in §26 are deliberately not yet decided |

**Reading conventions**

| Term | Meaning |
|---|---|
| **Approved** | Part of the approved baseline. Changing it requires a new decision-register entry |
| **Deferred** | Listed in §26: either not yet decided, or decided only as a default pending a named review or benchmark |
| **Build** | Implemented and owned by Veloxa (differentiated product logic) |
| **Adopt** | An external component used as-is |
| **Integrate** | An external component run or called *behind a Veloxa interface*, replaceable without changing callers |
| **Adapt** | An external design pattern re-implemented by Veloxa; no runtime dependency |
| **K, R, V** | The three time cutoffs: admission/knowledge (K), source-recorded (R), valid (V). Defined in §8 |

Nothing in this document is an implementation. Snippets are illustrative contracts, not code to be copied.

---

## 1. Purpose and architectural principles

### 1.1 Purpose

The Veloxa CXO Cockpit answers executive questions about a company with short, **evidence-graded** answers that:

- cite the specific records behind them;
- say "I don't know" or "the sources disagree" when that is the honest answer;
- can be **reproduced exactly as of any past point of knowledge**.

It also provides:

- a ranked, evidence-backed view of what the executive should worry about;
- AI representations of employees ("employee agents") that can be interviewed within strict knowledge and disclosure boundaries;
- a company memory of events, decisions and source conflicts.

This document is the **reference design**. It is written for a commercial product serving many organisations. The five-day hackathon is a delivery constraint on *what is implemented first*, not on the architecture (§25).

### 1.2 Principles

| # | Principle | Consequence |
|---|---|---|
| P1 | **Evidence before eloquence** | Every factual claim must trace to admitted records or to deterministic derivations of them. The model is never the authority for numbers, citations, permissions or time |
| P2 | **Time is explicit** | Admission, recorded and valid time are separate axes. Every read states its time basis; no question is silently re-interpreted |
| P3 | **Append-only truth, rebuildable views** | The evidence ledger is insert-only. Everything derived is a projection that can be rebuilt for any sealed admission point |
| P4 | **Deny by default, enforced in code and database** | Tenant isolation, permissions, disclosure control and time visibility are enforced by the database and deterministic code. Prompts are never a security boundary |
| P5 | **Permission fidelity to sources** | Users and agents see only what the source systems' permissions allowed, *as known at the read basis* |
| P6 | **Abstention is a feature** | "Refused", "I don't know" and "sources disagree" are first-class outcomes with defined triggers |
| P7 | **People-data minimisation** | No individual attrition prediction, no activity-timing surveillance, no reading of private communications. Aggregate disclosure is audited against inference |
| P8 | **Humans decide truth and actions** | Source-truth judgements, conflict resolutions, entity merges affecting people, and every action in a business system are human decisions. The system proposes; it never executes |
| P9 | **Own the differentiators, integrate the commodity** | Temporal visibility, provenance, disclosure auditing, governed metrics, memory permissions, verification, ACL history and risk ranking are built. Databases, authorization engines, durable execution and telemetry are adopted or integrated behind interfaces |
| P10 | **Interfaces are fixed first** | Component contracts (§5.3) are stable from the first implementation; implementations are swappable |
| P11 | **Reproducible and auditable** | Every answer records its read basis, policy versions, evidence, model and prompt versions. It can be explained and replayed |
| P12 | **Frontend independence** | The backend exposes capabilities and contracts (§19). The frontend technology and design are decided separately |

---

## 2. Product overview and system boundaries

### 2.1 Capabilities

| Capability | Description |
|---|---|
| Briefing | The top concerns for the current knowledge point, ranked with reasons and evidence |
| Ask | A natural-language question → status, short answer, evidence grade, cited claims, disagreements, unknowns |
| Evidence inspection | Any cited record, with its source, locator, time stamps, reliability class and data class (policy-filtered) |
| Time travel | Any view recomputed "as known at" a past admission point, or "as recorded by" / "valid at" a past date |
| Employee interviews | Conversations with agents representing employees, bounded by what that employee could know *and* what the interviewer may see |
| Company memory | Timeline, decisions found in the records, conflict register with resolutions, change between two knowledge points |
| Organisation | Confirmed and planned reporting lines with effective dates |
| Refusal with alternative | Policy refusals that explain why and offer a legitimate alternative |
| Administration | Admission status, quarantine, policy events, audit, disclosure-ledger summary, conflict review, assumption registry |

### 2.2 Users and principals

| Principal | Examples | Notes |
|---|---|---|
| Executive | CEO, CFO, department heads | Primary users. Clearance per tenant policy |
| Administrator | Tenant admin, platform operator | Operational functions only. Break-glass access is separate (§9) |
| Employee (production, real tenants) | Any staff member | May view and correct their own agent's knowledge and memory |
| Service identities | Connector, indexing, projection and workflow workers | Least-privilege database roles (§9.4) |
| Agents | Employee agents | **Not principals in source systems.** They act only inside the platform, under the interviewer's authorization (§14) |

### 2.3 External systems

| System class | Examples | Interaction |
|---|---|---|
| Sources | HRMS, payroll, GL/ERP, issue tracker, version control, incident management, email metadata, documents, file drops | Read-only ingestion by connector workers, including **ACLs and identities** where the source exposes them |
| Identity provider | OIDC/SAML, SCIM | Authentication, user and group provisioning |
| LLM providers | Claude via the Anthropic API or a cloud partner endpoint | Called only through `LLMClient` (§18) |
| Object storage | S3-compatible, with object lock | Raw landing (immutable) |

### 2.4 Out of scope

These are boundaries, not omissions:

- Any write or action in a source or business system: email, tickets, HR changes, approvals.
- Automated HR decisions.
- Individual attrition, performance or sentiment prediction.
- Reading private messages for monitoring.
- Commit-count or activity-timing productivity metrics.
- Selection of frontend technology, visual design or layout (§19 defines only the contracts).

### 2.5 Tenant kinds

| Kind | Meaning |
|---|---|
| **Real tenant** | A customer organisation. Agents represent real employees. Persona invention is disabled by policy |
| **Synthetic tenant** (`synthetic = true`) | A simulated organisation, such as the hackathon company. Fictional personal details for agents are permitted, each logged as an assumption with an identifier, and they can never become evidence |

---

## 3. Functional and non-functional requirements

### 3.1 Functional requirements

| ID | Requirement |
|---|---|
| FR-01 | Ingest heterogeneous sources (structured exports, documents, messages, voice-note transcripts) into an immutable, release-gated evidence ledger |
| FR-02 | Resolve identities across sources, with human-governed decisions |
| FR-03 | Represent source conflicts explicitly. Record which source is believed and why, as a human judgement |
| FR-04 | Answer questions with cited, graded claims; abstain or report disagreement when warranted |
| FR-05 | Support point-in-time queries in four explicit modes (§8) with zero leakage of later material |
| FR-06 | Provide a ranked, evidence-backed risk briefing that is reproducible per knowledge point |
| FR-07 | Provide interviewable employee agents with grounded identity, workload, history, memory and (synthetic tenants only) a logged fictional personal life |
| FR-08 | Enforce source-faithful permissions for users and agents |
| FR-09 | Prevent inference of protected individual values from aggregates |
| FR-10 | Model effective-dated organisational changes, including planned changes |
| FR-11 | Refuse disallowed requests with reasons and alternatives |
| FR-12 | Provide audit, policy-event and administrative views |
| FR-13 | Provide frontend-independent API contracts for all user journeys |
| FR-14 | (Stretch) Generate simulated post-cutoff activity, isolated and never usable as evidence |

### 3.2 Non-functional requirements

| ID | Attribute | Requirement |
|---|---|---|
| NFR-01 | Tenant isolation | No cross-tenant read or write is possible through application bugs. Enforced by the database (§9) |
| NFR-02 | Temporal integrity | Zero leakage of material admitted after the read basis. Continuously proven by canary tests (§21) |
| NFR-03 | Reproducibility | An answer re-executed with the same read basis, policy and definition versions returns the same evidence set and decisions |
| NFR-04 | Citation integrity | 100% of cited identifiers exist and are visible at the read basis (enforced by the verifier) |
| NFR-05 | Privacy | Zero individual protected values disclosed to unauthorised principals, including through aggregates, agents, caches and documents |
| NFR-06 | Explainability | Every ranking, grade and refusal has a deterministic, human-readable reason |
| NFR-07 | Availability | Early production target 99.5%; enterprise target 99.9%+ (assumptions, to be set per contract) |
| NFR-08 | Latency | Interactive ask p50 < 6 s, p95 < 15 s (assumption; dominated by model calls). Metric-only and evidence views < 1 s |
| NFR-09 | Scale | See §3.3 |
| NFR-10 | Residency and retention | Per-tenant region, provider and retention policy. Erasure supported without breaking ledger verifiability (§26) |
| NFR-11 | Cost control | Per-tenant budgets enforced before model calls (§18) |
| NFR-12 | Operability | Every component observable via OpenTelemetry; every rebuildable store rebuildable from the ledger |

### 3.3 Sizing assumptions (to be confirmed by product strategy)

| Dimension | Early production | Enterprise |
|---|---|---|
| Tenants | 1–50 | 50–2,000 |
| Employees per tenant | 20–2,000 | up to 50,000 |
| Source systems per tenant | 5–15 | 15–60 |
| Records ingested per year (all tenants) | 10⁶–10⁷ | 10⁸–10⁹ |
| Text documents/messages | 10⁵–10⁶ | 10⁷–10⁹ |
| Concurrent interactive users | 10–200 | 1,000–10,000 |

---

## 4. Production architecture overview

### 4.1 Planes

The system has seven logical planes. A plane is a responsibility boundary, not necessarily a deployable.

| Plane | Responsibility | Principal components |
|---|---|---|
| **Edge** | Authentication, rate limiting, request admission | API gateway, identity integration |
| **Control** | Tenancy, cells, authorization, disclosure control, policy administration, keys | Tenant directory and cell router, ReBAC engine, ABAC policy engine, disclosure auditor, key management |
| **Ingestion** | Pull or receive source data, land it immutably, validate, parse, admit in sealed batches | Connector workers, raw landing, validation and quarantine, parsers, admission sequencer |
| **Data** | System of record and derived projections | PostgreSQL cluster per cell (ledger, judgement, knowledge, projection, policy, runtime schemas), object storage, audit store, search/vector projections |
| **Intelligence** | Question understanding, planning, metric execution, retrieval, evidence compilation, composition, verification, grading, risk ranking | Answer pipeline, governed metrics engine, retrieval service, evidence compiler, verifier, risk engine |
| **Agent** | Employee agents, their knowledge boundaries and governed memory; isolated simulation | Agent runtime, memory store, simulation namespace |
| **Platform** | Durable execution, model access, caching, telemetry, deployment | Workflow runtime, `LLMClient`, cache, observability stack |

### 4.2 Lifecycle of a question (narrative)

1. **Edge.** The gateway authenticates the principal (OIDC) and resolves the tenant through the tenant directory. The request is routed to the tenant's cell.
2. **Session context.** The core opens a transaction-scoped context: tenant, principal, session knowledge point (a sealed admission batch, §8).
3. **Pre-screen.** Deterministic refusal patterns run first: individual pay, attrition ranking, private-message reading, personal-leave reasons, activity productivity. A match ends the request with a refusal and an alternative.
4. **Temporal parse.** Deterministic code resolves the mode (KNOWN_AT, RECORDED_BY, VALID_AT, CURRENT) and the cutoffs K, R, V. Ambiguity is stated in the answer, never resolved silently.
5. **Plan.** A bounded planner (LLM, schema-constrained) proposes calls to **declared operations only**: governed metrics, retrieval, graph traversal, timeline and diff.
6. **Execute.** Each call is authorized before execution:
   - tenant guard (RLS);
   - relationship check (ReBAC);
   - attribute policy (ABAC);
   - for aggregates over protected measures, the disclosure auditor.
7. **Compile evidence.** Code deduplicates, attaches lineage, conflicts and coverage gaps, and excludes any data class above the principal's clearance **before** any prompt is assembled.
8. **Compose.** One model call produces structured claims, each citing evidence identifiers.
9. **Verify.** The verifier decomposes claims and checks each citation:
   - deterministically: existence, visibility, grade cap, numbers and entities, policy, hedges;
   - by an independent entailment pass.
   
   It can only drop or downgrade claims.
10. **Grade and decide.** Deterministic code assigns the evidence grade and status: refused > abstained > conflict > qualified > answered.
11. **Record.** The answer, its claims, citations, read basis, policy, definition and model versions, and disclosure-ledger rows are committed in one transaction. An audit event is appended.

### 4.3 Lifecycle of source data (narrative)

1. A connector worker pulls an increment (or receives a webhook or file drop), including ACL and identity streams where available.
2. Raw objects are written to immutable object storage with a manifest (source, extraction time, schema version, content hash).
3. Contract validation runs. Failures are quarantined with a reason and become an evidence-quality signal.
4. Parsers (document parsers run in a sandbox) produce source-record versions.
5. The admission sequencer assigns the batch a strictly increasing sequence number. Records, cursor state and raw pointers commit together. The batch is **sealed** only after everything has committed.
6. Projection workers derive knowledge (entities, attribute values, edges, facts, conflicts) and domain read models. Indexing workers update lexical and vector projections. Both are idempotent for the batch.
7. Downstream consumers react: risk snapshot refresh, ReBAC tuple projection, cache invalidation. They observe only sealed batches.

---

## 5. System components and responsibilities

### 5.1 Component catalogue

| # | Component | Plane | Approach | Owns data |
|---|---|---|---|---|
| C01 | API gateway | Edge | Integrate (managed gateway or Envoy) | — |
| C02 | Identity integration | Edge/Control | Integrate (customer IdP via OIDC/SAML/SCIM) | Principal and group mirror (runtime) |
| C03 | Tenant directory and cell router | Control | Build | Tenant → cell mapping, tenant lifecycle state |
| C04 | Connector workers | Ingestion | Build, with Integrate for selected libraries (§26) | Connector cursors (ledger, per batch) |
| C05 | Raw landing | Ingestion/Data | Adopt (S3-compatible object storage with object lock) | Raw objects, manifests |
| C06 | Validation, quarantine and parsing | Ingestion | Build; document parsers Integrate (sandboxed) | Quarantine records |
| C07 | Admission sequencer | Ingestion | Build | `ledger.admission_batch`, sealed watermark |
| C08 | Evidence ledger | Data | Build on PostgreSQL 18 | Source-record versions, ACL records, identity records |
| C09 | Judgement store | Data | Build | Versioned human decisions: aliases, conflict resolutions, metric definitions, risk models, persona specs |
| C10 | Projection workers | Data | Build | `knowledge.*`, `projection.*` (rebuildable) |
| C11 | Indexing workers | Data | Build over pgvector and PostgreSQL FTS (OpenSearch later) | Chunk tables, vector and lexical indexes |
| C12 | Entity resolution | Data | Build; probabilistic matching Integrate (Splink) | Resolution decisions (judgement), candidates |
| C13 | Governed metrics engine and policy compiler | Intelligence | Build | Metric definitions (judgement), compiled-plan cache |
| C14 | Retrieval service | Intelligence | Build | — (reads projections) |
| C15 | Evidence compiler | Intelligence | Build | — (produces evidence packs) |
| C16 | Answer pipeline orchestrator | Intelligence | Build | `runtime.answer*` |
| C17 | Verifier | Intelligence | Build | Verification records (runtime) |
| C18 | Risk engine | Intelligence | Build | `knowledge.risk_snapshot`, `risk`, `risk_signal` |
| C19 | Disclosure auditor | Control | Build | `policy.disclosure_ledger` |
| C20 | Relationship authorization (ReBAC) | Control | Integrate (SpiceDB) behind `RelationshipStore` | Current relationship projection only |
| C21 | Attribute policy (ABAC) | Control | Integrate (OPA) behind `PolicyDecisionPoint` | Policy bundles (versioned) |
| C22 | Agent tool authorization | Control | Integrate (Cedar) behind `PolicyDecisionPoint` | Agent tool policies |
| C23 | Agent runtime | Agent | Build | Sessions (runtime) |
| C24 | Agent memory store | Agent | Build on PostgreSQL | `runtime.agent_memory*` |
| C25 | Simulation namespace | Agent | Build | Simulated events (isolated) |
| C26 | Durable workflow runtime | Platform | Integrate (DBOS initially; Temporal at scale) behind `ActivityRunner` | Workflow journal |
| C27 | `LLMClient` | Platform | Build | Budget reservations, model call records |
| C28 | Cache | Platform | Integrate (Valkey) when multi-instance | — (exact-key, policy-scoped) |
| C29 | Observability | Platform | Adopt (OpenTelemetry, OpenLLMetry); trace store Integrate | Telemetry |
| C30 | Audit store | Data/Control | Build (append-only, hash-chained); external anchoring Integrate (§26) | Audit events |
| C31 | PostgreSQL operations | Platform | Adopt (CloudNativePG ≥ 1.30, PgBouncer) | — |
| C32 | Historical ACL evaluator | Control | Build, behind `HistoricalAclEvaluator` | — (reads Veloxa-owned ACL, identity and group-membership history in PostgreSQL; writes evaluation provenance with the answer or agent turn) |

### 5.2 Component specifications

For each major component: responsibility, inputs → outputs, data ownership, interfaces, security boundary, failure behaviour and scaling.

**C03 Tenant directory and cell router**

| Aspect | Specification |
|---|---|
| Responsibility | Authoritative mapping tenant → cell; tenant lifecycle (provisioning, active, moving, suspended, erased); routing of every request to the tenant's cell |
| Inputs → outputs | Authenticated tenant claim → cell endpoint, tenant flags (`synthetic`, residency, isolation tier) |
| Data ownership | Directory database (global, small, replicated); no tenant business data |
| Interfaces | Gateway (lookup), provisioning workflows, tenant-move workflow (§24.3) |
| Security boundary | Only control-plane services may write. A tenant identifier is a system-generated UUID, never embedded in database, schema or role names |
| Failure | Read-mostly with cached lookups. If the directory is unavailable, existing cached routes continue; new tenant onboarding pauses |
| Scaling | Replicated reads; writes are rare |

**C04 Connector workers**

| Aspect | Specification |
|---|---|
| Responsibility | Incremental extraction of records, **ACLs, identities and group memberships**, with checkpointed cursors |
| Inputs → outputs | Source APIs, webhooks, file drops → raw objects + manifest + parsed batch candidates + cursor state |
| Data ownership | Cursor state, committed in the same admission batch as the records it covers |
| Interfaces | `Connector` contract (§5.3, §7.1); raw landing; admission sequencer |
| Security boundary | **Separate deployable.** Holds source credentials (secret manager, per tenant, per source). Network egress only to the source and to internal ingestion endpoints. Never reachable from the internet |
| Failure | At-least-once extraction. Idempotent admission via (source, native id, version) keys. A failed batch never seals. Retries with backoff and source rate-limit handling. Late updates are captured with a lag window and boundary deduplication |
| Scaling | Horizontally by (tenant, source); one active sync per (tenant, source) enforced by a queue lock |

**C07 Admission sequencer**

| Aspect | Specification |
|---|---|
| Responsibility | Assign strictly increasing `batch_seq`; seal a batch only after its records, ACL records, raw pointers and cursor state have committed; publish the sealed watermark |
| Inputs → outputs | Candidate batch → sealed `admission_batch` row; watermark update event (outbox) |
| Data ownership | `ledger.admission_batch` |
| Interfaces | Connector workers (submit), projection/indexing workers and consumers (observe the sealed watermark), temporal module (resolve K) |
| Security boundary | Only the ingestion role may write. Manual K is allowed only for forward backfill under an administrative workflow |
| Failure | Single writer per cell (one admission worker or a transaction-scoped advisory lock). On crash, unsealed batches are discarded or resumed idempotently; the watermark never moves backwards |
| Scaling | Batching amortises contention. Multiple cells scale horizontally. A single cell's sequencer is not on the interactive path |

**C08 Evidence ledger**

| Aspect | Specification |
|---|---|
| Responsibility | Immutable store of source-record versions with admission batch, recorded-time interval and basis, data class, raw-object pointer and parser version |
| Inputs → outputs | Admission batches → append-only rows |
| Data ownership | `ledger.*` |
| Interfaces | Projection workers, evidence compiler (read), audit (lineage) |
| Security boundary | INSERT-only for the ingestion role; SELECT for service roles under RLS; no UPDATE or DELETE grants. Corrections are new versions with `supersedes` and optimistic `expected_version` |
| Failure | Constraint violations reject the batch. Ledger loss is recovered from backups plus raw landing re-admission |
| Scaling | Partitioned by tenant (and time for large tenants); tenant-leading indexes |

**C10 Projection workers**

| Aspect | Specification |
|---|---|
| Responsibility | Derive `knowledge.*` (entities, attribute values, edges, facts with lineage, conflicts) and `projection.*` (domain read models) from sealed batches and judgement versions |
| Inputs → outputs | Sealed batch + judgement versions → projection rows tagged with the batch range that produced them |
| Data ownership | Rebuildable projections |
| Interfaces | Admission watermark (input), consumers (read) |
| Security boundary | Write role for projections only; RLS applies |
| Failure | Idempotent per batch. A projection can be dropped and rebuilt to any sealed watermark. Readers never see a projection beyond the sealed watermark |
| Scaling | Per tenant; parallel by projection family |

**C11 Indexing workers and C14 Retrieval service**

| Aspect | Specification |
|---|---|
| Responsibility | Maintain lexical (PostgreSQL FTS) and dense (pgvector) projections of prose records with tenant, ACL, admission and data-class attributes. Execute pre-authorized retrieval (§12) |
| Inputs → outputs | Sealed batches → chunk rows and indexes. Query + authorized principal set + K → candidate evidence (already filtered inside SQL) |
| Data ownership | `projection.chunk*` |
| Interfaces | `Retriever` / `ContextCompiler` (§5.3) |
| Security boundary | ACL, tenant, admission and data-class filters run inside SQL. Residual checks run in a quarantine buffer before anything is logged, cached, reranked or sent to a model (§12.4) |
| Failure | Index loss → rebuild from the ledger. Degraded mode: lexical only |
| Scaling | Tenant-partitioned tables with per-partition indexes. OpenSearch projection when text volume or relevance needs exceed PostgreSQL (§24) |

**C13 Governed metrics engine and policy compiler**

| Aspect | Specification |
|---|---|
| Responsibility | Execute **declared, versioned** metric definitions with explicit time semantics, data class and disclosure rules. Compile to parameterized SQL with tenant, temporal and policy predicates |
| Inputs → outputs | (metric, dimensions, filters, mode, K, R, V, principal) → result + facts with lineage + disclosure rows |
| Data ownership | Definitions are judgement artefacts; compiled plans are cached per definition version |
| Interfaces | Planner (declared operations), PDP (ABAC filters compiled to SQL), disclosure auditor (pre-release), evidence compiler |
| Security boundary | Bind parameters only. No templating and no string interpolation of principals or attributes. The LLM never authors SQL. Every query path goes through the compiler |
| Failure | An invalid definition is rejected at activation. Runtime errors → the metric is unavailable → the answer abstains for that claim |
| Scaling | Read replicas; result caching keyed by policy context (§11.6) |

**C16 Answer pipeline orchestrator and C17 Verifier**

| Aspect | Specification |
|---|---|
| Responsibility | Run the stages in §13: pre-screen, temporal parse, plan, execute, compile, compose, verify, grade, decide, record |
| Inputs → outputs | Question + context → `Answer` object (§19.4) + persisted answer, claims, citations and audit |
| Data ownership | `runtime.answer`, `answer_claim`, `answer_citation`, `verification` |
| Interfaces | Metrics engine, retrieval, evidence compiler, `LLMClient`, disclosure auditor, audit |
| Security boundary | Only declared operations; each call authorized. The model receives only the compiled evidence pack. Verifier outcomes can only drop or downgrade |
| Failure | Model unavailable → deterministic "facts only" response listing compiled facts with citations, status `qualified`. Verifier unavailable → claims are not shown as verified |
| Scaling | Stateless; horizontally scaled; bounded steps and tokens per request |

**C18 Risk engine**

| Aspect | Specification |
|---|---|
| Responsibility | Detect risk signals, quantify exposure where evidence permits, rank by the approved model (§16), and produce snapshots per (K, R, V) |
| Inputs → outputs | Facts and metrics at a read basis + the risk-model version → ranked snapshot with reasons, quantification states and evidence |
| Data ownership | `knowledge.risk_snapshot`, `risk`, `risk_signal` |
| Interfaces | Metrics engine, briefing API, workflow runtime (scheduled snapshots) |
| Security boundary | Uses only principal-visible evidence for the requesting principal's view. People-related criteria use allocation records only (§16.6) |
| Failure | Missing evidence → the risk is shown as unquantified or held back; numbers are never invented |
| Scaling | Precomputed per tenant and admission batch; on-demand for historical bases |

**C19 Disclosure auditor**

| Aspect | Specification |
|---|---|
| Responsibility | Decide, value-independently, whether releasing an aggregate over protected measures would let the principal determine any protected individual value or small-group total (§15) |
| Inputs → outputs | Candidate coefficient rows + principal ledger → allow / coarsen / deny + ledger append |
| Data ownership | `policy.disclosure_ledger` (append-only) |
| Interfaces | Metrics engine, evidence drawer, agent runtime, exports — every channel that can reveal an aggregate |
| Security boundary | Reads cohort structure, never values. Runs before release. Serialized per ledger scope |
| Failure | **Fail closed**: if the auditor is unavailable, protected aggregates are not released |
| Scaling | Incremental reduced basis per ledger; sparse exact elimination |

**C20–C22 Authorization engines**

| Aspect | Specification |
|---|---|
| Responsibility | **Current authorization only:** may the requester access this information *now*? ReBAC answers "can principal P see object O" and "which objects of type T can P see" over the **current** relationship projection. ABAC applies attribute rules (data class, purpose, role, region), compiled to SQL filters where possible. Agent-tool policy authorizes any tool an agent may call. These engines are **never** asked historical questions; those go to C32 (§10.7) |
| Inputs → outputs | Check / lookup / compile requests → decisions with consistency token and policy version |
| Data ownership | Engines hold derived state only. **Permission history lives in Veloxa's bitemporal ACL records** (§10) |
| Interfaces | `RelationshipStore`, `PolicyDecisionPoint` |
| Security boundary | Not reachable from outside the cell; mutual TLS; decision logs with masking |
| Failure | Fail closed. ReBAC state is rebuildable from ACL history |
| Scaling | Engine-native horizontal scaling; per-cell deployment |

**C32 Historical ACL evaluator**

| Aspect | Specification |
|---|---|
| Responsibility | Answer a **historical** question about a source principal (typically the person an agent represents), using ACL, identity and group-membership history admitted by K. Two modes (§10.7): <br>• `POINT_IN_TIME`: could the principal access object O at the instant t_h implied by a `ReadBasis`? <br>• `ELIGIBILITY_DURING_WINDOW`: did the principal have access to O at any instant within a bounded window that ends no later than t_h and the principal's knowledge cutoff? <br>It returns `allowed`, `denied` or `unknown` with provenance. An `allowed` result shows **eligibility to access, not that the principal read the object**. It never answers whether the *requester* may see anything now |
| Inputs → outputs | (subject, permission, resource, `ReadBasis`, mode, window) → `HistoricalAclResult` (§5.3): outcome, mode, historical instant (`historical_at`), evaluation run time (`evaluated_at`), window, resource-existence evidence, eligible intervals, grant relations, ACL and membership record versions used, reason code |
| Data ownership | None of its own. Reads `ledger` ACL, identity and membership records through their bitemporal projections (`knowledge.acl_grant`, `knowledge.group_membership`, `knowledge.identity_link`). Results are recorded as provenance on the answer or agent turn that used them |
| Interfaces | Agent runtime and evidence compiler (KB computation, §14.2); evidence and provenance views ("who could access this at the time", subject to current authorization on the ACL data itself); never the current-authorization path |
| Security boundary | Can only **narrow** what is used, never widen it. A historical `allowed` is never a substitute for current authorization. The ACL and membership records it reads are data with their own data class and are disclosed only under current ABAC policy |
| Failure | **Fail closed**: missing, ambiguous, stale or unevaluable history, an unbounded or invalid window, and a resource whose existence at the instants examined cannot be established, yield `unknown`, which every caller treats as not allowed. Evaluator unavailable → no historical grants → affected items are excluded from KB |
| Scaling | Set-oriented SQL evaluation over tenant-partitioned projections with tenant-leading indexes (range intersection for windows); batch evaluation per agent turn; memoised per (subject, `ReadBasis`, mode, window) within a request |

**C23/C24 Agent runtime and memory**

| Aspect | Specification |
|---|---|
| Responsibility | Interview sessions for employee agents; compile each turn's evidence from KB ∩ AZ ∩ policy (§14); persist governed memory with isolation and audited invalidation |
| Inputs → outputs | Message + session (agent, interviewer, K) → agent turn with cited factual claims, persona references, boundary note; memory rows |
| Data ownership | `runtime.agent_session`, `agent_memory`, `agent_memory_event` |
| Interfaces | Evidence compiler, verifier, `LLMClient`, disclosure auditor (interviewer's ledger), historical ACL evaluator (KB) and current authorization (AZ) |
| Security boundary | Agents hold no credentials and have no outbound action tools. Agent tool policy is enforced by the agent-tool PDP (Cedar). KB uses historical ACL evaluation of the represented employee; AZ always uses the interviewer's **current** authorization (§14.2) |
| Failure | Model unavailable → the session reports unavailability. Memory writes are transactional with the turn |
| Scaling | Stateless runtime; memory partitioned by tenant |

**C26 Durable workflow runtime**

| Aspect | Specification |
|---|---|
| Responsibility | Durable execution for syncs, admission sealing, projection and indexing, scheduled risk snapshots, approval waits, simulations, tenant moves |
| Inputs → outputs | Workflow start events → journaled steps with idempotency keys |
| Data ownership | Workflow journal (DBOS system tables in PostgreSQL initially) |
| Interfaces | `ActivityRunner` (§17) |
| Security boundary | Workflows run as service roles with the minimum database privileges for their activities |
| Failure | Resume from the journal. Step-name mismatches fail loudly. Activities are idempotent |
| Scaling | DBOS within service processes initially; Temporal when isolation or scale triggers fire (§17.5) |

**C27 `LLMClient`**

| Aspect | Specification |
|---|---|
| Responsibility | The single path to models: routing by stage and sensitivity; budget reservation; residency and model-permission policy; redaction; structured outputs; retries and fallbacks; prompt and version pinning; record/replay; replay-aware telemetry |
| Inputs → outputs | Typed request (stage, tenant, data classes present, schema) → validated structured response + call record |
| Data ownership | `runtime.llm_call`, `runtime.budget_reservation` |
| Interfaces | Pipeline, agents, verifier, risk narrative |
| Security boundary | Provider keys in the secret manager; never in prompts or logs; restricted to the service account |
| Failure | Budget exhausted → refusal of model-dependent stages (facts-only mode). Provider outage → fallback endpoint permitted by residency policy, else facts-only |
| Scaling | Stateless; per-tenant concurrency limits |

### 5.3 Day-one interfaces (contracts)

These contracts are fixed before implementation. Implementations behind them are replaceable.

```python
# Illustrative contract sketches (not implementation)

class ReadBasis(Protocol):            # §8
    tenant_id: UUID
    batch_seq: int                    # sealed admission point (K)
    mode: Literal["CURRENT", "KNOWN_AT", "RECORDED_BY", "VALID_AT"]
    recorded_cutoff: datetime | None  # R
    valid_at: datetime | None         # V

class AdmissionSequencer(Protocol):
    def open_batch(self, tenant_id: UUID, source: str) -> int: ...
    def seal(self, tenant_id: UUID, batch_seq: int) -> None: ...      # after records + cursor commit
    def sealed_watermark(self, tenant_id: UUID) -> int: ...

class Connector(Protocol):            # §7.1
    def extract(self, cursor: Cursor) -> Iterator[RecordEnvelope]: ...
    def extract_acls(self, cursor: Cursor) -> Iterator[AclEnvelope]: ...
    def extract_identities(self, cursor: Cursor) -> Iterator[IdentityEnvelope]: ...

class PolicyDecisionPoint(Protocol):  # ABAC + agent-tool policies
    def decide(self, principal, action, resource, context) -> Decision: ...
    def compile_filter(self, principal, resource_type, context) -> SqlPredicate: ...

class RelationshipStore(Protocol):    # ReBAC current projection: CURRENT authorization only
    def check(self, subject, permission, resource, consistency) -> CheckResult: ...
    def lookup_resources(self, subject, permission, resource_type, consistency) -> Iterator[str]: ...

HistoricalAclMode = Literal["POINT_IN_TIME", "ELIGIBILITY_DURING_WINDOW"]   # §10.7; the caller always states the mode

@dataclass(frozen=True)
class AclWindow:                      # half-open [start, end); both bounds required (§10.7)
    start: datetime
    end: datetime                     # <= min(t_h, the subject's knowledge cutoff)

@dataclass(frozen=True)
class ResourceExistence:              # §10.7: existence evidence for O, from records admitted by K
    created_at: datetime | None       # recorded creation or event time, if the source records one
    ended_at: datetime | None         # admitted deletion or end time, if any
    observed: tuple[datetime, datetime] | None   # earliest and latest admitted observations of O
                                                 # (an observation is never treated as O's creation)

class HistoricalAclEvaluator(Protocol):   # §10.7: historical source-ACL evaluation (never current authorization)
    def evaluate(self, subject: PrincipalRef, permission: str, resource: ResourceRef,
                 basis: ReadBasis, mode: HistoricalAclMode,
                 window: AclWindow | None = None) -> HistoricalAclResult: ...    # window required iff ELIGIBILITY_DURING_WINDOW
    def evaluate_many(self, subject: PrincipalRef, permission: str, resources: list[ResourceRef],
                      basis: ReadBasis, mode: HistoricalAclMode,
                      window: AclWindow | None = None) -> dict[ResourceRef, HistoricalAclResult]: ...

@dataclass(frozen=True)
class HistoricalAclResult:
    outcome: Literal["allowed", "denied", "unknown"]   # callers treat "unknown" exactly like "denied"
    mode: HistoricalAclMode
    basis: ReadBasis                                   # K (batch_seq), query mode, R, V used
    historical_at: datetime                            # the historical instant t_h derived from the basis (§10.7)
    evaluated_at: datetime                             # wall-clock time the evaluation actually ran (audit only;
                                                       # never an input to the decision)
    window: AclWindow | None                           # the window requested (ELIGIBILITY_DURING_WINDOW only)
    resource_existence: ResourceExistence | None       # O's existence evidence used (both modes); None if none admitted
    eligible_intervals: list[tuple[datetime, datetime]]  # sub-intervals of the instants examined in which O is known
                                                         # to exist and every link of the grant held at once
    grant_relations: list[str]                         # relations that produced the grant, e.g. "author", "recipient",
                                                       # "group_member", "role_entitlement" (mapped to knowledge basis, §14.2)
    acl_records: list[RecordVersionRef]                # ACL record versions relied on
    membership_records: list[RecordVersionRef]         # group/identity records relied on (valid at the instants examined)
    acl_source_version: str | None
    acl_synced_at: datetime | None
    reason: Literal["grant_valid", "explicit_deny", "no_grant", "no_acl_history",
                    "snapshot_only_before_extraction", "membership_ambiguous",
                    "identity_unresolved", "stale_beyond_limit", "depth_limit",
                    "partial_history", "window_invalid", "resource_availability_unknown",
                    "resource_not_available", "evaluator_error"]
# "allowed" means the subject was ELIGIBLE to access the object. It is never evidence that the subject read it.

class DisclosureAuditor(Protocol):    # §15
    def evaluate(self, scope: LedgerScope, rows: list[CoefficientRow]) -> AuditDecision: ...

class MetricEngine(Protocol):         # §11
    def run(self, metric: str, dims: dict, basis: ReadBasis, principal) -> MetricResult: ...

class Retriever(Protocol):            # §12
    def search(self, query: str, basis: ReadBasis, principal_set: list[str], k: int) -> list[Evidence]: ...

class ContextCompiler(Protocol):      # §13
    def compile(self, plan_results, basis: ReadBasis, principal) -> EvidencePack: ...

class MemoryStore(Protocol):          # §14
    def append(self, scope: MemoryScope, item: MemoryItem) -> None: ...
    def recall(self, scope: MemoryScope, basis: ReadBasis, query) -> list[MemoryItem]: ...

class ActivityRunner(Protocol):       # §17
    def start(self, workflow: str, args, idempotency_key: str) -> WorkflowHandle: ...

class LLMClient(Protocol):            # §18
    def call(self, request: ModelRequest) -> ModelResponse: ...
```

---

## 6. Data architecture and PostgreSQL schema boundaries

### 6.1 Stores

| Store | Technology | Content | Mutability |
|---|---|---|---|
| System of record (per cell) | **PostgreSQL 18** (Adopt) | Ledger, judgements, knowledge and domain projections, policy, runtime | See §6.2 |
| Raw landing | S3-compatible object storage with object lock (Adopt) | Original source payloads + manifests | Immutable (WORM) |
| Audit store | Separate database/account with append-only grants and periodic external export (Build) | Audit events, hash chain | Append-only |
| Search projection | PostgreSQL FTS (initial) → OpenSearch (trigger, §24) | Lexical index over prose chunks | Rebuildable |
| Vector projection | pgvector ≥ 0.8 in PostgreSQL (Adopt) | Embeddings per chunk | Rebuildable |
| Graph projection | Not deployed initially (§12.7, §26) | — | Rebuildable if added |
| Cache | Valkey (Integrate, when multi-instance) | Exact-key, policy-scoped results | Ephemeral |
| Workflow journal | DBOS system tables in PostgreSQL (Integrate) → Temporal persistence at scale | Workflow state | Managed by the runtime |

### 6.2 Schema boundaries (one PostgreSQL cluster per cell)

| Schema | Contents | Mutability | Writer roles | Reader roles | Rebuildable from |
|---|---|---|---|---|---|
| `raw_index` | Manifest of raw objects (hash, source, extraction time, schema version, object key) | Insert-only | `veloxa_ingest` | Service roles | Object storage |
| `ledger` | `admission_batch`, source-record versions, **ACL history** (source ACL records, identity-link records, group-membership records, each versioned with admission, recorded and valid time), connector cursors | **Insert-only** | `veloxa_ingest` | Service roles (RLS) | Backups + raw re-admission |
| `judgement` | Versioned human decisions: alias and entity resolutions, conflict resolutions, metric definitions, risk-model versions, persona specs, assumption registry | Insert-only versions | Admin workflows | Service roles (RLS) | Version-controlled source files |
| `knowledge` | Entities, aliases (resolved), attribute values, edges, facts and `fact_input` lineage, conflicts and sides, risk snapshots, **bitemporal ACL projections** (`knowledge.acl_grant`, `knowledge.group_membership`, `knowledge.identity_link`, read by C32) | Rebuildable projection | Projection role | Service roles (RLS) | `ledger` + `judgement` |
| `projection` | Domain read models (people, org, issues, incidents, finance lines), chunk tables for retrieval | Rebuildable projection | Projection and indexing roles | Service roles (RLS) | `ledger` + `knowledge` |
| `policy` | Policy bundle versions, data-class assignments, **disclosure ledger** | Insert-only (ledger); versioned (policy) | Policy admin; auditor | Auditor, PDP adapters | Not rebuildable (ledger is history) |
| `runtime` | Sessions, answers, claims, citations, verification records, agent memory and events, approvals, LLM call records, budget reservations | Append-mostly (no destructive updates of history) | `veloxa_app` | `veloxa_app` (RLS) | Not rebuildable (operational history) |
| `audit` (hackathon only; production uses a separate store) | Audit events with hash chain | Insert-only | `veloxa_audit_writer` | Auditors | Not rebuildable |

**Rules:**

- **History is never destroyed.** No schema that holds history (`ledger`, `judgement`, `policy.disclosure_ledger`, `runtime`, `audit`) grants UPDATE or DELETE to service roles.
- **Projections can be dropped and rebuilt** to any sealed admission watermark.
- **ACL history is canonical in PostgreSQL.** The `ledger` ACL, identity-link and group-membership records are the only system of record for who could access what, and when. The `knowledge` ACL projections are rebuildable from them. The ReBAC engine (SpiceDB) holds only a derived **current** projection and is never a source of history (§10.2, §10.7).
- **Every relation carries `tenant_id`.** Every unique key and foreign key includes it (§9.3).

### 6.3 Universal columns and identifiers

| Column | Applies to | Meaning |
|---|---|---|
| `tenant_id uuid` | All | Tenant. First column of every key and index |
| `batch_seq bigint` | Ledger rows; projections (as the producing range) | Sealed admission point (§8) |
| `valid tstzrange` | Facts and attribute values | Valid time V; date-only bounds expanded in the tenant time zone |
| `recorded tstzrange`, `recorded_basis` | Facts, records | Source-recorded time R with its basis: `event_ts`, `document_date`, `implied`, `export_upper`, `transcript_upper` |
| `data_class` | Records, facts, chunks | `internal`, `confidential`, `personal`, `sensitive_personal`, `payroll_aggregate`, `private_comms`, `suppressed`, `assumption` |
| `version`, `supersedes` | Ledger and judgement rows | Append-only version chain |

**Identifiers:**
- Primary identifiers are **UUIDv7** (`uuidv7()`, built into PostgreSQL 18) or **natural keys** from the source (system + native ID + locator). Global sequences are never used as cross-tenant identifiers.
- Natural keys stay stable across rebuilds, so runtime references (answers, memories, audit) survive projection rebuilds. A startup check reports any dangling runtime references.

### 6.4 Illustrative DDL

```sql
-- Prerequisites (see §6.6): PostgreSQL 18.x latest minor; btree_gist; vector (pgvector >= 0.8)
CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TABLE ledger.admission_batch (
  tenant_id   uuid        NOT NULL,
  batch_seq   bigint      NOT NULL,          -- strictly increasing per tenant, assigned by the sequencer
  admitted_at timestamptz NOT NULL,          -- informational; ordering is by batch_seq
  sealed      boolean     NOT NULL DEFAULT false,
  sealed_at   timestamptz,
  PRIMARY KEY (tenant_id, batch_seq)
);

CREATE TABLE ledger.source_record (
  tenant_id      uuid        NOT NULL,
  record_id      text        NOT NULL,       -- natural key: system + native id (+ locator)
  version        int         NOT NULL CHECK (version >= 1),
  supersedes     int,                        -- previous version of the same record
  batch_seq      bigint      NOT NULL,
  raw_object     text        NOT NULL,       -- content hash in object storage
  parser_version text        NOT NULL,
  data_class     text        NOT NULL,
  extracted_at   timestamptz NOT NULL,
  payload        jsonb       NOT NULL,
  PRIMARY KEY (tenant_id, record_id, version),
  FOREIGN KEY (tenant_id, batch_seq) REFERENCES ledger.admission_batch (tenant_id, batch_seq)
);
-- INSERT-only: no UPDATE/DELETE grants. Version appends use optimistic expected_version checks.

-- Projection (rebuildable): bitemporal attribute values. "known" is derived from the version chain
-- and sealed batch sequence; it is maintained only in this projection, never in the ledger.
CREATE TABLE knowledge.attribute_value (
  tenant_id        uuid      NOT NULL,
  entity_id        text      NOT NULL,
  attribute        text      NOT NULL,
  value            jsonb     NOT NULL,
  status           text      NOT NULL CHECK (status IN ('confirmed','planned','reported')),
  valid            tstzrange NOT NULL CHECK (NOT isempty(valid)),
  known_batches    int8range NOT NULL CHECK (NOT isempty(known_batches)),  -- [first batch, superseding batch)
  recorded         tstzrange NOT NULL,
  recorded_basis   text      NOT NULL,
  source_record_id text      NOT NULL,
  source_version   int       NOT NULL,
  FOREIGN KEY (tenant_id, source_record_id, source_version)
    REFERENCES ledger.source_record (tenant_id, record_id, version),
  CONSTRAINT one_confirmed_value EXCLUDE USING gist
    (tenant_id WITH =, entity_id WITH =, attribute WITH =, valid WITH &&, known_batches WITH &&)
    WHERE (status = 'confirmed')
);
```

**Why an `EXCLUDE` constraint.** Bitemporal uniqueness ("no two confirmed values overlap in both valid and known time") needs two ranges and a predicate. `PRIMARY KEY … WITHOUT OVERLAPS` supports one range column and no predicate.

**Where `WITHOUT OVERLAPS` and `PERIOD` still fit.** Uni-temporal current-knowledge projections (for example, current reporting lines) may use `PRIMARY KEY (…, valid WITHOUT OVERLAPS)` and `PERIOD` foreign keys. Temporal foreign keys support only `NO ACTION`.

**Multi-valued relations** (team membership, reporting lines with planned changes) are modelled as bitemporal edges.

### 6.5 Versioning and migrations

| Artefact | Versioning |
|---|---|
| Raw objects | Content hash; a changed hash is a new object |
| Source records | `(record_id, version)`; corrections append |
| Judgements (aliases, conflicts, metric definitions, risk models, personas, assumptions) | Version rows with author, rationale and timestamp. The source files are version-controlled; answers record the versions they used |
| Code and rules | Build identifier stamped on answers, risk snapshots and projections |
| Database schema | Expand–contract migrations for all schemas. Projections may instead be rebuilt. History schemas are never migrated destructively |

### 6.6 Deployment prerequisites (PostgreSQL)

| Item | Requirement |
|---|---|
| Version | PostgreSQL 18.x at the latest minor release at deployment. Security advisories reviewed at each upgrade |
| `btree_gist` | Required for GiST exclusion and `WITHOUT OVERLAPS` constraints that combine `uuid`/`text` equality with range overlap (trusted extension) |
| `vector` (pgvector) | ≥ 0.8.0 for iterative index scans. Not in the core image: use a pgvector-enabled image or package |
| UUIDs | `uuidv7()` and `gen_random_uuid()` are core; no `pgcrypto` needed |
| Optional | `pg_trgm` (alias *suggestions* only), `pg_stat_statements` (reviewed before use in pooled cells), `pgaudit` (privileged-role statement audit) |
| `auto_explain` | `log_analyze` disabled for application roles in pooled cells (row-count side channel, §12.4) |
| Settings | Server `TimeZone = 'UTC'`. Tenant time zones are applied in the application. `app.*` settings are set transaction-locally only |
| Operator (Kubernetes) | CloudNativePG ≥ 1.30 with the Barman Cloud Plugin for backups; PgBouncer pooler |

---

## 7. Ingestion and evidence provenance

### 7.1 Connector contract (Build)

Every connector implements three streams and a cursor:

| Stream | Content | Required |
|---|---|---|
| Records | Source objects as envelopes: native ID, version or updated-at, payload, source timestamps, data-class hint | Yes |
| ACLs | Per object or container: principals with access (users, groups, external groups, domains) and their relation | Where the source exposes them. **Connector libraries rarely capture ACLs; this stream is Veloxa-built** |
| Identities and groups | Users, group memberships, external identity mappings | Where available |
| Cursor | Opaque checkpoint, capped at the value observed at sync start (signpost), with a lag window for late updates | Yes |

**Checkpoint rule:** a cursor advances only when the records, ACLs and identities it covers have committed **in the same admission batch**.

**Connector implementations** may use external libraries (dlt as a library; selected Airbyte connectors subject to licence review, §26) behind this contract. No connector library is trusted to produce ACL semantics.

### 7.2 Landing, validation, parsing

1. **Landing.** The raw payload is written to object storage under a content-addressed key, with a manifest row in `raw_index`.
2. **Validation.** Each source object type has a contract (schema + semantic checks such as arithmetic identities, referential presence and date-format rules). Failures go to quarantine with a reason; quarantine is visible to administrators. Answers touching a source with open quality incidents carry a qualifier.
3. **Parsing.** Structured exports are parsed by versioned parsers. Documents (PDF, office files, email) are parsed by a document-parsing library (Integrate; candidates in §26) running in an **isolated, network-restricted worker** with size and page limits, because document parsers have a history of SSRF and path-traversal vulnerabilities.
4. **Normalisation.**
   - Instants are stored in UTC.
   - Date-only values carry precision and are expanded in the tenant time zone.
   - Currency is stored in minor units.
   - Explicit format rules apply (e.g. day-month vs month-day dates).

### 7.3 Admission

Parsed records, ACL records, identity records, raw pointers and cursor state for a sync become one admission batch (§8.2). Duplicates are recognised by normalised content hash (for example, a printout that reproduces messages already ingested). The earliest admitted copy is canonical, and duplicates never count as independent corroboration.

### 7.4 Lineage (Build)

The provenance chain is stored as relations, not documents:

`raw object → source-record version → fact (via metric definition or transform, with fact_input rows) → answer claim → answer`

- Every link is a foreign-key-checkable relation.
- Every derived object's admission range is the maximum of its inputs (§8.4).
- Pipeline-run lineage (connector runs, projection runs) is emitted as OpenLineage run events for observability (Integrate). **OpenLineage is dataset- and column-level and is not used for record-level evidence provenance.**

### 7.5 Entity resolution (Build; matching Integrate)

| Step | Mechanism |
|---|---|
| Blocking and deterministic rules | Exact identifiers (emails, HR IDs), unique initials within a context population, surname + initial, vendor-name normalisation (proposal only) |
| Probabilistic matching | Fellegi–Sunter scoring (Splink, Integrate) for cross-system people and vendor matching |
| **Context-scoped aliases** | An alias is valid only within a declared context (e.g. issue-tracker assignee vs payroll processor). The same initials can resolve differently in different contexts |
| Human decision | Below the auto-accept threshold, and **always** for merges that affect people or sensitive data. Decisions are versioned judgements with evidence and confidence |
| Similarity never decides identity | Embeddings or string similarity may *suggest*; they never merge |
| Unresolved identifiers | Kept as raw strings and surfaced as "unresolved identity" in answers |

Resolution decisions are admitted like other judgements. A merge decided later never changes what was shown at an earlier read basis.

### 7.6 Conflicts

| Source of conflicts | Mechanism | Who decides |
|---|---|---|
| Structured reconciliation rules | Deterministic checks across systems (payroll vs ledger postings, invoices vs postings, reported figures vs system figures, statement vs version history) | The rule detects; a human resolves |
| Curated conflicts | Human-authored conflict records with sides citing records/facts, normalised values and resolution rationale | Human |
| Model-flagged candidates | During composition, the model may flag disagreement between cited records. Shown as "possible disagreement (unreviewed)" and queued for review | The model notices; a human decides |

A conflict is visible at a read basis only if at least two of its sides are visible there (§8.4).

---

## 8. Temporal architecture

### 8.1 Time axes

| Axis | Symbol | Meaning | Source |
|---|---|---|---|
| Admission (knowledge) | **K** | When the platform made a record available to users, as a **sealed admission batch** | Admission sequencer |
| Source-recorded | **R** | When the source system or author recorded it; an interval with a basis (uncertainty encoded) | Event timestamps, document dates, export upper bounds, implied-recording rules |
| Valid | **V** | When the fact was true in the world | Effective dates, event intervals |
| Extraction | E | When the connector extracted it | Connector |
| Ingestion | I | Wall-clock ingestion time | Audit only. **Never used for answers** |

Ordering invariants (tested): `R ≤ E ≤ admission` for extracted data.

### 8.2 Sealed admission sequence (Approved)

- **Assignment.** K is a **commit-ordered, strictly increasing `batch_seq`** per tenant, assigned by a single admission sequencer per cell. It is *not* a timestamp column default: `now()`, `transaction_timestamp()` and sequences are assigned before commit and are not commit-ordered.
- **Sealing.** A batch is sealed only after its records, ACL and identity records, raw pointers and connector cursor state have committed. The **sealed watermark** is the highest sealed `batch_seq`.
- **Requests.** A request for a K beyond the sealed watermark is rejected. A date-time K maps to the highest batch sealed at or before that instant.
- **Read basis.** Every answer, snapshot, memory and cache entry records the `batch_seq` it read.
- **Manual K** is permitted only for forward backfill, under an administrative workflow.

### 8.3 Query modes

| Mode | Use | Cutoffs |
|---|---|---|
| CURRENT | Default | K = current sealed watermark (clamped to the clock); R = V = now |
| KNOWN_AT(date) | "What did we know on X?" | K = last batch sealed by end of X (tenant time zone); R = V = K's time |
| RECORDED_BY(date) | "What had the company recorded by X, according to material admitted by K?" | K = current (or given); R = end of X |
| VALID_AT(date) | "What was true on X, by best current knowledge?" | K = current (or given); V = X; R unbounded (≤ K) |

**The mode is always echoed in the answer.**

**No silent re-interpretation.** A KNOWN_AT date before any admission returns "nothing had been admitted", then *offers* RECORDED_BY.

**Date-only inputs mean the end of that day** in the tenant's time zone.

### 8.4 Normative rules

| Rule | Statement |
|---|---|
| T1 | A record is **visible** iff its `batch_seq ≤ K` and its batch is sealed |
| T2 | A fact is **usable at R** iff every input is visible and the fact's `upper(recorded) ≤ R`. Recorded time belongs to the **fact** (field-level): one source row can yield facts with different recorded bases |
| T3 | A fact **holds at V** iff `V ∈ valid`. A snapshot-only fact (unknown valid start) holds only "as at extraction"; earlier states are reported as unknown |
| T4 | **Implied recording.** System-generated event fields have recorded = event time. HR master fields such as join dates have recorded ≤ the effective date (an assumption, logged). Snapshot attributes have recorded ≤ extraction. Undated transcripts have recorded ≤ the transcript file's creation upper bound |
| T5 | **Lineage inheritance.** Every derived object (fact, conflict, risk, entity, alias, persona item, memory, cached result, simulated event) carries `max(batch_seq of inputs)`. Runtime objects carry `max(session K, inputs)` |
| T6 | **Date expansion.** Date-only values become `[00:00, next day 00:00)` intervals in the tenant time zone |
| T7 | **Instants are compared as instants.** `timestamptz` and ranges in PostgreSQL; never as text |
| T8 | **Corrections are new versions.** "As known at K" uses the latest version admitted at or before K, so pre-correction views are reproducible |
| T9 | **Conversation context is scoped to one K.** Changing the knowledge point starts a new context; earlier turns at a later K never enter the prompt |
| T10 | **Cache keys** include tenant, principal permission-set hash, K, R, V, mode, policy version, definition versions and build identifier |

### 8.5 Leakage paths and controls

| Leakage path | Control |
|---|---|
| A query that forgets the visibility predicate | All reads go through a scoped data-access layer that injects the `ReadBasis`. A static check bans raw table access elsewhere. Every ledger and projection row carries `batch_seq` |
| Derived objects (facts, risks, entities, aliases, persona items) computed from later material | T5 lineage inheritance; property tests |
| Runtime objects stamped with wall-clock time | Stamped with session K (T5) |
| Chat history across a time-travel switch | T9 |
| "Latest" snapshot or cache lookups | Snapshots and caches are addressed only by explicit read basis (T10) |
| Conflict existence revealing later material | Visible only if ≥ 2 sides are visible |
| Planned organisational changes admitted later | Edges inherit admission; filtered by T1 |
| Simulated activity | Carries session K; never readable by evidence paths |
| Lexical ranking statistics computed over later documents | Accepted and documented (negligible), or per-K index (§26) |
| Author hindsight (rules written after seeing later material) | Process control: rules and anchors committed before later material is analysed; disclosed |

### 8.6 Erasure

An immutable ledger must still honour erasure obligations. The approved direction is crypto-shredding of payloads plus tombstone versions that keep the version chain and hash chain verifiable. The detailed design is deferred (§26).

---

## 9. Tenant isolation and security model

### 9.1 Cells

| Cell type | Isolation | Use |
|---|---|---|
| **Pooled cell** | Shared schemas; `tenant_id` on every row; PostgreSQL RLS as a mandatory backstop | Most tenants |
| **Silo cell** | Database (or cluster) per tenant, with tenant-specific credentials and keys | Tenants requiring high assurance, residency or customer-managed keys |

The **tenant directory and cell router** (C03) maps tenants to cells. Tenant moves (pooled→silo, cell→cell) use logical replication (§24.3). **No global table lacks `tenant_id`**: shared reference data is replicated per cell, so tenants can always move.

### 9.2 What RLS guarantees, and what it does not

**What RLS guarantees.** With an application-set tenant context, RLS prevents cross-tenant access caused by **application bugs**.

**What it does not guarantee.** It does **not** defend against a **compromised application server**, which could set any tenant context. High-assurance tenants therefore use silo cells with per-tenant credentials and network isolation. Which tenants require silo cells is a per-contract decision (§26).

### 9.3 RLS design (Approved)

**Policies and enforcement**

- **FORCE ROW LEVEL SECURITY** on every tenant table, so owners are subject to policy too.
- A **restrictive** tenant policy for `ALL` commands with both `USING` and `WITH CHECK`, plus permissive grants within it. Restrictive policies are AND-ed and cannot be widened by later permissive policies.

**Tenant context**

- Transaction-local only. Every unit of work is a transaction that starts with:

  ```sql
  SELECT set_config('app.tenant_id',  $1, true),
         set_config('app.principal_id', $2, true),
         set_config('app.batch_seq',  $3, true);
  ```

- Session-level `SET` is banned (lint), because it bleeds across pooled connections.
- **Fail closed.** Policies cast `current_setting('app.tenant_id')::uuid`. An unset, empty or malformed context raises an error instead of matching rows. After a transaction ends, a placeholder setting reads as an empty string, not NULL, and the cast of an empty string fails.

**Pooling**

- An application-side pool or PgBouncer in transaction mode. Session `SET`/`RESET` are not supported in transaction mode; protocol-level prepared statements are supported with `max_prepared_statements > 0`.
- `idle_in_transaction_session_timeout` set for application roles.

**Views and functions**

- Every view over tenant data uses `security_invoker = true`. Views otherwise apply the *owner's* privileges and RLS policies.
- `SECURITY DEFINER` functions are banned by default. Exceptions are allow-listed and:
  - owned by a role subject to FORCE RLS;
  - have a pinned `search_path`;
  - take an explicit tenant argument checked against the context;
  - have `EXECUTE` revoked from `PUBLIC`;
  - return aggregates only through the disclosure auditor.

**Covert channels**

- Unique, primary-key and foreign-key checks bypass RLS. Every unique key and FK therefore includes `tenant_id`, and composite FKs prevent cross-tenant references.

**Other relation types**

- Partitions: identical RLS on every partition; no direct grants on partitions.
- Materialized views: none over tenant data in pooled cells.

**Planner statistics**

- Run the latest minor release. Application roles have no `CREATE` privilege on any schema (no functions, operators or statistics objects). Verify that `pg_stats` exposes no rows for RLS tables to application roles.

**Startup self-checks.** A service refuses to start unless all of these hold:

1. Its role is not a superuser and has no `BYPASSRLS`.
2. It owns no relation in tenant schemas.
3. Every tenant relation has RLS and FORCE RLS enabled, plus the restrictive tenant policy.
4. Every view is `security_invoker`.
5. The server version meets the minimum.

**CI** runs the same catalog lint after migrations.

### 9.4 Database roles

| Role | Login | Key attributes | Privileges | Used by |
|---|---|---|---|---|
| `veloxa_owner` | No | NOSUPERUSER, NOBYPASSRLS | Owns schemas and tables (DDL) | Migrations via `SET ROLE` |
| `veloxa_migrator` | Yes | Member of owner | — | CI/deploy only |
| `veloxa_app` | Yes | NOBYPASSRLS, owns nothing, no `CREATE` | SELECT on knowledge/projection; INSERT on runtime; no TRUNCATE/REFERENCES | Core API, agent runtime |
| `veloxa_ingest` | Yes | as above | INSERT on `raw_index`, `ledger` | Connector workers, admission |
| `veloxa_projection` | Yes | as above | Write `knowledge`/`projection` | Projection and indexing workers |
| `veloxa_audit_writer` | Yes | as above | INSERT-only on audit (a trigger rejects UPDATE/DELETE) | All services |
| `veloxa_definer` | No | Subject to FORCE RLS | Owns allow-listed definer functions (e.g. auditor helpers) | — |
| `veloxa_breakglass` | Yes | **BYPASSRLS**, read-only | Read | Humans only: separate credentials, MFA, time-boxed grant, alerting, audit |

Individual payroll lines are unreadable by `veloxa_app` (restrictive `USING (false)` policy). Only the auditor's allow-listed function reads them, after the auditor approves an aggregate.

### 9.5 Non-database stores

| Store | Control |
|---|---|
| Caches, search and vector projections, logs, traces, the LLM gateway path | Not protected by RLS. **Tenant-keyed or tenant-partitioned**; cross-tenant lookups are tested to return nothing (§21) |
| Embeddings | Inherit the source's tenant and data class. Never shared across tenants |
| Tenant identifiers | System-generated UUIDs. Never embedded in database, schema or role names, or in cache keys without unambiguous encoding. Silo database names are derived from a hash within PostgreSQL's 63-byte identifier limit |

### 9.6 Encryption, keys, secrets

- TLS everywhere, including mutual TLS inside the cell for control-plane services.
- At-rest encryption, with per-tenant envelope data keys (KMS).
- Field-level encryption for `sensitive_personal` payloads.
- Customer-managed keys for silo tenants.
- Secrets (source credentials, provider keys) in a secret manager, scoped per service identity, never in images, prompts or logs.
- Erasure by crypto-shredding (§8.6).

---

## 10. Authorization and source ACL architecture

### 10.1 Layers

| Layer | Mechanism | Purpose | Approach |
|---|---|---|---|
| 1 | PostgreSQL RLS | Tenant backstop | Adopt (§9) |
| 2 | ReBAC (SpiceDB) | Source-faithful object permissions **as they stand now**: who can currently see this email, ticket, document, channel | Integrate behind `RelationshipStore` |
| 3 | ABAC (OPA) | Data class, purpose, role, region, time-mode rules; compiled to SQL filters and column masks where possible | Integrate behind `PolicyDecisionPoint` |
| 4 | Agent-tool authorization (Cedar) | Which tools an agent session may invoke, under which conditions | Integrate behind `PolicyDecisionPoint` |
| 5 | Disclosure auditor | Inference control over aggregates | Build (§15) |
| 6 | Model permissions | Which data classes may be sent to which provider, region or model | Build in `LLMClient` (§18) |
| 7 | Output validation | Verifier policy scan | Build (§13) |

**Every executor call is authorized before execution.** Evidence the principal cannot see never enters a prompt. Post-filtering after a model call is never a control.

These layers form **current authorization** and are mandatory for every requester-facing output. **Historical ACL evaluation (§10.7) is not a layer and grants nothing.** It answers a different question (what a source principal could access at a historical read basis) and can only further restrict what is used.

### 10.2 ACL history (Build)

- **ACLs are admitted records.** Connector ACL, identity and group-membership streams are admitted into the ledger as versioned records with admission, recorded and valid time. That makes "who could access X at time t, as known at K" answerable.
- **The ReBAC engine holds only the current projection,** derived from ACL history by a projection worker. Its own snapshot retention is short, so it is never the permission-history store, and it is never queried for historical questions.
- **Historical questions are answered by the historical ACL evaluator (C32, §10.7)** directly from this history in PostgreSQL.
- **The authorization engine can be rebuilt from ACL history at any time.**

### 10.3 Principal model

ACL principals are typed keys:

| Key | Meaning |
|---|---|
| `user:<id>` | An individual identity |
| `group:<id>` | A Veloxa-resolved group |
| `ext_group:<source>:<name>` | A source-system group, namespaced to avoid collisions |
| `domain:<name>` | Organisation-wide sharing, resolved against the actual roster |
| `is_public` | Separate boolean |

**Group expansion happens at query time,** so a membership change does not require re-indexing objects. There are two expansions, and they are never interchanged:

| Expansion | Used for | Memberships used |
|---|---|---|
| **Current** | Current authorization of the requester | Memberships valid **now**, from the current projection |
| **Historical** | Historical ACL evaluation of a source principal (§10.7) | Memberships admitted by K, from membership history, **valid at the instant examined**: t_h in `POINT_IN_TIME`; in `ELIGIBILITY_DURING_WINDOW`, the same instant at which the grant itself is valid. Current memberships are never substituted for historical ones, and vice versa |

### 10.4 Consistency

- **Recorded tokens.** Each answer records the ReBAC consistency token (ZedToken) used for its checks.
- **Fresh reads after revocation.** After admitting an ACL revocation, reads use `at_least_as_fresh` (with the token from the revocation write) or `fully_consistent`. Latency-optimised reads are permitted only where staleness has been explicitly accepted per data class.
- **Staleness fields.** `acl_synced_at` and `acl_source_version` are stored per object. Each data class has a maximum ACL staleness. Beyond it, retrieval runs a live residual check against the source (where supported) or excludes the object, and the evidence grade carries a staleness qualifier.
- **Residual checks fail closed.** A source whose residual check errors contributes no results.
- **ACL model migrations** use shadow-query comparison: old and new filters are run side by side and any disagreement is reported before cut-over.

### 10.5 ABAC data filtering

ABAC policies are evaluated by the PDP and, where possible, compiled into SQL predicates and column masks. The metrics engine and retrieval service bind them as parameters (§11). Decision logs are retained with masking and policy versions.

### 10.6 Agent-tool authorization

Agents have **no outbound action tools**. Any tool an agent session can reach (read-only lookups) is authorized by the agent-tool policy, evaluated against (agent, interviewer, tenant, purpose = interview, data classes). Proposed actions are drafts handed to a human approval workflow (§17).

### 10.7 Current authorization vs historical ACL evaluation

**Two distinct questions**

| | Current authorization | Historical ACL evaluation |
|---|---|---|
| Question | May the **requester** access this information **now**? | Could a **source principal** (e.g. the employee an agent represents) access object O **at the instant implied by a read basis**, or **at any instant within a bounded window** before it? |
| Mechanism | RLS → ReBAC (SpiceDB current projection, fresh consistency after revocations) → ABAC (OPA) → disclosure auditor (§10.1) | `HistoricalAclEvaluator` (C32) over Veloxa-owned bitemporal ACL, identity and group-membership history in PostgreSQL |
| Group memberships | Current | Valid at the instant examined, admitted by K |
| Result | Allow / deny (fail closed) | `allowed` / `denied` / `unknown`, with provenance. `allowed` means eligible to access, not "read" |
| Can grant access? | Yes: it is the only thing that can | **No.** It can only narrow what is used |
| Mandatory? | **Always**, for every requester-facing output | Only where a historical source-principal question is asked (agent knowledge, historical visibility views) |

**Authorization rule (normative)**

1. Every item shown to a requester, or used to produce something shown to a requester, must pass **current authorization of that requester**. There are no exceptions, and no historical result can substitute for it.
2. Where a historical question also applies, the effective decision is **current authorization AND historical evaluation = `allowed`**. It is never "current OR historical".
3. **A historical `allowed` never restores access revoked since.** If the requester's current authorization denies an item, it is excluded, whatever any historical evaluation says.
4. **`unknown` is treated as `denied`.** Missing, ambiguous or unevaluable history fails closed.
5. **Eligibility is not reading.** A historical `allowed`, in either mode, shows only that the subject *could* access the object. It is never evidence that the subject opened, read or remembers it. Knowledge resting on access alone is labelled `possible` or `role_plausible` (§14.2).

**Evaluation semantics**

*Historical instant t_h, derived from the `ReadBasis`:*

| Mode | t_h |
|---|---|
| `KNOWN_AT(K)` | The sealing time of batch K |
| `RECORDED_BY(R)` | R. Only ACL and membership records with `upper(recorded) ≤ R` are usable |
| `VALID_AT(V)` | V |
| `CURRENT` | Now, evaluated from history as known at the current sealed watermark |

t_h is returned as `historical_at`. `evaluated_at` is the wall-clock time at which the evaluation actually ran. It is recorded for audit only and never enters the decision, so replaying an evaluation later yields the same outcome with a different `evaluated_at`.

*Evaluation modes (the caller always states the mode; there is no implicit default):*

| Mode | Question | Instants examined | Used by |
|---|---|---|---|
| `POINT_IN_TIME` | Could the subject access O **at t_h**? | t_h only | Historical visibility views ("who could access this at the time"); any caller that needs access at one instant |
| `ELIGIBILITY_DURING_WINDOW` | Did the subject have access to O **at any instant** within a bounded window w = [w_start, w_end)? | Every instant in w | Agent knowledge boundaries (KB, §14.2) |

*Window rules (`ELIGIBILITY_DURING_WINDOW`):*
- Both bounds are explicit. `w_end ≤ min(t_h, the subject's knowledge cutoff)`, so a window never reaches past the read basis or the cutoff (e.g. an exit date).
- `w_start` is no earlier than the start of the subject's earliest identity link and no earlier than `w_end` minus the maximum lookback (D-10b, §26.1).
- A missing, unbounded, empty or out-of-range window → `unknown` (`window_invalid`).
- Within w, only instants at which O is **known to exist** can qualify (resource-existence rule, below).

*Resource-existence rule (both modes).* Each instant t examined is classified from O's existence evidence admitted by K (`ResourceExistence`, §5.3):

| State | When |
|---|---|
| **Known to exist** | t is at or after the recorded creation or event time, or at or after the earliest admitted observation of O; **and** t is before any admitted end; **and**, if t is after the latest admitted observation, the gap is within the data class's staleness limit (D-10a) |
| **Known not to exist** | A creation or event time is recorded and t precedes it; or an admitted deletion or end is at or before t |
| **Existence unknown** | Anything else. In particular: <br>• before the earliest observation when no creation time is recorded (a first observation is **not** treated as O's creation, and nothing is inferred about whether O existed earlier); <br>• beyond the staleness limit after the latest observation |

- `POINT_IN_TIME`: if O is known to exist at t_h, evaluation continues; if known not to exist, the result is `denied` (`resource_not_available`); if existence is unknown, it is `unknown` (`resource_availability_unknown`).
- `ELIGIBILITY_DURING_WINDOW`:
  - instants where O is known not to exist cannot qualify;
  - instants of unknown existence are treated as unevaluable;
  - `eligible_intervals` lie only where O is known to exist.

*Records usable for the evaluation:*
- ACL, membership and identity records with `batch_seq ≤ K` (sealed), at their latest version admitted by K (T8).
- Validity must contain the instant examined: t_h in `POINT_IN_TIME`; each candidate instant of w in `ELIGIBILITY_DURING_WINDOW`.
- Recorded-time rules follow §8.4 (T2–T4).

*Subject resolution:*
- The subject is a Veloxa person, mapped to source principals through identity-link records valid at the instant examined.
- An unresolved or ambiguous mapping → `unknown` (`identity_unresolved`).

*Group resolution:*
- Memberships valid at the instant examined are expanded transitively, with cycle detection and a configured depth limit.
- Exceeding the limit → `unknown` (`depth_limit`).
- Overlapping, contradictory or gapped membership intervals covering the instant examined → `unknown` (`membership_ambiguous`).

*Same-instant rule (both modes):* a grant counts only at an instant where **every link holds at once**: the identity link, each group membership on the path, and the grant itself are all valid, and no applicable explicit deny is valid. A membership valid at one instant and a grant valid at another never combine.

*Outcomes, `POINT_IN_TIME`:*

| Outcome | When |
|---|---|
| `allowed` | O is known to exist at t_h, and the same-instant rule is satisfied at t_h |
| `denied` | O is known not to exist at t_h (`resource_not_available`); or an explicit deny applies at t_h (deny overrides allow); or the object's ACL state at t_h is known and excludes the subject (`no_grant`) |
| `unknown` | Any of the following: <br>• O's existence at t_h cannot be established (`resource_availability_unknown`); <br>• no ACL history exists for the object at t_h (`no_acl_history`); <br>• the source exposes only a current ACL snapshot and t_h precedes its extraction (`snapshot_only_before_extraction`, the same rule as T3); <br>• ACL staleness relative to t_h exceeds the data-class limit (`stale_beyond_limit`); <br>• identity, membership or depth failures as above; <br>• evaluation error (`evaluator_error`) |

*Outcomes, `ELIGIBILITY_DURING_WINDOW`:*

| Outcome | When |
|---|---|
| `allowed` | At least one instant in w at which O is known to exist satisfies the same-instant rule, and the history covering that instant is evaluable. The qualifying sub-intervals are returned as `eligible_intervals` |
| `denied` | Every instant in w is either one at which O is known not to exist, or one at which O is known to exist and the history is evaluable with no instant satisfying the same-instant rule. Reasons: `resource_not_available` if O is known not to exist throughout w; otherwise `no_grant` or `explicit_deny` |
| `unknown` | No qualifying instant was found, and some part of w is unevaluable. Reasons: <br>• `resource_availability_unknown` when the unevaluable part is one of unknown existence; <br>• `partial_history`, or the specific reason, when it is unevaluable ACL history; <br>• `window_invalid` when the window is invalid |

*Party-derived relations:*
- Sender, recipient, cc, assignee, author and responder relations are admitted as ACL records whose validity starts at the record's event time.
- They end only if a later admitted ACL record ends them.
- The relation that produced a grant is returned in `grant_relations`, so callers can tell participation (e.g. author, sender) from access alone (e.g. recipient, group member). The mapping to knowledge labels is in §14.2.

*Provenance:*
- Every result lists the mode, the read basis, the historical instant (`historical_at`), the evaluation run time (`evaluated_at`), the resource-existence evidence used, the window and eligible intervals (window mode), the grant relations, the ACL and membership record versions relied on, `acl_source_version`, `acl_synced_at` and a reason code.
- Results used by an answer or agent turn are recorded with it, so the decision can be replayed.

**Separation from the current-authorization path**

- The evaluator **never writes to, reads from or falls back to SpiceDB**. SpiceDB is never asked historical questions.
- Revocations admitted into ACL history update the current projection as before (§10.4). They also enter history, so historical evaluations at bases after the revocation see it.
- **Who had access at the time is itself sensitive.** ACL and membership records carry their own data class. Showing them (e.g. "who could access this at the time") requires current ABAC authorization on that ACL data, in addition to authorization on the object.

**Tests (historical ACL suite, §21.2)**

| Test | Asserts |
|---|---|
| ACL-H1 | Historically allowed (either mode) but currently revoked for the requester → excluded |
| ACL-H2 | Membership valid now but not at t_h → historical `no_grant`/`denied`. Membership valid at t_h but not now → historical `allowed`, and the item is still excluded unless current authorization passes. In window mode, a membership and a grant valid only at different instants never combine → `no_grant` |
| ACL-H3 | No ACL history, snapshot-only before extraction, stale beyond the limit, unresolved identity, depth limit, a partially unevaluable window with no qualifying instant, an invalid or unbounded window, a resource whose existence at the instant (or throughout the unevaluated part of the window) is unknown → `unknown`, and the item is excluded. A resource known not to exist at t_h → `denied` (`resource_not_available`). A resource first observed after t_h, with no recorded creation time, → `unknown`, never `denied` |
| ACL-H4 | ACL, membership or identity records admitted after K are never used; no window reaches past t_h or the subject's knowledge cutoff; no eligible interval lies where the resource is not known to exist (canary) |
| ACL-H5 | No evaluator code path calls the ReBAC engine (static check) |
| ACL-H6 | Results, including `historical_at`, the resource-existence evidence, eligible intervals and grant relations, are replayable from the recorded provenance. A replay differs only in `evaluated_at` |

---

## 11. Governed metrics and query execution

### 11.1 Purpose

All numbers in answers, briefings and risks come from **governed metric definitions**: versioned, reviewed, tested judgement artefacts. They are never produced by model arithmetic or by model-authored SQL.

### 11.2 Definition contract

```yaml
# Illustrative metric definition (judgement artefact)
metric: open_story_points
version: 3
owner: engineering-analytics
grain: [project, epic, assignee]
measure: sum(story_points)
source: projection.issue
time_semantics:
  axis: valid            # how V applies
  snapshot_fields: [status_at_extraction]   # usable only "as at extraction" (T3)
  history_fields: {done_at: resolved_on, created_at: created_on}
data_class: internal
dimensions_allowed: [project, epic, assignee_entity]
disclosure:
  protected_inputs: []   # e.g. [payroll.gross] for payroll-derived metrics
  min_cohort: null
tests:
  - fixture: small_project
    expect: {...}
```

### 11.3 Compilation and execution

1. The planner requests `(metric, dimensions, filters)` using only declared names.
2. The compiler loads the active definition version and validates the requested dimensions against `dimensions_allowed`.
3. ABAC predicates for the principal are obtained from the PDP (compiled filter). The `ReadBasis` predicates (§8.4) are added.
4. SQL is generated with **bind parameters only**. No templating engine and no string interpolation of principals, attributes or user text.
5. If the definition declares protected inputs, the coefficient rows of the release are submitted to the disclosure auditor **before** execution results are returned.
6. Results are returned as facts with `fact_input` lineage to the records used.

### 11.4 Policy combination semantics

Policy combination follows a formal, property-tested specification adapted from the most rigorous open-source semantic layer reviewed (Cube):

- Member access is the union across matching policies.
- Row access is the intersection across queried members, each member's visible rows being the union of its granting policies' row filters.
- Masking returns masked values instead of denying.
- A join path that cannot be authorized is denied (fail-closed).

### 11.5 Time semantics

- Valid-time vocabulary follows the time-spine and slowly-changing-dimension validity conventions of established metric layers (Adapt).
- **Transaction-time (admission) semantics are Veloxa-specific** and are not provided by any external semantic layer.

### 11.6 Caching

Metric results and materialisations are cached only under exact keys (T10) that include the policy hash and the effective principal set. **A cache read of a protected aggregate is a disclosure**: it appends to the disclosure ledger exactly as a fresh execution would.

### 11.7 Governance

- Definitions are reviewed before activation.
- Every query path (API, agents, risk engine, exports) goes through the compiler; integration tests enumerate the paths.
- Definition tests run in CI.
- A definition format compatible with an open metric interchange format is a deferred option (§26).

---

## 12. Retrieval and knowledge graph

### 12.1 Retrieval components

| Component | Approved direction |
|---|---|
| Structured | Governed metrics (§11). Numbers never come from text |
| Lexical | PostgreSQL full-text search initially. An OpenSearch projection when the text volume or relevance needs exceed PostgreSQL (§24) |
| Dense | pgvector ≥ 0.8 (HNSW) in tenant-partitioned chunk tables |
| Entity-aware | Retrieval keyed by resolved entity IDs plus context-scoped alias expansion. **Embeddings never decide identity** |
| Fusion | Reciprocal rank fusion. The constant, branch weights and per-branch depth are **pinned in configuration and evaluated** (implementations vary widely) |
| Rerank | Applied only to authorized candidates |
| Graph traversal | Recursive queries over canonical bitemporal edges |

### 12.2 Filtering behaviour (verified)

With approximate indexes, pgvector applies `WHERE` filters, including RLS quals, **after** the index scan. The index does not restrict traversal to authorized nodes. Unauthorized rows are discarded inside the database executor and never returned.

The consequences are:

- recall loss for selective filters;
- a timing side channel in shared indexes;
- a row-count channel through query-plan instrumentation.

Iterative index scans (`hnsw.iterative_scan`, bounded by `hnsw.max_scan_tuples`) mitigate recall loss only up to their limit.

### 12.3 Layout and filters (Approved direction; tuning deferred)

**Tenant isolation**
- Chunk tables are partitioned by tenant (list partitions for large tenants, hash partitions for the long tail), with one vector index per partition.
- Queries carry an explicit tenant predicate, in addition to RLS, so partition pruning excludes other tenants entirely.
- Queries without a tenant predicate are rejected.

**Filters inside SQL**
- Each chunk row carries:
  - `acl_principals text[]` (typed keys, §10.3) with a GIN index;
  - `is_public`;
  - `batch_seq`;
  - `data_class`;
  - ACL staleness fields.
- The query binds the principal's expanded key set, K and the allowed classes.

**Selective filters**
- Relaxed-order iterative scans with bounded limits.
- When the authorized candidate set is small (by estimate), approximate search is skipped: authorized IDs are selected by index, then exact distances are computed (perfect recall).

### 12.4 Authorise-before-anything (Approved)

Some checks cannot be expressed in SQL, such as a live source residual check. For those:

- Candidates enter an in-memory **quarantine buffer** in the retrieval executor.
- Until a candidate passes authorization it is never logged (only counts), traced, cached, reranked or passed to a model.
- Failures are dropped and counted.
- Query-plan row-count instrumentation (`auto_explain.log_analyze`) is disabled for application roles in pooled cells.

### 12.5 Context compilation strategies

The `ContextCompiler` chooses per request:

| Strategy | When | Behaviour |
|---|---|---|
| Whole scope | The authorized evidence for the resolved scope fits the context budget | Include all of it (ordered: headline evidence first and last) |
| Retrieval | The scope exceeds the budget | Hybrid retrieval + metrics + traversal results within the budget |

Both strategies produce the same `EvidencePack` contract (§13.3).

### 12.6 Knowledge graph (Approved direction)

The canonical graph is **bitemporal edge tables in PostgreSQL** (reports-to, member-of, owns, depends-on, mentions, decided-by, informed), under the same RLS, authorization and time model as everything else. Traversal uses recursive SQL.

### 12.7 Graph projection (Deferred)

A graph engine may be added as a **read projection** for graph analytics or interactive exploration, carrying tenant, ACL and time attributes and never used for evidence without re-authorization.

- Candidates: a dedicated graph database, or an in-PostgreSQL openCypher extension.
- SQL/PGQ native property graphs are **not available**: committed for PostgreSQL 19 and then reverted.

Selection is deferred (§26).

---

## 13. AI answering and verification pipeline

### 13.1 Stages

| # | Stage | Executed by | Output | Control |
|---|---|---|---|---|
| 1 | Authenticate; resolve tenant and cell; open the transaction-scoped context | Code | Principal, tenant, session K | §9 |
| 2 | **Refusal pre-screen** | Code (pattern + entity checks); an optional classifier as a second signal only | Early `refused` with reason and alternative | Policy catalogue §13.6 |
| 3 | Temporal parse → mode, K, R, V | Code. A model fallback for unparseable phrasing, always echoed | `ReadBasis` | §8 |
| 4 | Entity linking | Code (context-scoped aliases + resolver) | Canonical entities | §7.5 |
| 5 | **Plan** | Model (schema-constrained), bounded steps | Calls to declared operations only: metrics, search, traversal, timeline, diff | No free-form tools; no SQL |
| 6 | Execute | Code | Results with evidence IDs, already authorized | RLS + ReBAC + ABAC + disclosure auditor before each call |
| 7 | Compile evidence | Code | `EvidencePack` | Excludes classes above clearance; deduplicates; adds lineage, conflicts, coverage gaps |
| 8 | Compose | Model | Structured claims citing evidence IDs | Evidence pack delimited as data |
| 9 | **Verify** | Code + an independent model pass | Kept, downgraded or dropped claims | §13.4. Can only drop or downgrade |
| 10 | Grade and decide | Code | Grade, qualifiers, status | §13.5 |
| 11 | Record | Code | Answer, claims, citations, read basis, versions, disclosure rows, audit | One transaction |

The pipeline contract (stages, typed messages, policy hooks) is **owned by Veloxa**. No agent framework sits in the core. Frameworks were reviewed and rejected for the core because of release churn, node re-execution on resume, and guardrails that run in parallel with the action rather than before it.

### 13.2 Planning bounds

- At most *N* planning steps per request (configured). Each step is one declared operation with schema-validated arguments.
- **Coverage check:** if the verifier reports that the evidence does not address the question, one re-plan is allowed. After that, the answer abstains or qualifies.
- **The interpretation is echoed** ("I read this as …") so that a misrouted question is visible.

### 13.3 Evidence pack (contract)

```json
{
  "read_basis": {"tenant": "…", "batch_seq": 4, "mode": "KNOWN_AT", "recorded_cutoff": null, "valid_at": null},
  "facts": [{"fact_id": "F1", "display": "…", "value": "…", "kind": "derived",
             "inputs": ["REC:…"], "metric": "open_story_points@3", "qualifiers": ["as at extraction"]}],
  "records": [{"record_id": "REC:…", "excerpt": "…", "locator": "…", "source_class": "system_export",
               "recorded": "[…)", "batch_seq": 3, "data_class": "internal"}],
  "conflicts": [{"conflict_id": "C-…", "sides": [...], "status": "open"}],
  "coverage_gaps": ["No CRM source connected"],
  "policy": {"versions": {...}, "rebac_token": "…"}
}
```

### 13.4 Verifier

**Decomposition.** Composed claims are decomposed into **atomic, pronoun-free claims**. Each atomic claim is checked against **its own cited evidence**, not against pooled context. (Open-source faithfulness metrics were reviewed and judge against pooled context; they are used only as offline CI cross-checks.)

**Checks for each atomic claim**

| Check | Kind | Effect on failure |
|---|---|---|
| Cited IDs exist in the pack and are visible at the read basis | Deterministic | Drop |
| Claim kind ≤ the weakest necessary source class (no upgrade from statement to verified) | Deterministic | Downgrade |
| Every number, date, amount and percentage ⊆ values of the cited facts or records (normalised units and formats) | Deterministic | Drop |
| Every named person, vendor or project ⊆ entities of the cited items | Deterministic | Drop |
| Policy scan: no protected individual values, leave types, suppressed text, or data above clearance | Deterministic | Drop + policy event |
| Hedge present when the source carries a qualifier ("I think", unit unclear), or when an agent claim rests on a KB item whose knowledge basis is `possible` or `role_plausible` (§14.2) | Deterministic (lexicon + knowledge-basis label) | Rewrite with a template hedge; drop if none fits |
| **Entailment** of the claim by its cited evidence | Independent model pass, three-valued: *supported* / *unsupported* / *insufficient* | Keep / drop / downgrade |

**Qualitative judgements about people** are not permitted as factual claims. They must be phrased as inferences about work items, citing the facts they rest on.

**Residual risk.** Qualitative entailment cannot be proven deterministically. The risk is measured by labelled sampling (§21).

### 13.5 Grades and status

| Grade | Shown as | Criteria for the headline claim |
|---|---|---|
| A | High confidence | System record or deterministic derivation from system records; identities resolved with high confidence; no unresolved visible conflict |
| B | Medium | Formal dated document; first-person dated message about the author's own action; human-resolved conflict; or A-grade facts with medium-confidence identity resolution |
| C | Low | Machine transcript, hearsay, reported voice note, unit-ambiguous figure, inference or forecast |
| D | "I don't know" | No usable evidence |

**Qualifiers** are shown beside the grade and do not lower it: "as at extraction", "forecast: assumptions …", "identity assumed", "partial source coverage", "ACL stale".

**Grades express strength of evidence, not probability.** Model-stated confidence is never used. Calibrated likelihoods may be shown only after production feedback provides labelled outcomes, and must then be presented with their sample size (§24).

**Status precedence:** **refused > abstained > conflict > qualified > answered**

| Status | Deterministic trigger |
|---|---|
| refused | Pre-screen match or policy denial |
| abstained | Headline grade D; required source absent (coverage map); or a forecast without a basis |
| conflict | An unresolved visible conflict touches the headline claim, or a reviewed disagreement. Both sides are shown with grades |
| qualified | Grade B/C, any qualifier, or a human-resolved conflict (the believed side is shown with "X said otherwise") |
| answered | Grade A, no qualifiers |

### 13.6 Refusal catalogue (policy, in code)

- Individual compensation for non-privileged principals.
- Ranking or predicting individuals' attrition, performance or sentiment.
- Reading or summarising private communications for monitoring.
- Reasons for personal or sensitive records.
- Commit-count or activity-timing productivity.
- Any action in a business system.

Every refusal carries a policy identifier, a reason and an alternative: for example, project-level continuity risk instead of individual attrition ranking.

### 13.7 Prompt-injection resistance

- Instruction and data are separated: evidence text is delimited and marked as data.
- The planner can emit only declared operations with validated arguments.
- There are no side-effecting tools.
- The verifier and deterministic output scans run on every answer.
- Injection classifiers are a quarantine signal, never the boundary.
- Red-team suites with document-borne attacks run in CI.

### 13.8 Explainability

The system explains answers through **evidence chains and derivations**: the metric definition and version, inputs, filters, time mode, policy constraints applied, the ranking criteria and dominance relations. Model internal reasoning is never presented as an explanation.

---

## 14. Employee agent architecture and memory

### 14.1 Model

| Element | Design |
|---|---|
| Runtime | One shared agent runtime. Agents are data (profile, persona spec, memory), not separate programs. Supervisor/specialist hierarchies are not used: interviews are one-to-one and need no delegation |
| Identity | Agent ↔ person entity (golden record). Identity attributes (title, team, manager, tenure, location) are read **at the read basis** from valid-time facts on every turn, never memorised across K |
| Hackathon tenant | **Fourteen** employee agents, one per in-scope employee of the synthetic tenant (§25) |

### 14.2 Knowledge vs disclosure (Approved)

Fix a tenant τ, a session knowledge point K, an agent *a* representing person *p*, and an interviewer *u*:

| Set | Definition |
|---|---|
| **KB(a, K)** | Records and facts admitted by K within τ, capped at a's knowledge cutoff (e.g. exit date). Includes items that *p* could know: <br>• items for which the **historical ACL evaluator** (§10.7) returns `allowed` for *p* in `ELIGIBILITY_DURING_WINDOW` mode, over a window ending at min(t_h, a's knowledge cutoff) (source ACLs and party relations: sender, recipient, assignee, author, responder, subject, with identity links and group memberships valid at the same instant as the grant); <br>• *p*'s own statements; <br>• team artefacts and *p*'s role entitlement set ("role-plausible"), established from membership and role-entitlement records valid at the same instant (returned by the evaluator as `role_entitlement` grants); <br>• a's memories from sessions this interviewer may use. <br>Items whose historical evaluation is `denied` or `unknown` are **not** in KB. Every KB item carries a **knowledge basis** (below) |
| **AZ(u, K)** | What *u* may see under **current authorization** (RLS, ReBAC current projection, ABAC: §10.1), evaluated **now**, restricted to material admitted by K. Aggregates are further gated by the disclosure auditor on *u*'s ledger. AZ is never computed from historical ACLs |
| **POL(τ, interview)** | Tenant policy: data classes allowed in interviews; persona content only if `synthetic = true` |
| **D(a, u, K)** | **KB ∩ AZ ∩ POL**: the only evidence an agent turn may use |

**Knowledge basis of KB items.** Historical eligibility shows what *p* could access, not what *p* read. Each KB item is labelled from the evaluator's `grant_relations`; when several apply, the strongest label is used.

| Knowledge basis | Grant relations | What the agent may say |
|---|---|---|
| `participant` | author, sender, committer, responder, assignee, entered-by: the record documents *p*'s own involvement | The involvement, as a record fact ("the tracker shows it assigned to me", "I sent that on …"). Content beyond the record is not claimed as remembered |
| `possible` | recipient, cc, group member, direct share, domain share: access only | That *p* had access ("it was sent to me", "I'd have had access to it"). **Never** that *p* read, opened or remembers it |
| `role_plausible` | role entitlement only | That awareness is plausible for the role ("in my role I'd likely have been aware of …"), never certain knowledge |

*p*'s own statements are `participant`. Agent memories keep the basis of the items they were derived from.

**Rules**

1. **Agent-turn retrieval draws only from D.**
2. **KB \ AZ** (the employee could know it; the interviewer may not see it) is never used. This defeats the *proxy attack*: reading a subordinate's mail or pay through their agent.
3. **AZ \ KB** (the interviewer may see it; the employee couldn't know it) is never used. This keeps the agent within what the employee could actually know.
4. **Aggregates over protected measures** discussed by an agent append to the **interviewer's** disclosure ledger.
5. **Factual claims** in agent turns go through the same verifier as cockpit answers.
6. **Two different subjects, two different evaluations.** KB asks whether the *represented employee* could access an item *at the read basis* (historical ACL evaluation). AZ asks whether the *interviewer* may access it *now* (current authorization). Neither substitutes for the other.
7. **Historical access never widens disclosure.** If the employee could access an item historically but the interviewer's current authorization denies it, it is not used. If the interviewer is currently authorized but the employee's historical evaluation is `denied` or `unknown`, it is not used.
8. **Access is not reading.** An agent never states or implies that *p* read, opened or remembers an item whose knowledge basis is `possible` or `role_plausible`. The verifier's hedge check (§13.4) rewrites such a first-person claim with a template hedge, or drops it if no hedge fits.

### 14.3 No impersonation, no action (Approved)

- **No credentials.** Agents never hold, request or use employee credentials, tokens or sessions.
- **Read-only service identity.** The platform reads sources under its own identity, read-only. ACL tuples are *data about who could see what*; they grant the agent nothing in source systems.
- **No action tools.** Agents never send, create, approve or modify anything. They may draft *proposals* for a human, who executes them under their own identity through an approval workflow.
- **Labelling.** Agent output is labelled as an AI representation, not the employee.
- **Real tenants:** employee consent is required; employees can view their agent's knowledge boundary and correct its memory; tenants can disable agents.

### 14.4 Persona (synthetic tenants only)

- **Scope.** Persona items cover voice, life details and role-plausible responsibilities. They are `assumption`-class data, each with an assumption identifier in the assumption registry, so they **can never be cited as evidence**.
- **Gating.** Items derived from a record carry a basis record and inherit its admission. Items without a basis must be time-neutral.
- **Forbidden content:**
  - health, religion, caste, sexuality, finances, politics, relationship difficulties;
  - reasons for any sensitive record;
  - opinions about named colleagues' private matters;
  - invented business facts (hires, numbers, decisions, events).
- **Thin records.** Employees with little documentation receive role-plausible responsibilities as logged assumptions, never as facts.

### 14.5 Memory (Approved)

| Tier | Content | Notes |
|---|---|---|
| Working | Current session turns (token-bounded) | Scoped to one K (T9) |
| Episodic | Records the person was party to, rendered as dated events | Derived from KB at the read basis |
| Semantic | Consolidated facts with validity intervals | Each item carries provenance to source episodes |
| Interview | Summaries of prior sessions | Keyed by (tenant, agent, **interviewer**, session K) |
| Reflective | Model-generated summaries | Always labelled *inference*; never evidence |

**Memory item structure.** Adapted from bitemporal agent-memory designs:

- valid from/to;
- admitted batch;
- expired-at;
- provenance episodes;
- `superseded_by` and `invalidation_reason`;
- for model-assisted invalidation, the decision record: model, prompt version, candidate set, chosen indices.

Facts are **expired, never deleted**. The model may *detect* contradiction candidates; resolution is deterministic and audited.

**Isolation**

- `tenant_id`, `agent_id` and `interviewer_id` are separate, RLS-enforced columns, never a concatenated scope string.
- Reads fail without a full scope.
- By default, memories from interviewer *U*'s sessions are not available in interviewer *V*'s sessions. Shared memories for a declared group are a tenant policy option (§26).
- Consolidated memories inherit the most restrictive ACL of their inputs.

### 14.6 Lifecycle

| Event | Behaviour |
|---|---|
| Employee exit | The agent becomes an alumnus: read-only, knowledge capped at the exit date, no new activity. Real tenants may deactivate it entirely |
| Reported status change (a change reported in a statement but not yet in the system of record) | Admitted as a *reported* fact; the agent knows it only for K at or after its admission. The agent never invents reasons or intentions |
| Correction by the employee (real tenants) | A versioned memory correction with author and rationale |

### 14.7 Simulation (stretch)

A rule-first generator extends observable patterns after the data cutoff:

- **Bounded by facts:** no activity for exited people or on recorded leave days; throughput bounded by history.
- **Model role:** the model writes only surface text.
- **Isolation:** output lives in an isolated simulation namespace, flagged `is_simulated`, and is **never readable by evidence paths**.

### 14.8 Response to withheld knowledge

When a question would require KB \ AZ, the agent must not reveal that the knowledge exists. The **default** is uniform non-disclosure, identical for unknown and undisclosable topics ("That's not something I can get into here"). Confirmation of this default and of the work-message excerpt policy is deferred (§26).

---

## 15. Payroll disclosure auditing

### 15.1 Scope and attacker model

- **Protected measures:** per-person monetary values (gross, deductions, net, bonus), tenant-configurable.
- **Non-privileged principals** receive aggregates only.
- **The attacker** knows the auditor's algorithm, remembers every answer from every channel, and has background knowledge (§15.3).
- **Goal:** no protected individual value, and no total for a group smaller than *k*, becomes **exactly** determined. Partial (interval) disclosure is addressed in §15.7 and §26.

### 15.2 Variables and coefficient rows

**Stable-pay linkage (M2), required**

- Each person-segment is one variable, where a segment is a maximal period with pay *assumed* constant.
- By default there is one segment per person. A segment splits only when a pay change is recorded **and** the principal may know that a change occurred.
- Partial periods enter as known pro-rata coefficients.

A per-period model (one variable per person-month) is **insufficient on its own**: an attacker who assumes pay is stable can difference consecutive totals across a joiner or leaver. Because M2 adds constraints to the per-period model, it detects every full disclosure the per-period model detects, and more.

**Released statistics and their rows**

| Released statistic | Rows added to the ledger |
|---|---|
| SUM over a cohort and periods | Coefficient row (person-segment × periods present × pro-rata weight) |
| AVG with derivable headcount | Same as SUM |
| Derived metrics (ratios to public values, per-head cost, growth) | Each declared protected input's row (assumed invertible) |
| Documents containing aggregates (e.g. ledger "salaries" postings) shown in the evidence view | That aggregate's row, at display time |
| MAX, MIN, MEDIAN, percentiles, variance | **Not released** to non-privileged principals |
| Counts of sensitive boolean attributes | Not audited by this mechanism; k-threshold + suppression policy |

MAX/MIN/MEDIAN are excluded because mixed sum/max auditing is NP-hard. Boolean sum auditing is coNP-hard.

### 15.3 Background knowledge, channels, scope

**Pre-seeded rows**
- The principal's own values (unit rows).
- Values the principal is individually authorized to see.
- Aggregates in documents the principal can already read.
- Publicly known values.

These are not violations, but they enable inference about others and must be present.

**Channels**
- Answers, briefings and risk cards.
- Evidence views.
- Agent interviews (on the interviewer's ledger).
- Exports and APIs.
- Cache reads.

Rows are appended in the same transaction that commits the response.

**Ledger scope and persistence**
- The default ledger scope is the individual principal. Group ledgers for principals presumed to share information are a tenant option (§26).
- Ledgers are append-only and never reset.
- Audit and append are serialized per (tenant, scope, measure) to prevent concurrent joint disclosure.

### 15.4 Decision procedure

The inputs are the ledger rows L (including background rows) and the candidate rows Q. **Values are never inputs.**

1. **Policy gates.**
   - The statistic is permitted.
   - Each cohort has at least *k* members (tenant-configured).
   - The principal may know each cohort's membership.
2. **Full-disclosure test.** Let A = L ∪ Q. A violation exists if any protected variable outside the principal's allowed set has its unit vector in the row space of A. This is tested exactly by rank comparison.
3. **Small-group test (policy).** Group variables with identical column vectors (atoms). A violation exists if the indicator of any union of atoms with fewer than *k* members lies in the row space.
4. **Outcome.** Allow and append. Otherwise deny, or coarsen to an alternative release that passes, chosen without consulting values. Denials carry a generic reason.
5. **Pre-existing exposure.** Variables already determined by L alone (e.g. via documents) are reported to the policy administrator.

### 15.5 Guarantees

| Statement | Basis |
|---|---|
| Over unbounded reals with exact sums, a value is uniquely determined iff its unit vector lies in the query row space | Linear algebra; the basis of classical sum auditing |
| Decisions use only query structure, never data, so denials convey no information about values (simulatable auditing) | By construction, matching the published definition of simulatable auditors |
| M2 detects every full disclosure the per-period model detects | Linear algebra |
| No exactly determined total for indicator-weighted groups smaller than *k* | Policy, enforced by construction for the enumerated sets; **not a privacy theorem** |
| Partial (interval) disclosure from bounds, rounding or priors | **Not guaranteed** by this mechanism (§15.7, §26) |
| Collusion across ledger scopes, timing side channels, external rumours | Out of model |

### 15.6 Implementation requirements

- **Exact arithmetic** (rational or integer elimination; modular arithmetic with verification at scale). Floating-point rank is not acceptable.
- An **incremental reduced basis** per ledger; sparse elimination; atoms by column hashing.
- **Logging** of decisions and row structure only, never values.
- **Rounding is presentation only.** The auditor treats every release as exact.
- **Fail closed** when unavailable.
- **Test oracle:** a statistical disclosure-control library (sdcTable, GPL, executed out of process in tests only) fuzz-tests the auditor against attacker models.

### 15.7 Approximate disclosure (Deferred: D-22)

Official-statistics practice also protects against *interval* disclosure: how tightly an attacker can bound a value using known bounds. Those checks depend on values and conflict with simulatable auditing.

The options are:

- (a) accept and document the residual risk;
- (b) a probabilistic simulatable auditor, which assumes a data distribution;
- (c) interval checks only for pre-defined published tables;
- (d) differential-privacy noise for people aggregates shown to non-privileged roles.

**No option is selected** (§26). Differential privacy, if adopted, requires a durable per-principal privacy-budget ledger built by Veloxa: the libraries reviewed keep budgets in memory or per session only.

---

## 16. Risk engine and ranking model

### 16.1 Pipeline

1. **Detectors** (deterministic rules over facts and metrics) produce **risk signals** with evidence IDs.
2. Signals are **grouped by business object** (a commitment, a service, a reporting line, a financial statement). **Drivers** are attached to that object as evidence, so one cause is not counted as several risks.
3. Each risk is scored on **impact criteria** with interval values.
4. Risks are ranked by **dominance**, with explicit guardrail tiers and declared ties.
5. A **snapshot** per read basis records inputs, the model version, an input hash, and the ranking with reasons.

### 16.2 Criteria (impacts, not causes)

| Criterion | Natural scale | Minimum evidence to score |
|---|---|---|
| C1 External-commitment exposure | Required-rate ratio toward a dated customer or board commitment | A dated commitment in a record or document, an open-scope fact, a throughput window. Otherwise "unquantified" |
| C2 Financial exposure | Currency at stake, from records the principal may see | A record-backed amount. Amounts derivable only from protected pay are **not** used; such findings are process findings |
| C3 Operational-continuity exposure | Share of commitment-linked work or a critical service held by one person; on-call coverage concentration | **Allocation records only** (tickets, rotas, requisitions) |
| C4 Information-integrity exposure | Discrepancy between figures reported upward and system records | Two independent sources in conflict |
| C5 Time proximity | Days to the affected commitment | A dated commitment |

**Additive value model.** It requires preferential independence among criteria, which is why criteria are defined on impacts and drivers are kept as evidence. Weights are swing weights tied to criterion ranges. **Weights, anchors and thresholds are owner value judgements (§26).**

### 16.3 Uncertainty and ranking

- Each criterion score is an **interval** reflecting the evidence grade and data-quality qualifiers.
- **Dominance.** Risk A ranks above B only if A's aggregate lower bound exceeds B's upper bound. Otherwise both are shown as "not distinguishable on current evidence" and ordered by guardrail tier, then a declared tie-break.
- **Guardrail tiers** are a few explicit lexicographic overrides, for example "a threatened external commitment ranks first".
- **Sensitivity.** Weight-stability intervals are computed for adjacent pairs ("stable unless the C1 weight falls below *w*"). Rank-acceptability analysis is optional.
- **Rank reasons** are generated from the dominating contributions and tiers.

### 16.4 Probabilities only behind evidence gates

A delivery probability (e.g. a throughput Monte Carlo) is emitted **only if every gate passes**:

| Gate | Test |
|---|---|
| Stationarity | Enough weekly samples, with no significant trend |
| Tracker completeness | Tracker activity consistent with independent activity |
| Scope stability | Scope growth bounded or modelled |
| Unit consistency | Consistent estimation units |

If any gate fails, the engine emits a **deterministic bound**, labelled as such with its assumptions (e.g. "the required rate exceeds the best observed rate"). **No probability is displayed.** When probabilities are shown, they appear as bands with sample size and assumptions.

### 16.5 Quantification states

| State | Shown | Ranking |
|---|---|---|
| Quantified | Intervals | Dominance |
| Bounded | Deterministic bounds only | Tier, then interval |
| Unquantified | Evidence-backed description: "not quantified: <missing evidence>" | Tier only |
| Insufficient | Not in the top list; listed under "signals needing data" | — |

**No number is invented to make a risk rankable.**

### 16.6 People-data restrictions

Criteria about people use **formal allocation records only**: tickets, rotas, requisitions. They never use activity timing (after-hours commits, message timestamps), communication content, leave reasons or engagement of individuals.

Team-level survey scores may be shown only as the survey system itself publishes them, with comments suppressed.

### 16.7 Historical rankings and backtesting

- Rankings are computed per read basis (§8) and addressed by it. A "latest" lookup is never served for a historical basis.
- Backtesting at historical K is supported. **Hindsight bias** is disclosed: rules and anchors should be committed before later material is analysed.

---

## 17. Durable workflow architecture

### 17.1 Contract

All durable work runs through `ActivityRunner`. Activities are **idempotent**, **named stably** and **versioned**.

### 17.2 Runtime (Approved)

| Stage | Runtime |
|---|---|
| Initial implementation and pilot | **DBOS**: durable workflows as a library, state in PostgreSQL system tables, no extra service. Provides queues, schedules, durable messaging (approval waits) and events (Integrate) |
| Scale | **Temporal**: dedicated server, PostgreSQL persistence and visibility, mature workflow versioning (Integrate) |

### 17.3 Workflow catalogue

| Workflow | Trigger | Notes |
|---|---|---|
| Connector sync | Schedule, webhook | One queued sync per (tenant, source), enforced by a queue lock |
| Admission sealing | Sync completion | Seals only after records and cursor commit |
| Projection refresh / rebuild | Sealed watermark advance, judgement version change | Idempotent per batch |
| Index refresh | Sealed watermark advance | Partition-scoped |
| ReBAC projection | ACL records admitted | Writes current tuples; records tokens |
| Risk snapshot | Watermark advance, schedule | Snapshot per read basis |
| Approval request | Proposal created | Waits durably for a human decision, with expiry |
| Tenant move | Administrative | §24.3 |
| Simulation (stretch) | Schedule | Isolated namespace |
| Erasure | Request | Design deferred (§26) |

### 17.4 Correctness rules

- **Idempotency keys** on every connector write, admission, projection step and model call.
- **Pinned step names**, guarded by compatibility tests. A step-name mismatch on replay fails loudly instead of silently diverging.
- **`application_version`** recorded on each run, and workflow-code changes gated for in-flight runs.
- **Human approval waits live only in the durable runtime,** never in an in-memory agent loop.
- **Outbox.** Internal events (watermark advanced, ACL changed) are written to an outbox table in the same transaction as the state change and relayed by the workflow runtime. A PostgreSQL-native queue library may serve as transport (§26). No message broker is deployed initially.

### 17.5 Scale triggers for Temporal

Move from DBOS to Temporal if any of these hold:

- Workflow volume or duration exceeds what in-process execution supports.
- Worker isolation from API processes is required.
- Multiple languages are needed.
- Cross-cell orchestration is needed.

The migration changes the runner implementation, not activities or contracts.

---

## 18. LLMClient, model routing and budget enforcement

### 18.1 Responsibilities (Build)

`LLMClient` is the only path to any model. Model access policy (budgets, residency, redaction, model permissions) is **in-house logic**. It is not delegated to a third-party proxy.

```python
# Illustrative request contract
@dataclass
class ModelRequest:
    tenant_id: UUID
    stage: Literal["plan", "compose", "verify", "agent", "extract", "risk_narrative"]
    data_classes: set[str]          # classes present in the prompt (from the evidence pack)
    schema: dict                    # structured-output JSON schema
    prompt_version: str
    messages: list[dict]
    budget_estimate_tokens: int
    idempotency_key: str
```

### 18.2 Functions

| Function | Design |
|---|---|
| Routing | By stage and sensitivity. Each stage has a model role (plan, compose, verify, agent, extract). The verifier uses a separate prompt and, where quality allows, a different model configuration |
| Model permissions and residency | A per-tenant policy states which providers, regions, endpoints and data classes are allowed. A request carrying a disallowed class is refused before any call. Once a session contains sensitive data, it is pinned to a compliant endpoint |
| Redaction | Classes a tenant forbids sending are removed or masked before prompt assembly, and the redaction is recorded |
| Budgets | **Pre-call reservation** in a PostgreSQL transaction against per-tenant and per-principal budgets, reconciled with actual usage afterwards. Requests exceeding the budget are refused before the call. Budgets are hard limits, not batched counters |
| Structured outputs | Schema-constrained outputs, validated on receipt. Invalid output → one retry → stage failure |
| Retries and fallbacks | Bounded retries. Fallback endpoints only within residency policy. Otherwise the pipeline degrades to facts-only mode |
| Versioning | Prompt templates and model identifiers are pinned and recorded on every call |
| Record/replay | Requests are hashed (model, prompt version, inputs). Recorded responses are replayed in tests; a missing recording in CI fails loudly. Recordings contain no secrets and no raw corpus beyond what the test fixture defines |
| Telemetry | OpenTelemetry spans with pinned GenAI attribute names. **Replayed calls do not re-emit token or cost telemetry** |

```sql
-- Illustrative pre-call budget reservation (same transaction as the call record)
UPDATE runtime.budget
   SET reserved_tokens = reserved_tokens + $estimate
 WHERE tenant_id = $tenant AND period = $period
   AND used_tokens + reserved_tokens + $estimate <= limit_tokens
RETURNING id;   -- no row => refuse before calling the model
```

### 18.3 Provider and proxy

- **Provider.** Claude models through the Anthropic API or a cloud-partner endpoint, as residency requires. Model identifiers, prices and API behaviours are verified against the provider's live model listing at implementation time (§26).
- **Proxy.** A separate gateway proxy is **optional** and **unselected** (§26). If one is introduced, it sits behind `LLMClient`, does not replace its policy, runs on a private network with administrative and extension features disabled, is pinned by hash, and has a patch SLA. Supply-chain compromise of a key-holding gateway is an explicit threat (§23).

---

## 19. API capabilities and frontend-independent contracts

### 19.1 Principles

- **JSON over HTTPS, versioned paths.** The contracts here are the backend's obligations to any frontend, whatever technology is chosen. They support every journey in §19.5.
- **Every read endpoint accepts the time parameters** `as_known_at` (or `batch_seq`), `mode`, `as_of`. The response echoes the resolved read basis.
- **A refusal is a successful response** with `status = "refused"`, not an error.
- **Authentication:** bearer tokens from the identity provider. Tenant resolution goes through the tenant directory; the client never passes a tenant ID.
- **Incremental delivery** is a required capability for long answers. The transport (e.g. server-sent events or WebSocket) is deferred (§26).

### 19.2 Common parameters

| Parameter | Meaning |
|---|---|
| `as_known_at` | Instant or date (tenant time zone; date-only means end of day). Resolved to the last sealed batch at or before it, clamped to the clock |
| `mode` | `CURRENT`, `KNOWN_AT`, `RECORDED_BY`, `VALID_AT` |
| `as_of` | The R or V date, depending on mode |
| `conversation_id` | Conversation scope; implicitly bound to one read basis (T9) |

### 19.3 Endpoints

| Capability | Endpoint(s) | Returns |
|---|---|---|
| Time metadata | `GET /v1/meta/releases`, `GET /v1/meta/clock` | Admission batches with sealing times; current clock and sealed watermark |
| Briefing | `GET /v1/briefing?limit=&since=` | Top risks with rank reasons, quantification states, evidence, and changes since a prior basis |
| Risks | `GET /v1/risks`, `GET /v1/risks/{id}` | Full ranking, sensitivity, signals, evidence |
| Ask | `POST /v1/ask` (+ incremental variant) | `Answer` (§19.4) |
| Evidence | `GET /v1/evidence/{record_id}` | Policy-filtered record view: excerpt, locator, source class, recorded/admitted times, data class, usage count. Displays of protected aggregates pass the auditor |
| Conflicts | `GET /v1/conflicts`, `GET /v1/conflicts/{id}` | Sides with grades, resolution status and rationale reference |
| Company memory | `GET /v1/timeline`, `GET /v1/decisions`, `GET /v1/diff?k1=&k2=` | Dated events, decisions found in records, changes between two bases |
| Organisation | `GET /v1/org`, `GET /v1/org/planned-changes` | Confirmed and planned reporting edges with effective dates and status |
| Agents | `GET /v1/agents`, `GET /v1/agents/{id}/profile`, `GET /v1/agents/{id}/persona` | Profiles at the basis; persona items with assumption IDs (synthetic tenants only) |
| Interviews | `POST /v1/agents/{id}/sessions`, `POST /v1/sessions/{id}/messages`, `GET /v1/sessions/{id}` | Agent turns with cited factual claims, persona references, boundary note |
| Agent memory (admin/self) | `GET /v1/agents/{id}/memories` | Memory items in scope |
| Simulated activity (stretch) | `GET /v1/agents/{id}/activity?include_simulated=true` | Flagged simulated events |
| Administration | `/v1/admin/ingest`, `/v1/admin/clock`, `/v1/admin/policy-events`, `/v1/admin/audit`, `/v1/admin/disclosure-ledger`, `/v1/admin/conflicts/review`, `/v1/admin/assumptions` | Operational data. The disclosure-ledger view shows structure only, never values |

### 19.4 `Answer` contract

```json
{
  "status": "answered | qualified | conflict | abstained | refused",
  "interpretation": "I read this as …",
  "answer": "≤ 3 sentences",
  "grade": {"value": "A|B|C|D", "label": "High|Medium|Low|I don't know", "reasons": ["…"]},
  "qualifiers": ["as at extraction", "identity assumed: …"],
  "claims": [{"text": "…", "kind": "record|document|statement|derived|inference",
              "evidence": ["REC:…", "F1"], "verification": "supported|downgraded"}],
  "disagreements": [{"conflict_id": "C-…", "sides": [{"summary": "…", "grade": "B", "evidence": ["…"]}]}],
  "unknowns": ["…"],
  "refusal": {"policy": "POL-…", "reason": "…", "alternative": "…"},
  "read_basis": {"batch_seq": 3, "mode": "KNOWN_AT", "as_of": null, "banner": "Answering as known at …"},
  "audit_id": "…"
}
```

**Typed errors:** `not_authorised`, `invalid_time` (e.g. a basis beyond the sealed watermark), `insufficient_evidence` (metric unavailable), `budget_exceeded`.

### 19.5 Journeys and supporting capabilities

| Journey | Required behaviour | Capabilities |
|---|---|---|
| J1 Today/home briefing | Ranked concerns with reasons, quantification state, evidence, and change since the last visit | `GET /briefing` |
| J2 Ask Veloxa | Status, answer, grade, claims, disagreements, unknowns, mode banner; incremental delivery | `POST /ask` |
| J3 Evidence drawer | Any citation opened with excerpt and provenance; aggregate displays audited | `GET /evidence/{id}` |
| J4 Time travel | All views recomputed for a chosen basis and mode; conversation re-scoped; shareable state | Time parameters on all reads; `GET /meta/*` |
| J5 People / interviews | Profiles at the basis; interviews under KB ∩ AZ; invented items flagged; per-interviewer memory | Agents and interviews endpoints |
| J6 Company memory | Timeline, decisions, conflicts, diff | Memory and conflicts endpoints |
| J7 Organisation changes | Confirmed vs planned lines with effective dates | Org endpoints |
| J8 Refusal with alternative | Refusal object with reason and alternative | `POST /ask` → `refused` |
| J9 Administration | Clock/basis, ingestion and quarantine, policy events, audit, disclosure-ledger summary, conflict review, assumption registry | Admin endpoints |

---

## 20. Deployment and service decomposition

### 20.1 Deployables

| Deployable | Contents | Why separate |
|---|---|---|
| **Core API** | Modular core: temporal, policy clients, metrics engine, retrieval, evidence compiler, pipeline, verifier, risk engine, disclosure auditor, agent runtime, admin. In-process DBOS workflows initially | One transactional domain. The no-leakage and authorization invariants are easier to prove in one process |
| **Connector workers** | Connector implementations, landing, validation, admission submission | Hold source credentials (different security posture); bursty; rate-limited by sources |
| **Indexing / projection workers** | Projection and index maintenance | CPU- and IO-heavy; must not starve the API |
| **Document parsing sandbox** | Document parsers | Untrusted input; restricted network and filesystem |
| **ReBAC engine** | SpiceDB | Off-the-shelf; latency-critical |
| **Policy engine** | OPA (and Cedar evaluation as library or service) | Off-the-shelf |
| **Workflow server** (scale stage) | Temporal | Only when the triggers in §17.5 fire |
| **Observability stack** | Collector, trace and metric stores | Off-the-shelf |
| **Control plane** | Tenant directory, cell router, provisioning | Global; small |

**Service extraction triggers** (beyond the list above):

- an independent scaling profile;
- a distinct security boundary;
- separate team ownership;
- a different release cadence.

Expected first extractions are the risk engine (scheduled, CPU-bound) and the agent runtime (long sessions).

### 20.2 Platform

| Concern | Production design |
|---|---|
| Orchestration | Managed Kubernetes per region; one stack per cell; GitOps deployment; infrastructure as code |
| PostgreSQL | CloudNativePG ≥ 1.30 with the Barman Cloud Plugin, synchronous standby, PITR; PgBouncer pooler (transaction mode); or an equivalent managed PostgreSQL 18 service with the required extensions |
| Object storage | Versioned, object-locked buckets per cell; per-tenant prefixes with envelope encryption |
| Network | Zones: edge (gateway), application (core API), ingestion (connectors, parsing sandbox, egress to sources only), data (PostgreSQL, object storage), control (authorization engines, directory). Mutual TLS between zones |
| Secrets | Secret manager; workload identity; per-service scoping |
| Supply chain | Pinned dependencies by hash; SBOM; signed images; dependency and container scanning; advisory monitoring for every integrated component |

---

## 21. Observability, evaluation and audit

### 21.1 Telemetry

- **OpenTelemetry traces, metrics and logs** end to end.
- **Model calls** are instrumented via OpenLLMetry (Adopt), with GenAI attribute names pinned in one module (the conventions are still evolving).
- **Traces export over OTLP** to a self-hosted trace and evaluation store. The preferred candidate is Langfuse (MIT core); final selection is deferred (§26).
- **Spans record IDs only after authorization** and never contain protected values.

**Signals**

| Signal | Purpose |
|---|---|
| Per-stage latency (parse, plan, execute, compile, compose, verify) | SLOs; locating failures |
| Validator drops and downgrades by reason | Composer drift |
| Abstention rate by intent | Coverage gaps (what to build next) |
| Policy events and refusals | Misuse patterns; refusal UX |
| Disclosure denials and coarsenings | Auditor behaviour (structure only) |
| Ingestion freshness, quarantine rate, ACL staleness per source | Evidence quality |
| Tokens, cost, budget rejections per tenant | Cost control |
| Leakage canaries | Continuous proof of temporal integrity (§21.2) |

### 21.2 Evaluation layers

| Layer | Content | Runs |
|---|---|---|
| Deterministic suites | Parsers, temporal rules, admission sealing, metrics, policy, disclosure auditor, verifier checks, grading, risk ranking | Every commit |
| RLS conformance | Wrong-tenant reads, fail-closed context, pooled-connection interleaving, role attributes, views, definer allow-list, tenant-scoped keys, statistics visibility, partitions, `TRUNCATE`, `WITH CHECK` | Every commit |
| Historical ACL suite | ACL-H1 to ACL-H6 (§10.7): revocations never bypassed; historical vs current memberships never interchanged, and same-instant chains in window mode; every unknown fails closed, including partial and invalid windows and unknown resource existence (both modes); first observation never treated as creation; no post-K records, no window past t_h or the cutoff, no eligibility where the resource is not known to exist; no ReBAC calls from the evaluator; replay from provenance (only `evaluated_at` differs) | Every commit |
| Leakage canaries | Facts admitted only in batch *n* are absent for every basis before *n* and present from *n* onward, across all surfaces (answers, briefings, memories, caches, org, conflicts) | Every commit; continuously in production at historical bases |
| Privacy suites | Protected values never appear in any output; differencing attacks denied; aggregates through agents and evidence views recorded | Every commit |
| Retrieval leak suites | Canary chunks visible to another principal never reach results, logs, traces, reranker inputs, prompts or caches | Every commit |
| Gold Q&A | Questions with expected values, citations, status and grade, run through the real pipeline with **recorded model responses** | Every commit (offline) |
| Live evaluation | The same gold set against current models; re-records; drift report | On demand; before releases |
| Adversarial | Prompt injection (direct and document-borne), paraphrase families, similar-name entity traps, temporal traps | Every commit + live |
| Agent suites | Identity probes for every agent; boundary probes (no KB \ AZ, no AZ \ KB); temporal probes; persona consistency; proxy attack; knowledge-basis probes (an access-only item is never asserted as read, §14.2 rule 8) | Every commit + live |
| Online (production) | Sampled answers re-verified; human-labelled claim-support audits; employee feedback | Continuous |
| Offline cross-checks | Open-source RAG evaluation libraries as secondary signals in CI only | Scheduled |

**Calibration.** Gold cases declare the expected grade, and the suite checks consistency. **Statistical calibration is not claimed** until production feedback exists.

### 21.3 Audit

- **What is recorded:** every request, plan, authorization decision (with policy versions and ReBAC token), evidence set, model call (model, prompt version, token counts), verification outcome, disclosure decision and refusal.
- **Store:** append-only, hash-chained, in a store separate from the application database in production, with access to the audit itself audited.
- **Tamper evidence:** chain heads are periodically anchored externally. The anchoring mechanism is deferred (§26).
- **Contents:** protected values are never written to audit. Prompts and responses are stored per data-class redaction policy.

---

## 22. Failure handling, recovery and disaster recovery

### 22.1 Failure behaviour by component

| Component | Failure | Behaviour |
|---|---|---|
| Connector / source | Source down, rate-limited, schema drift | Retry with backoff. Schema drift goes to quarantine. The batch doesn't seal. Freshness alerts. Answers carry "data as of" qualifiers |
| Admission sequencer | Crash mid-batch | The unsealed batch is discarded or resumed idempotently. The watermark never regresses |
| Projection / indexing | Bug or corruption | Drop and rebuild to the sealed watermark. Readers are served only up to the last complete projection watermark |
| ReBAC engine | Unavailable | **Fail closed** for checks. Rebuild state from ACL history if needed |
| Policy engine | Unavailable | **Fail closed** |
| Disclosure auditor | Unavailable | **Fail closed**: no protected aggregates released |
| Model provider | Outage or refusal | Fallback endpoint within residency policy, else facts-only mode (`qualified`, no composition) |
| Verifier | Unavailable | Claims are not presented as verified; status at most `qualified` |
| Budget exhausted | — | Model-dependent stages refused; facts-only mode |
| Workflow runtime | Process crash | Resume from the journal. Idempotent activities; step-name checks |
| PostgreSQL primary | Failure | Operator failover to a synchronous standby; PgBouncer reconnects; in-flight transactions retried idempotently |
| Cache | Loss | Recompute (exact keys only) |
| Tenant directory | Unavailable | Cached routes continue; provisioning pauses |

### 22.2 Backups and disaster recovery

- **Backups:** PostgreSQL PITR with continuous WAL archiving to object storage. Object storage versioned and object-locked. Audit store exported to WORM storage. Workflow persistence backed up with its database.
- **Targets** (assumptions, to be set per contract):
  - early production: RPO ≤ 15 min, RTO ≤ 4 h;
  - enterprise: per contract, with cross-region replicas.
- **Rebuild order after restore:**
  1. Ledger and judgement.
  2. Policy and disclosure ledger.
  3. Knowledge and domain projections, rebuilt to the sealed watermark.
  4. Search and vector indexes.
  5. ReBAC projection, from ACL history.
  6. Caches, which start cold.
- **Drills:** restore tests every quarter, including verifying that answers recorded before the incident replay identically at their read basis.

---

## 23. Security threat boundaries

### 23.1 Trust boundaries

| Boundary | Inside | Outside | Enforcement |
|---|---|---|---|
| Internet ↔ edge | Gateway | Clients | OIDC, WAF, rate limits, request size limits |
| Edge ↔ cell | Core API | Gateway | Tenant directory routing; mutual TLS |
| Tenant ↔ tenant (pooled cell) | One tenant's rows | Others | FORCE RLS + restrictive policy + tenant-scoped keys; tenant-keyed non-DB stores |
| Principal ↔ principal | Authorized evidence | Unauthorized evidence | ReBAC + ABAC before execution; disclosure auditor for aggregates |
| Agent ↔ source systems | — | Source systems | No credentials, no action tools; read-only platform identity |
| Agent knowledge ↔ interviewer | KB ∩ AZ ∩ policy | Everything else | Evidence compiled from D only. KB comes from historical ACL evaluation of the represented employee; AZ from the interviewer's current authorization (§10.7, §14.2) |
| Historical ↔ current permissions | Current authorization of the requester | Historical ACL grants | Historical evaluation can only narrow; `unknown` fails closed; historical results never restore revoked access (§10.7) |
| Platform ↔ model provider | Evidence pack (policy-filtered) | Provider | `LLMClient` model permissions, residency, redaction |
| Platform ↔ sources | Connector workers | Sources | Per-tenant secrets; egress restrictions |
| Untrusted documents ↔ platform | Parsing sandbox | Parsed output | Isolation; size and page limits; no network |
| Operators ↔ data | Break-glass | Normal operations | Separate credentials, MFA, time-boxed grants, audit |

### 23.2 Threat register (summary)

| Category | Threats | Controls (sections) |
|---|---|---|
| Tenant isolation | Missing filter; unset or bled context; privileged connection; view-owner bypass; definer escalation; constraint covert channel; statistics leaks; partition or matview bypass; migrations regressing RLS; tenant IDs in identifiers; compromised app server | §9.3–9.5; RLS conformance suite (§21.2); silo cells for high assurance |
| Temporal leakage | Unfiltered queries; derived and runtime objects; caches; conversation carry-over; latest-snapshot lookups | §8.4–8.5; canaries |
| Retrieval leakage | Post-filter recall loss; timing and count channels; residual-check candidates reaching logs or prompts | §12.2–12.4 |
| Permission drift | Stale ACLs; revocation lag; engine snapshot limits | §10.2–10.4 |
| Historical-permission misuse | A historical grant used to restore revoked access; current group membership used for a historical question (or the reverse); a membership and a grant from different instants combined in a window; missing or ambiguous ACL history treated as allowed; historical questions sent to the current ReBAC engine; ACL history disclosing who had access; historical eligibility presented as proof that someone read something | §10.3, §10.7 (ACL-H1–H6), §14.2 (rules 6–8), §21.2 |
| Inference attacks | Differencing across cohorts, periods, documents, agents, caches; non-linear statistics | §15 |
| Agent misuse | Proxy attack; memory bleed across interviewers; fabricated business facts; impersonation | §14 |
| Prompt injection | Direct and document-borne | §13.7 |
| Metrics layer | SQL injection via templated policy; endpoints bypassing the compiler; cross-tenant caches | §11 |
| Supply chain | Compromised dependency or gateway release; parser vulnerabilities | §18.3, §20.2; parsing sandbox |
| Model output | Confabulation; unsupported qualitative claims | §13.4–13.5 |
| Operational | Break-glass misuse; secret exposure | §9.4, §9.6 |

### 23.3 Security reviews required before production

See §26:

- threat-model review;
- penetration test;
- RLS and authorization review;
- parsing-sandbox review;
- LLM data-flow and residency review (including provider data-processing terms);
- data-protection impact assessment under the applicable regimes;
- licence review of integrated components.

---

## 24. Production scaling and evolution

### 24.1 Scaling dimensions

| Dimension | Approach |
|---|---|
| Tenants | More cells. Pooled cells for the long tail; silo cells on demand |
| Ledger and projection volume | Partition by tenant (and time); tenant-leading indexes; read replicas for projections |
| Text volume | PostgreSQL FTS → OpenSearch projection |
| Vector volume | Tenant-partitioned pgvector. A dedicated vector store only if a trigger fires |
| Interactive load | Stateless core replicas; per-tenant concurrency limits; exact-key caching |
| Durable work | DBOS → Temporal (§17.5) |
| Authorization | Engine-native horizontal scaling per cell |

### 24.2 Adoption triggers

Each technology is adopted when its trigger is met **and** a benchmark confirms the need.

| Not deployed initially | Adopt when |
|---|---|
| OpenSearch | Text volume per cell or relevance-tuning needs exceed PostgreSQL FTS |
| Dedicated vector database | Vector count per cell or filtered-search latency targets exceed partitioned pgvector |
| Graph projection | Graph analytics or interactive multi-hop exploration that recursive SQL cannot serve within targets |
| Message broker | Many independent consumer groups, cross-cell streaming, or replay volume beyond the outbox |
| Temporal | §17.5 |
| Differential privacy for people analytics | A self-service people-analytics capability (requires Veloxa's durable privacy-budget ledger) |
| Lakehouse history | Warehouse-scale analytics or customer data-lake integration |

### 24.3 Tenant moves

Pooled→silo and cell→cell moves use logical replication (declarative publications and subscriptions under the PostgreSQL operator):

1. Copy and catch up.
2. Freeze admissions for the tenant.
3. Verify (row counts, hash checks, canary replays at historical bases).
4. Switch the directory entry.
5. Retire the source copy.

This requires every table to be tenant-keyed and no cross-tenant references, both of which the design enforces.

### 24.4 Stages

| Stage | Adds | Exit criteria |
|---|---|---|
| **H. Hackathon** | §25 profile | Must-haves met; canaries and privacy suites green; fresh clone works |
| **P0. Hardening** | Contract tests for every interface; expand–contract migrations; load tests; threat-model review | Interfaces frozen at v1 |
| **P1. Design-partner pilot** | SSO and SCIM; real connectors with ACL streams; SpiceDB, OPA and Cedar deployed; managed PostgreSQL with PITR; object lock; per-tenant keys; residency policy; trace store; **persona invention disabled**; employee consent and visibility for agents | Security review; impact assessment; pilot SLOs |
| **P2. Multi-tenant SaaS** | Pooled cells; Valkey; dense retrieval enabled; tenant-configurable risk weights and disclosure parameters; online evaluation; GitOps; DR drills | Isolation tests in CI; audit readiness |
| **P3. Enterprise** | Silo cells; residency regions; customer-managed keys; adoption-trigger components as needed; SIEM integration; model-risk governance mapping | Enterprise security reviews |

---

## 25. Hackathon implementation profile

The hackathon is a **deployment and configuration profile of this architecture**, not a different architecture. Every component runs either as specified, or as a real implementation of the same interface with a lighter backend. No technology is introduced that production would have to replace with a different data model or contract.

### 25.1 Configuration

| Component | Hackathon configuration | Production difference |
|---|---|---|
| Cells and tenants | One cell. Tenant `veloxa` (`synthetic = true`), plus an empty second tenant used only for isolation tests | Many cells; pooled and silo |
| PostgreSQL | PostgreSQL 18 container with `btree_gist` and pgvector; all schemas of §6.2; audit as a separate schema | Operator-managed clusters; separate audit store |
| RLS | Full design of §9.3–9.4, including startup self-checks and the conformance suite | Same |
| Raw landing | The release-pack archive or folder is **supplied by path at setup, never committed to the repository**. A manifest with content hashes; read-only mount | Object storage with object lock |
| Connectors | File-drop connector with versioned parsers per export type. Party relations (senders, recipients, assignees, authors, responders) are derived from records as the stand-in for source ACL streams | API/webhook connectors with ACL and identity streams |
| Admission | Four admission batches, one per release folder (Day 1 to Day 4), each sealed at its release time (10:00 IST on 16, 17, 18 and 19 Nov 2026). A demo clock clamps K | Continuous batches |
| Entity resolution | Deterministic rules + owner-authored decision files with context-scoped aliases. Probabilistic matching is not needed at this scale; the interface is present | Splink-backed matching + review queue |
| ReBAC | Relationship tuples in a PostgreSQL table using a SpiceDB-compatible schema shape, behind `RelationshipStore`. Running a SpiceDB container is optional | SpiceDB |
| ABAC / agent tools | Policy rules evaluated in-process behind `PolicyDecisionPoint` | OPA (data) and Cedar (agent tools) |
| Governed metrics | Real: roughly 15–20 definitions with time semantics, data class, disclosure declarations and tests | Same, with tenant-specific definitions |
| Retrieval | PostgreSQL FTS + entity-aware lookup + whole-scope strategy. **Dense retrieval disabled by configuration**: near-identical names refer to different entities in this dataset, and the corpus fits the whole-scope strategy | Hybrid with tenant-partitioned pgvector |
| Pipeline | Full multi-stage pipeline including the independent verifier | Same |
| Disclosure auditor | Real (§15), including all channels and background rows | Same; parameters per tenant |
| Risk engine | Real (§16). The weights, anchors and gates file is committed before later release material is analysed. If the delivery-probability gates fail on the supplied data, a deterministic bound is shown instead (§16.4) | Tenant-configured weights |
| Agents | **14 employee agents** on the shared runtime: KB ∩ AZ ∩ POL, per-agent knowledge cutoffs, per-interviewer memory, persona specs with assumption IDs, no tools that act | Real employees: consent; no persona invention |
| Workflows | DBOS in-process | DBOS → Temporal |
| `LLMClient` | Claude via the Anthropic API; budgets; record/replay for tests | Same; residency routing across endpoints |
| Observability | OpenTelemetry with a console or OTLP exporter; per-answer trace rows | Full stack + trace store |
| Deployment | Docker Compose (application + PostgreSQL); one setup command taking the data path; CI fresh-clone job | Kubernetes, GitOps |
| Frontend | **Undecided.** Only the contracts of §19 are fixed | — |

**Not run in the hackathon** (interfaces present): Temporal, SpiceDB and OPA servers, the Cedar engine, Valkey, OpenSearch, a graph projection, Kubernetes, multiple cells, SSO, a gateway proxy, a trace store.

### 25.2 Process obligations (from the challenge instructions)

- **Source judgements are the owner's.** Source-truth judgements, conflict resolutions and identity decisions are authored by the owner, with reasoning recorded in `TRANSCRIPT_NOTES.md`. AI tools do not decide which source is true.
- **Assumptions are logged.** Every invented persona detail and every interpretive assumption has an entry in `ASSUMPTIONS.md`: date, assumption, source line, confidence, what would change the owner's mind. Entries are revised, never deleted.
- **Voice notes.** Each voice note gets a written response (change or pushback), committed by 23:59 IST on its release day.
- **Required documentation:** `README.md`, `ARCHITECTURE.md`, `ASSUMPTIONS.md`, `TRANSCRIPT_NOTES.md`, `DECISIONS.md`, `AI_LOG.md`.
- **Repository:**
  - public, with at least two meaningful commits per day;
  - no raw challenge data in the repository;
  - a single setup-and-run command from a fresh clone;
  - final submission of the repository link, frozen commit hash and command by the code-freeze time.

### 25.3 Requirements traceability

| ID | Requirement | Component(s) | API capability | Journey | Acceptance tests |
|---|---|---|---|---|---|
| H-01 | All four release folders loaded into the designed model | Connector, parsers, ledger, admission sequencer | `/meta/releases`, `/admin/ingest` | J9 | Every file classified; counts per folder; batch per folder sealed at its release time |
| H-02 | Reconciled with transcripts; believed source and reason recorded | Conflicts + human resolutions | `/conflicts` | J6, J2 | Every curated conflict has a status; resolved ones reference a `TRANSCRIPT_NOTES.md` entry |
| H-03 | 14 people as AI agents | Agent registry, runtime | `/agents` | J5 | Exactly 14 agents mapped to HR identities |
| H-04 | Role, workload, history | Person facts, metrics | `/agents/{id}/profile` | J5 | Identity probes for all 14 at each basis; workload facts cite records |
| H-05 | Personal life invented and logged | Persona specs (synthetic) | `/agents/{id}/persona` | J5 | Every persona item has an assumption ID present in `ASSUMPTIONS.md`; persona items never cited by the cockpit |
| H-06 | Agents consistent with records | Verifier; KB ∩ AZ ∩ POL | Interview endpoints | J5 | Temporal probes (at each admission boundary; post-exit); proxy-attack, boundary and isolation tests |
| H-07 | Agent memories | Memory store | `/agents/{id}/memories` | J5 | Persists for the same interviewer; isolated across interviewers; hidden at earlier bases |
| H-08 | One question → short answer | Pipeline | `/ask` | J2 | Gold set (H-15) |
| H-09 | Cites specific records | Evidence compiler, verifier | `/evidence/{id}` | J3 | Citation validity 100%; every claim cites a visible record or fact |
| H-10 | States a confidence level | Grading | `Answer.grade` | J2 | Expected grade = produced grade |
| H-11 | "I don't know" / "the sources disagree" | Status precedence | `Answer.status` | J2 | Uncertainty and conflict cases (H-16) |
| H-12 | Ranked "what should I worry about", evidence-backed | Risk engine | `/briefing`, `/risks` | J1 | Reproducible ranking hash; every item has evidence and a quantification state; no probability when the gates fail |
| H-13, H-17 – H-19 | Requirements arising from the content of voice notes released on D2–D4 | — | — | — | Held until each voice note's release day, then added here |
| H-14 | Point-in-time answers using release dates; no leakage | Temporal architecture | Time parameters on all reads | J4 | Canaries at every batch boundary across all surfaces; pre-admission KNOWN_AT returns "nothing admitted" plus an offer; RECORDED_BY semantics; runtime objects stamped with session K |
| H-15 | ≥ 25 automated gold questions, one command | Evaluation harness, recorded responses | Test command | — | At least 25 (target 30+), runnable offline from a fresh clone |
| H-16 | ≥ 5 refusal/uncertainty tests | Pre-screen, abstention, conflicts | `/ask` | J2, J8 | Individual pay refused; attrition ranking refused; message reading refused, with a missing-source note; absent-source questions abstain; forecasts without basis abstain; conflicting figures reported as conflicts; ungated probability withheld |
| H-20 | Written voice-note responses on release days | Documentation | — | — | One response file per note, with commit times |
| H-21 | Administration | Admin functions | `/admin/*` | J9 | One test per admin function; admin role required |
| H-22 | Company memory | Timeline, decisions, conflicts, diff | Memory endpoints | J6 | Events cite records; diff between consecutive batches equals exactly the newer batch's material |
| H-23 – H-28 | `README`, `ARCHITECTURE`, `ASSUMPTIONS`, `TRANSCRIPT_NOTES`, `DECISIONS`, `AI_LOG` | Documentation | — | — | Presence and format checks |
| H-29 | Public repository; ≥ 2 commits per day | Process | — | — | Commit-history check |
| H-30 | Fresh clone + one command | Compose; data-path setup | — | — | CI clean-clone job; clear error when data is missing |
| H-31 | Final submission | Process | — | — | Checklist |
| H-S | Stretch: post-cutoff activity | Simulation namespace | `/agents/{id}/activity` | J5 | Simulated events never appear in evidence |

---

## 26. Deferred and unresolved decisions

An item here is **not** approved. Where a default is stated, it governs implementation until the item is closed, and it is labelled as a default.

### 26.1 Unresolved design decisions

| ID | Decision | Current position | Closed by |
|---|---|---|---|
| D-22 | Approximate (interval) disclosure in the auditor | **No option selected.** Options: accept and document; probabilistic simulatable auditing; interval checks for pre-defined tables only; DP noise for non-privileged people aggregates | Owner risk-appetite decision; privacy review |
| D-13a | Agent response to withheld knowledge | Default: uniform non-disclosure | Owner confirmation |
| D-13b | Work-message excerpts for executives (messages between other employees) | No default. Either business-sentence excerpts (date only), or exclusion | Owner decision; privacy review |
| D-14a | Disclosure parameters: *k*; group ledgers | Default *k* = 5; per-principal ledgers only | Tenant policy; owner confirmation |
| D-15 | Risk-model weights, anchors, guardrail tiers, gate thresholds | Structure approved; values not set | Owner value judgement; committed before later material is analysed |
| D-11a | Coarse data-class restrictive policies in RLS keyed on principal role | Not adopted by default | Security review |
| D-11b | Criteria for assigning tenants to silo cells; per-tenant database roles in pooled cells | Per contract | Product/security policy |
| D-12a | Vector partition granularity, over-fetch, exact-search threshold, iterative-scan limits | Not set | Benchmarks (§26.4) |
| D-10a | Maximum ACL staleness per data class (also bounds how long a resource is treated as still existing after its latest observation, §10.7) | Not set | Security review with source behaviour |
| D-10b | Maximum lookback for `ELIGIBILITY_DURING_WINDOW` (§10.7), per data class | Value not set. Until set, the window starts at the start of the represented person's earliest identity link and ends at min(t_h, knowledge cutoff) (default) | Owner confirmation; privacy review |
| D-08a | Per-knowledge-point lexical index vs accepting corpus-wide ranking statistics | Accept and document (default) | Owner confirmation |
| D-14b | Shared interviewer memories for declared groups | Off by default | Tenant policy |
| D-06a | Erasure design (crypto-shredding + tombstones preserving verifiability) | Direction approved; design open | Design review; legal review |

### 26.2 Unselected implementation options

| Area | Candidates | Note |
|---|---|---|
| Trace and evaluation store | Langfuse (preferred candidate; MIT core, OTLP ingest); alternatives | Audit and SSO features of some candidates are commercially licensed; Veloxa audit stays in-house regardless |
| Gateway proxy | None (default); hardened, hash-pinned LiteLLM on a private network; Envoy-based gateway | Policy remains in `LLMClient` in all cases |
| Outbox transport | Custom relay; Procrastinate; pgmq | All PostgreSQL-native |
| Document parsing | Docling; Unstructured | Sandboxed in all cases |
| Connector libraries | dlt (library); selected Airbyte connectors | Airbyte connectors are ELv2-licensed: licence review required |
| Data-quality tooling | Veloxa contracts (default); Great Expectations as an optional adapter | soda-core v4 is ELv2 |
| Graph projection | Dedicated graph database; in-PostgreSQL openCypher extension | Only if triggered (§24.2) |
| Audit anchoring | Transparency-log library; other external anchoring | Fit unverified |
| Metric definition format | Veloxa format; alignment with an open metric interchange format | Portability only |
| Streaming transport for incremental answers | Server-sent events; WebSocket | Frontend phase |
| Frontend technology, visual system, layout | — | Separate owner decision |
| Model tiering per pipeline stage | Single default model; cheaper models for selected stages | After live evaluation |
| Cache introduction | Valkey when multi-instance | Timing |

### 26.3 Unverified technical assumptions

| Assumption | Verify by |
|---|---|
| RLS cannot be enabled on materialized views | Test on the target PostgreSQL version |
| Querying a partition directly applies the partition's own RLS settings | Test |
| With only restrictive policies applicable, no rows are accessible | Test |
| Unset, empty and malformed tenant contexts all fail closed with the chosen policy expression | RLS conformance test |
| Model identifiers, prices and API behaviours (structured outputs, effort controls, tool-choice limits, caching minimums) | The provider's live model listing and documentation at implementation |
| Deterministic collations are required for equality columns in GiST exclusion constraints | Test |
| DBOS recovery is restricted to runs with a matching application version | Source review / test |
| Per-tenant partitioning practice in SpiceDB (prefixes vs instances) | Vendor documentation / test |
| HR master join dates were recorded no later than the effective date (implied-recording rule) | Logged assumption; tenant confirmation |
| PgBouncer version containing the parameter-tracking fix | Release notes |
| Policy-compilation output of the ABAC engine for nested joins is production-stable | Test |
| Production-usage and performance claims made by component vendors | Treated as unverified |

### 26.4 Decisions requiring production benchmarks

- RLS overhead with tenant-leading indexes at target volumes.
- pgvector recall and latency under ACL selectivity, partitioning and iterative-scan limits; the exact-search threshold.
- ReBAC reverse-lookup latency and consistency-token behaviour at target relationship volumes.
- Disclosure-auditor latency with thousands of persons and long ledgers.
- DBOS throughput and the Temporal trigger points.
- Verifier latency and cost per answer; fusion parameters; end-to-end latency targets (NFR-08).
- Adoption triggers for OpenSearch, a vector database and a graph projection.

### 26.5 Decisions requiring security, privacy or legal review

- Threat-model review and penetration test before the pilot.
- RLS and authorization review (including break-glass procedure).
- Parsing-sandbox review.
- LLM data flows: provider data-processing terms, residency and retention.
- Data-protection impact assessment under applicable regimes (for Indian tenants, the DPDP Act 2023 and Rules 2025; GDPR for EU tenants).
- Licence review of integrated components (ELv2 connectors; any AGPL component excluded; GPL tools only out of process in testing).
- Supply-chain process for key-holding components.

---

## 27. Architecture decision register and references

### 27.1 Decision register

The owner approved the production-first baseline on 16 Nov 2026. Under that approval, the recommendations of the research decision register are **approved**, except the items it marked unresolved, which are listed in §26.

| ADR | Decision | Status | Sections |
|---|---|---|---|
| ADR-01 | PostgreSQL 18 is the system of record at every stage, including the hackathon | Approved | §6 |
| ADR-02 | Cell-based multi-tenancy: pooled cells with RLS backstop; silo cells for high assurance | Approved (silo criteria deferred) | §9 |
| ADR-03 | Three time axes (admission, recorded, valid) with explicit query modes and versioned corrections | Approved | §8 |
| ADR-04 | K is a sealed, commit-ordered admission sequence; the ledger is append-only; mutable ranges exist only in rebuildable projections | Approved | §6, §8 |
| ADR-05 | Multi-stage answering pipeline with governed metrics, hybrid retrieval, evidence compilation and an independent verifier | Approved | §11–§13 |
| ADR-06 | Layered authorization: RLS → ReBAC (SpiceDB) → ABAC (OPA) → agent-tool policy (Cedar) → disclosure auditor → model permissions → output validation. ACL history is Veloxa-owned | Approved | §10 |
| ADR-07 | RLS operating model: FORCE RLS, restrictive tenant policy, transaction-local context, pooling rules, role set, view and definer rules, tenant-scoped keys, self-checks | Approved (D-11a/b deferred) | §9 |
| ADR-08 | Vector tenancy and ACL layout: tenant partitions, ACL columns filtered in SQL, exact-subset path, authorise-before-anything | Approved (tuning deferred) | §12 |
| ADR-09 | Governed metrics hardening: declared definitions, bind parameters only, single policy compiler, policy-keyed caches, cache reads as disclosures, formal combination semantics | Approved | §11 |
| ADR-10 | Verifier: atomic claims, per-citation three-valued entailment, deterministic checks, drop/downgrade only | Approved | §13 |
| ADR-11 | Evidence grades A–D with qualifiers; status precedence; no model-stated confidence | Approved | §13.5 |
| ADR-12 | Employee agents: shared runtime; KB ∩ AZ ∩ POL; no credentials or actions; governed memory with per-interviewer isolation and audited invalidation; persona invention only in synthetic tenants | Approved (D-13a/b deferred) | §14 |
| ADR-13 | Disclosure auditor: stable-pay linkage, coefficient rows across all channels, background rows, value-independent decisions, small-group policy, refusal of non-linear statistics, exact arithmetic, fail-closed | Approved (D-14a deferred; D-22 deferred) | §15 |
| ADR-14 | Risk engine: impact criteria, interval scores, dominance ranking with guardrail tiers, evidence gates for probabilities, quantification states, allocation-only people data | Approved (D-15 values deferred) | §16 |
| ADR-15 | Durable execution behind `ActivityRunner`: DBOS initially, Temporal at scale; outbox; idempotency; pinned step names | Approved (outbox transport unselected) | §17 |
| ADR-16 | `LLMClient` owns model policy: routing, residency, model permissions, redaction, pre-call budget reservations, record/replay; any proxy optional | Approved (proxy unselected) | §18 |
| ADR-17 | Service decomposition: modular core; separate connector, indexing/projection and parsing workers; off-the-shelf authorization, workflow and telemetry services | Approved | §20 |
| ADR-18 | Tenant directory, cell routing and tenant-move strategy from day one | Approved | §5, §24.3 |
| ADR-19 | Frontend-independent API contracts covering journeys J1–J9 | Approved (frontend and transport deferred) | §19 |
| ADR-20 | Evaluation layers including recorded model responses, leakage canaries, privacy, retrieval-leak and agent suites | Approved | §21 |
| ADR-21 | No agent framework in the core; no external memory system of record; no model-authored SQL | Approved | §13, §14, §11 |
| ADR-22 | Build/adopt/integrate/adapt map from the repository research | Approved as baseline; specific product options in §26.2 | §5.1, §27.3 |
| ADR-23 | Hackathon profile: data supplied by path (never committed); single-command setup; profile of §25 | Approved (exact command fixed at implementation) | §25 |
| ADR-24 | Approximate disclosure | **Deferred** | §15.7, §26.1 |

### 27.2 Superseded recommendations

These earlier recommendations must not be reintroduced:

| Superseded | Replaced by |
|---|---|
| SQLite for the hackathon; LLM-free template answers as the test path | ADR-01; ADR-20 |
| One full-context model call as the production answering design; router over a fixed tool list | ADR-05 (whole-scope retained only as a context strategy) |
| A single "as-of" parameter conflating recorded-by and valid-at; silent re-interpretation of pre-admission dates | ADR-03 |
| Knowledge time as a timestamp column; mutable ranges in the ledger | ADR-04 |
| Session-level `SET` of tenant context; RLS without FORCE or `WITH CHECK` | ADR-07 |
| "Pre-filtering inside the vector index" | ADR-08 (post-scan filtering inside the executor, with partitioning) |
| Agents acting as principals with employees' permissions | ADR-12 |
| Pairwise cohort-difference rule; rounded latest-quarter payroll as a privacy control | ADR-13 |
| Impact × urgency risk scores; ungated Monte Carlo probabilities; after-hours activity metrics | ADR-14 |
| An LLM gateway proxy as the key-holding perimeter and budget enforcer | ADR-16 |
| Temporal required from the pilot | ADR-15 |

### 27.3 Build / adopt / integrate / adapt summary

| Approach | Components |
|---|---|
| **Build (differentiated)** | Temporal visibility and admission sequencing; evidence ledger and record-level provenance; judgement store; governed metrics and policy compiler; retrieval service and evidence compiler; answer pipeline and verifier; disclosure auditor; risk engine; agent runtime and governed memory; ACL ingestion contract and ACL history; tenant directory and router; `LLMClient`; audit hash chain |
| **Adopt** | PostgreSQL 18; pgvector; PgBouncer; CloudNativePG; object storage; OpenTelemetry and OpenLLMetry instrumentation; pgaudit (operations) |
| **Integrate (behind interfaces)** | SpiceDB; OPA; Cedar; DBOS / Temporal; Splink; document parser (sandboxed); connector libraries; trace store; Valkey; OpenSearch (when triggered) |
| **Adapt (patterns only)** | Transaction-local request context (PostgREST); RLS testing practices (Supabase); permission-aware retrieval (Onyx); policy combination semantics (Cube); valid-time metric vocabulary (MetricFlow); bitemporal memory edges (Graphiti); versioned memory blocks (Letta); single ordered admission log (XTDB); optimistic stream versions (message-db); cursor signposts and lag windows (Meltano, dlt); interval attacks and linked tables as test oracle (sdcTable); tenant-filter enforcement (Qdrant); cell topology (GitLab Cells); pinned durable step names (Pydantic AI, DBOS) |

### 27.4 References

**Internal research (private working documents; not part of the repository).** These record the evidence, verification and change history behind this document:
- architecture research;
- final assessment (revisions r1–r3);
- decision register;
- GitHub repository research;
- changelog.

**Technical sources** (accessed 16 Nov 2026):

| Topic | Source |
|---|---|
| Row security, policies, views, temporal constraints, btree_gist, statistics | PostgreSQL 18 documentation: *Row Security Policies*, *CREATE POLICY*, *CREATE VIEW*, *CREATE TABLE*, *btree_gist*, *pg_stats*; PostgreSQL security advisories |
| Connection pooling | PgBouncer features documentation |
| Vector filtering | pgvector README (filtering, iterative index scans, multitenancy) |
| Disclosure auditing | Kenthapadi, Mishra, Nissim, *Simulatable Auditing*, PODS 2005; Chin, *Security problems on inference control for SUM, MAX, and MIN queries*, J. ACM 1986; Chin & Özsoyoğlu, IEEE TSE 1982; Dinur & Nissim, PODS 2003 |
| Risk ranking | Cox, *What's Wrong with Risk Matrices?*, Risk Analysis 2008; multi-attribute value theory (Keeney & Raiffa) |
| Model confidence and long context | Xiong et al., ICLR 2024; Farquhar et al., Nature 2024; Liu et al., TACL 2024 |
| Fusion | Cormack, Clarke, Büttcher, SIGIR 2009 |
| AI security and governance | OWASP Top 10 for LLM Applications 2025; NIST AI 600-1 |
| Repositories (patterns or integration candidates) | SpiceDB, OPA, Cedar, DBOS, Temporal, CloudNativePG, PgBouncer, PostgREST, Supabase, Onyx, Cube, MetricFlow, Graphiti, Letta, XTDB, message-db, Splink, dlt, Meltano SDK, OpenLLMetry, Langfuse, sdcTable, GitLab Cells design |

### 27.5 Document revision history

| Version | Date | Change | Decision impact |
|---|---|---|---|
| 1.0 | 16 Nov 2026 | Approved production-first baseline | Baseline approved by the owner |
| 1.0.1 | 16 Nov 2026 | **Clarification, not a redesign.** Current authorization of the requester is separated from historical source-ACL evaluation (C32, `HistoricalAclEvaluator`): <br>• two evaluation modes, `POINT_IN_TIME` and `ELIGIBILITY_DURING_WINDOW`, with the same-instant rule; <br>• eligibility is not reading: knowledge-basis labels `participant`, `possible`, `role_plausible`; <br>• ACL, identity and membership history named as canonical in PostgreSQL (§6.2); <br>• historical ACL suite ACL-H1 to ACL-H6 in §21.2. <br>Sections: §5.1–§5.3, §6.2, §10.1–§10.3, §10.7, §13.4 (hedge row), §14.2, §21.2, §23.1–§23.2, §26.1 (new deferred parameter D-10b) | **No ADR reopened.** ADR-06 (layered authorization; Veloxa-owned ACL history) and ADR-12 (KB ∩ AZ ∩ POL) are unchanged; §10.7 and §14.2 state how they apply. PostgreSQL 18 remains the system of record, and SpiceDB remains the current ReBAC projection only. Original approval date retained |
| 1.0.1 (correction pass) | 16 Nov 2026 | **Contract corrections:** <br>• `HistoricalAclResult` separates `historical_at` (the historical instant t_h) from `evaluated_at` (the wall-clock run time, audit only); <br>• `ELIGIBILITY_DURING_WINDOW` intersects the window with the resource's established availability period (new field `resource_available`; reasons `resource_availability_unknown` and `resource_not_available`); <br>• ACL-H3, ACL-H4 and ACL-H6 extended. <br>**Wording:** §25.1 risk-engine row no longer states a dataset result in advance. <br>Sections: §5.2 (C32), §5.3, §10.7, §21.2, §25.1 | **No ADR reopened**; approval date unchanged. `POINT_IN_TIME` is not changed by the availability rule (flagged for owner review in `AI_LOG.md`) |
| 1.0.1 (existence rule) | 16 Nov 2026 | **Owner decision:** the resource-existence check applies to **both** modes, with three states: known to exist (continue), known not to exist (`denied`, `resource_not_available`) and unknown (`unknown`, `resource_availability_unknown`). A first observation is never treated as creation, and non-existence is never inferred from it. `resource_available` is replaced by `ResourceExistence` / `resource_existence`. The D-10a staleness limit also bounds how long a resource is treated as still existing after its latest observation. ACL-H3, ACL-H4 and ACL-H6 updated. Sections: §5.2 (C32), §5.3, §10.7, §21.2, §26.1 (D-10a wording) | **No ADR reopened**; approval date unchanged. This supersedes the correction-pass rule that started availability at a first extraction |
