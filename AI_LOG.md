# AI_LOG

This log records the main tasks given to AI tools, what they produced, where they were wrong, and what was changed. It follows the challenge rule that the owner is responsible for everything AI produces.

**Conventions**
- **Owner** is the project author.
- **AI** is the coding/research assistant (Claude Code). Entries are dated in IST.
- **Who caught it** says whether the owner's review or the AI's own verification found the error.
- **Release-gated details:** entries marked *details deferred* (E6, E11, E19 and three later passages) are completed when the release pack they depend on is published. The full text is kept privately.
- **What AI did not do:** AI was not asked to decide, and did not decide, which source is true in any conflict between transcripts and exports. Conflict conclusions and identity decisions are reserved for the owner, in `TRANSCRIPT_NOTES.md` and the decision files.

---

## Mon 16 Nov 2026 — Phase 1: research and architecture

### Tasks given to AI

| # | Task (owner instruction) | AI output |
|---|---|---|
| 1 | Inventory every challenge file and produce a deep architecture research report with eight companion research documents | Source inventory; a contradiction and uncertainty register (with the "your conclusion" column deliberately left blank for the owner); architecture research; data model; AI/agent design; evaluation strategy; technology comparison; product capabilities; roadmap; sources. Kept in a private research folder, not in this repository |
| 2 | Independently verify and challenge that architecture (Phase 1 verification) | A verification report and a decision document for owner review |
| 3 | Re-evaluate everything as a production-first enterprise architecture, without five-day constraints | A production-first final assessment (r1) and a second decision document |
| 4 | Address eight owner review findings: RLS hardening; pgvector filtering; agent knowledge vs interviewer disclosure; formal payroll disclosure auditor; risk-model challenge; requirements traceability; frontend left undecided; PostgreSQL 18 DDL prerequisites | Assessment revision r2 |
| 5 | Inspect real open-source repositories across 12 capability areas, at source level | A GitHub repository research report (~70 repositories, run as four parallel research passes) and assessment revision r3 |
| 6 | Write the official `ARCHITECTURE.md` from the approved production-first baseline, with deferred items kept explicit | `ARCHITECTURE.md` (this repository) |

### Where AI was wrong, and what changed

| # | AI error or weakness | Who caught it | Correction |
|---|---|---|---|
| E1 | On first read, `email.md` was empty (0 bytes on disk, presumably unsaved in the editor). AI started from the schedule quoted in the prompt | AI noticed; the owner saved the file | Re-read the saved email. It changed the plan: 10:00 IST release times, no live sessions, `AI_LOG.md` required, AI must not decide source truth |
| E2 | The first research recommended SQLite, a router LLM over a fixed tool list, and an "LLM-free" template mode as the test path | AI's own verification pass, then the owner's production-first override | Replaced by PostgreSQL 18 everywhere, a multi-stage pipeline with governed metrics, hybrid retrieval and an independent verifier, and tests that replay recorded model responses |
| E3 | The first temporal model used one "as-of" parameter for both "recorded by" and "true at", and silently re-interpreted "what did we know on X" for dates before any release | AI verification | Three cutoffs (admission K, recorded R, valid V) with four explicit query modes, always shown in the answer |
| E4 | The first DDL compared timestamps as text in SQLite. Date-only upper bounds and mixed time-zone offsets compare incorrectly | AI verification (reproduced with a test) | Instants are compared as instants (`timestamptz`/ranges); date-only values are expanded in the tenant time zone |
| E5 | The first privacy design allowed month-by-month payroll totals. Differencing two consecutive totals across a joiner reveals one person's pay | AI verification (computed exactly) | A formal disclosure auditor (§15 of `ARCHITECTURE.md`). A later, cruder fix (rounded latest-quarter totals) was also shown to leak and was superseded |
| E6 | The first risk design built a people-related risk signal from individual activity-timing data. In effect, that is an individual attrition profile, which the project refuses to build. *(source-specific details are deferred to the D3 release.)* | AI verification | People-related risk criteria use allocation records only (tickets, rotas, requisitions). Activity timing is excluded everywhere |
| E7 | r1 said pgvector "pre-filters inside the index". Wrong: filters, including RLS, apply after the approximate scan | Owner review finding 2 | Corrected, with tenant partitioning, ACL columns filtered in SQL, an exact-search path, and authorise-before-anything for residual checks |
| E8 | r1 RLS design: "per-request `SET app.tenant_id`" (unsafe with transaction pooling); RLS without FORCE or `WITH CHECK`; a primary key described as conditional, which is impossible; `btree_gist` omitted | Owner review findings 1 and 8 | Hardened RLS model (roles, FORCE, restrictive policy, transaction-local context, views, definer allow-list, tenant-scoped keys, self-checks, conformance tests); corrected DDL and prerequisites |
| E9 | r1 said an agent "acts as a principal with the employee's source permissions". That conflated what the employee knew with what the interviewer may see, and implied impersonation | Owner review finding 3 | Answers use only KB(agent) ∩ AZ(interviewer) ∩ policy. No credentials, no actions. Memory isolated per interviewer |
| E10 | r1 payroll rule "cohort difference ≥ k" was pairwise only and missed multi-query combinations | Owner review finding 4 | Row-space auditor with a stable-pay linkage model, every disclosure channel, background-knowledge rows and value-independent decisions. Verified against the simulatable-auditing paper |
| E11 | r1 risk model multiplied ordinal scores and would have shown an ungated Monte Carlo delivery probability, which can be falsely precise. *(dataset-specific details are deferred to the D3 release.)* | Owner review finding 5 | Impact criteria, interval scores, dominance ranking, and evidence gates for probabilities. A deterministic bound is shown when the gates fail |
| E12 | r2 modelled the knowledge time K as an `admitted_at` timestamp and kept a mutable "known" range on a ledger-like table | Repository research (r3) | K is a sealed, commit-ordered admission sequence. The ledger is append-only. Mutable ranges exist only in rebuildable projections |
| E13 | Earlier documents recommended an LLM gateway proxy as the key-holding perimeter and budget enforcer, before its security-advisory history was checked | Repository research (r3) | Model policy (budgets as pre-call reservations, residency, redaction) moved into Veloxa's `LLMClient`. Any proxy is optional and unselected |
| E14 | Some literature and behaviour claims were cited from memory | AI, flagged throughout | Marked as unverified in the research. `ARCHITECTURE.md` §26.3 lists the technical assumptions still to verify |
| E15 | A very long shell command failed on Windows because of the command-length limit | AI | Split the output into smaller files |

### Owner decisions recorded today

- **Production-first override.** The architecture is chosen for a production enterprise product. The five-day deadline is a delivery constraint, not a design constraint.
- **Baseline approved.** The production-first baseline is approved (16 Nov 2026). Unresolved items stay deferred in `ARCHITECTURE.md` §26, including approximate disclosure handling (D-22).
- **Frontend.** Technology and design remain undecided (separate phase).
- **No data in the repository.** Raw challenge data and private research documents stay out of the public repository.

---

## Mon 16 Nov 2026 — Historical ACL clarification (ARCHITECTURE.md baseline v1.0)

### Task given to AI

| # | Task (owner instruction) | Reason | AI output |
|---|---|---|---|
| 7 | Make a narrowly scoped clarification in `ARCHITECTURE.md`: separate **current authorization** (may the requester access this now?) from **historical source-ACL evaluation** (could a represented employee or source principal access an object at a historical read basis?). Keep PostgreSQL 18 as the system of record and SpiceDB as the current ReBAC projection. Preserve the KB ∩ AZ ∩ POL agent model. No approved decision reopened | The v1.0 text did not state which question each mechanism answers, so historical permissions could have been mistaken for current ones | Edits to `ARCHITECTURE.md` only (sections listed below) |

**Files and sections changed**

- `ARCHITECTURE.md`:
  - §5.1: new catalogue row C32 Historical ACL evaluator.
  - §5.2:
    - C20–C22 card states "current authorization only";
    - new C32 card;
    - C23/C24 interfaces and security boundary.
  - §5.3: `RelationshipStore` annotated as current-only; new `HistoricalAclEvaluator` contract and `HistoricalAclResult`.
  - §10.1: layer 2 wording; statement that historical evaluation is not an authorization layer.
  - §10.2: history includes group memberships; historical questions go to C32.
  - §10.3: current vs historical group expansion.
  - New §10.7: authorization rule, evaluation semantics, outcomes, provenance, separation from SpiceDB, tests ACL-H1 to ACL-H6.
  - §14.2: KB defined via historical evaluation of the represented employee; AZ defined as the interviewer's current authorization; new rules 6–7.
  - §23.1: trust-boundary rows.
  - §23.2: threat-register row.
- `AI_LOG.md`: this entry.
- **Not changed:** `ASSUMPTIONS.md`, `TRANSCRIPT_NOTES.md` and `DECISIONS.md` do not exist yet and were not created. No research documents, challenge data or earlier log entries were modified.

### Where AI was wrong, and what changed

| # | AI error or weakness | Who caught it | Correction |
|---|---|---|---|
| E16 | `ARCHITECTURE.md` v1.0 (AI-drafted) was ambiguous: <br>• `AZ(u, K)` read as "ReBAC/ABAC at K", which could be taken as historical authorization of the interviewer; <br>• group expansion was described only "at query time", without distinguishing current from historical memberships; <br>• KB relied on "source ACL and party relations" with no evaluation contract or fail-closed rule | Owner (this task) | §10.7 contract and rule; AZ defined as current authorization evaluated now; KB defined via `HistoricalAclEvaluator` with `unknown` = not allowed |
| E17 | While editing, the new §10.7 test list was first labelled as part of "authorization suites (§21.2)". §21.2 has no section by that name | AI self-check (cross-reference grep) | Re-pointed to the existing deterministic, leakage-canary and agent suites of §21.2. §21 itself not edited |

### Design points stated for owner visibility (not recorded as owner decisions)

- **Point-in-time access.** The evaluator answers "access at t_h". This is the fail-closed reading of the existing principle that unknown or ambiguous access is denied. An "accumulated exposure" interpretation (ever had access before t_h) is *not* part of the contract. The owner may ask to add it later.
- **No new assumption.** No new assumption about Veloxa data was introduced. The existing hackathon rule (party relations derived from records as the stand-in for source ACLs) is unchanged, so no `ASSUMPTIONS.md` entry was needed.

### Confirmations

- **No source truth decided.** AI did not decide which source is true in any conflict.
- **No owner decisions made.** AI did not write any owner conclusion or record any owner decision. No entry was added to `DECISIONS.md`.

---

## Mon 16 Nov 2026 — ARCHITECTURE.md v1.0.1 and documentation preparation

### Task given to AI

| # | Task (owner instruction) | AI output |
|---|---|---|
| 8 | **(a)** Finalise the historical ACL clarification as `ARCHITECTURE.md` v1.0.1: <br>• two evaluation modes (`POINT_IN_TIME`, `ELIGIBILITY_DURING_WINDOW`); <br>• historical eligibility is not proof of reading; <br>• current interviewer authorization always mandatory; <br>• ACL history canonical in PostgreSQL; SpiceDB current-only; <br>• ACL-H1 to ACL-H6 in §21.2; <br>• no ADR reopened. <br>**(b)** Create `ASSUMPTIONS.md`, `TRANSCRIPT_NOTES.md` and `DECISIONS.md` from the research and the architecture, with every owner conclusion and owner rationale left for the owner. <br>**(c)** Log the task | `ARCHITECTURE.md` v1.0.1; three new documentation files (drafts for owner review); this entry |

**Files created or modified**

- **`ARCHITECTURE.md`** (modified, v1.0 → v1.0.1; approval date 16 Nov 2026 kept):
  - Header: version and revision note.
  - §5.2: C32 card (two modes, eligibility ≠ reading, window failure).
  - §5.3: `HistoricalAclEvaluator` now takes `mode` and `window`. `HistoricalAclResult` adds `mode`, `window`, `eligible_intervals`, `grant_relations` and the reasons `partial_history` and `window_invalid`.
  - §6.2: ACL history named in `ledger`; ACL projections named in `knowledge`; new rule that ACL history is canonical in PostgreSQL.
  - §10.3: the historical-expansion row.
  - §10.7:
    - normative rule 5 (eligibility is not reading);
    - the two modes and the window rules;
    - the same-instant rule;
    - outcomes per mode;
    - grant relations in provenance;
    - ACL-H1 to ACL-H6 extended to cover windows.
  - §13.4: hedge-check row.
  - §14.2:
    - KB uses window mode;
    - knowledge-basis labels `participant`, `possible` and `role_plausible`;
    - rule 8 (access is not reading).
  - §21.2: new "Historical ACL suite" row; agent suites gain knowledge-basis probes.
  - §23.2: threat row extended.
  - §26.1: new deferred parameter D-10b (maximum window lookback).
  - New §27.5: document revision history.
- **`ASSUMPTIONS.md`** (created): A-001 to A-023, each with date, assumption, exact source line, pack, proposed confidence and "would change my mind". All are marked *Draft: owner to review*. It also lists the assumptions deliberately not made, and reserves a persona section (A-101 onwards; none written).
- **`TRANSCRIPT_NOTES.md`** (created):
  - suspected mishearings M-01 to M-14;
  - contradictions C-01 to C-27 (the research IDs), plus five new items, C-28 to C-32;
  - identity question I-01;
  - open ambiguities U-01 to U-04.
  
  Each item has evidence with source references, candidate interpretations, what would settle it, and the pack it becomes visible in. **All 33 owner-conclusion tables and all 14 mishearing conclusion cells are blank.**
- **`DECISIONS.md`** (created):
  - the documented owner statements, quoted verbatim;
  - approved decisions DEC-01 to DEC-22, each with alternatives, trade-offs, status and documented rationale (**"Owner rationale: Owner to write"** throughout);
  - deferred decisions, kept separate and not approved (D-22, D-13a/b, D-14a/b, D-15, D-11a/b, D-12a, D-10a/b, D-08a, D-06a, frontend);
  - superseded recommendations.
- **`AI_LOG.md`**: this entry.
- **Not changed:** the private research documents, challenge data and release packs, and every earlier `AI_LOG.md` entry (tasks 1–7, errors E1–E17).

### Where AI was wrong, and what changed

| # | AI error or weakness | Who caught it | Correction |
|---|---|---|---|
| E18 | In `ARCHITECTURE.md` v1.0 (task 7), KB counted every historical `allowed` as something the employee "could know", with no distinction between having access and having read. It also supported only point-in-time evaluation. Its design note had called the window reading out of contract | Owner (this task) | `ELIGIBILITY_DURING_WINDOW` with a same-instant rule; knowledge-basis labels; §14.2 rule 8; a verifier hedge for access-only items |
| E19 | A count in the private research report was wrong. *(details are deferred to the D3 release.)* | AI (recomputed from the source export) | The wrong figure is not used in any public file. The research file was not modified (out of scope) |
| E20 | The research contradiction register (C-22) described the flat ledger payroll line as unchanged "despite join/leave events". The HR export shows no joins or exits in the period the ledger extract covers | AI (checked against the HR export) | `TRANSCRIPT_NOTES.md` C-22 states the evidence neutrally and lists "genuine: no headcount or pay changes" as a candidate reading |
| E21 | The private research report (§4.5) presents payroll aggregates in a form that would allow inference about one individual's pay (the leak class behind E5). Wording generalised in the correction pass | AI (while deciding what the public files may contain) | No public file contains individual pay or any combination from which it could be derived. **The research files must stay private** |
| E22 | Drafting slips in the new files: <br>• a paragraph-range citation in A-006 did not match the transcripts; <br>• two items (U-01, U-02) were missing from the `TRANSCRIPT_NOTES.md` visibility table | AI self-check | Fixed before completion |

### Points for owner attention (not decided by AI)

- **Release gating of the notes.** Many `TRANSCRIPT_NOTES.md` and `ASSUMPTIONS.md` entries rest on D2–D4 material, which the challenge treats as released later in the week. Every entry is tagged with its pack. The owner decides whether to commit them all now or stage them by release day.
- **Quotation level.** The new files quote short excerpts and cite employee names and IDs from the (fictional) material, which `TRANSCRIPT_NOTES.md` needs as evidence. The owner instructed that transcripts, emails, payroll and source exports must not be copied into the repository. The owner should review whether this level of quotation is acceptable before committing.
- **Supersedes an earlier design point.** The task-7 note "an accumulated-exposure interpretation is not part of the contract" is superseded by the owner's instruction to add `ELIGIBILITY_DURING_WINDOW`. The earlier entry is kept as history.

### Confirmations

- **No source truth decided.** AI did not decide which source is true in any conflict. Candidate interpretations are unranked, and every owner-conclusion field is blank.
- **No owner decisions or reasoning invented.**
  - `DECISIONS.md` quotes only statements the owner wrote in instructions on 16 Nov 2026.
  - Every owner-rationale field reads "Owner to write".
  - No deferred decision was approved; D-22 remains unselected.
  - `ASSUMPTIONS.md` entries are drafts, and their confidence values are proposals.
- **No ADR reopened.** ADR-01 to ADR-24 are unchanged. v1.0.1 is a clarification recorded in §27.5.
- **No implementation.** No application code, tests, Docker, infrastructure, commits or pushes.

---

## Mon 16 Nov 2026 — Documentation correction pass

### Task given to AI

| # | Task (owner instruction) | AI output |
|---|---|---|
| 9 | A targeted correction pass on `ARCHITECTURE.md`, `TRANSCRIPT_NOTES.md`, `DECISIONS.md`, `ASSUMPTIONS.md` and `AI_LOG.md`: <br>• split the historical instant from the evaluation time in `HistoricalAclResult`; <br>• bound `ELIGIBILITY_DURING_WINDOW` by the resource's availability; <br>• correct "Visible from" and pack tags against the release schedule; <br>• make the public files safe; <br>• separate ground rules and observed facts from assumptions. <br>No redesign, no ADR reopened, no source truth decided, no new assumptions | Edits to the five files (below) |

**What changed, by file**

- **`ARCHITECTURE.md`** (still v1.0.1; approval date unchanged; a correction row added in §27.5):
  - §5.3 `HistoricalAclResult`:
    - `historical_at` now holds the historical instant t_h;
    - `evaluated_at` is now the wall-clock run time, for audit only and never a decision input;
    - new field `resource_available`;
    - new reasons `resource_availability_unknown` and `resource_not_available`.
  - §10.7: a note explaining `historical_at` and `evaluated_at`; a new window rule intersecting the window with the resource's established availability period (existence never inferred earlier than the evidence); window outcomes rewritten over `w ∩ availability`; provenance list updated; ACL-H3, ACL-H4 and ACL-H6 extended.
  - §5.2 C32: the Inputs → outputs and Failure rows.
  - §21.2: the historical ACL suite row.
  - §25.1: the risk-engine row no longer states a dataset result in advance (that result depended on D3 material).
  - Header revision note.
- **`TRANSCRIPT_NOTES.md`**: rewritten in place. The IDs, parts, candidate interpretations and blank owner tables are all kept.
  - **Every evidence line now carries a pack tag**, and "Visible from" is the latest tag among all cited evidence. 14 items changed:
    - M-01, M-13 → D4;
    - M-03, M-04, M-09, M-10 → D2;
    - M-05, M-07, M-12 → D3;
    - C-01, C-30 → D2;
    - C-17, C-27 → D3;
    - C-15 → D4 (with a note that it can be committed on D2 if its single D4 bullet is held back).
    
    M-09 and C-15 were not in the owner's list; they were found by checking every citation.
  - **Public safety:**
    - verbatim excerpts replaced by paraphrases with paragraph or sheet-row locators;
    - personal names and employee IDs replaced by job titles and `HR row n` locators;
    - financial figures replaced by qualitative relations;
    - vendor names and invoice, voucher, requisition and incident identifiers removed, including two file names that embedded an invoice number and a person's initials;
    - gendered pronouns that had been assigned to two roles removed.
  - C-21 is flagged for owner review.
- **`ASSUMPTIONS.md`**: rewritten in place.
  - New §0 "Ground rules and observed facts", GR-01 to GR-07. Stubs remain at the old IDs:
    - A-001 → GR-01;
    - A-013 → GR-04 (D1 part) and GR-07 (D3 part);
    - A-014 → GR-05;
    - A-015 → GR-06;
    - A-022 → GR-03;
    - A-003 split, with its explicit-offset part moving to GR-02.
  - A-002 was reviewed and kept as an assumption: the email does not say in terms that it overrides the brief.
  - Packs recomputed. Later-pack citations were dropped where an earlier source suffices (A-004, A-006, A-012, A-016, A-021, GR-03, GR-05). A-016's D2 email example was replaced by a D1 one.
  - Same public-safety treatment as `TRANSCRIPT_NOTES.md`.
  - A-011, A-016, A-017 and A-018 are explicitly marked **Not approved**.
- **`DECISIONS.md`**:
  - DEC-06 notes the timing-field split and the availability bound.
  - DEC-18's rationale no longer states a D3-derived result.
  - The D-14a note no longer quotes a D2 figure.
  - DEC-09's reference repointed from A-001 to GR-01.
  - Revision-log line added. No decision added, approved or changed in substance.
- **`AI_LOG.md`**: rows E19 and E21 of task 8 generalised (they contained a per-person workload breakdown and a description of which payroll aggregates leak). E20's wording made neutral. This entry appended. All other earlier entries are unchanged.

### Where AI was wrong, and what changed

| # | AI error or weakness | Who caught it | Correction |
|---|---|---|---|
| E23 | In v1.0.1, `HistoricalAclResult.evaluated_at` held the historical instant t_h. The name suggests the time the evaluation ran, and there was no field for that | Owner (this task) | `historical_at` = t_h; `evaluated_at` = run time (audit only) |
| E24 | The v1.0.1 window rules bounded the window by identity links, the lookback, t_h and the knowledge cutoff, but **not** by when the resource existed. Eligibility could have been reported for a period before the object existed | Owner | Window intersected with the established availability period; fail closed when it cannot be established |
| E25 | "Visible from" in `TRANSCRIPT_NOTES.md`, and the pack column in `ASSUMPTIONS.md`, were set from the main evidence rather than from **every** citation, so entries marked D1 or D2 cited D2–D4 material | Owner (12 listed items); AI found M-09, C-15 and several `ASSUMPTIONS.md` citations while rechecking all of them | Per-citation pack tags; packs recomputed; later-pack citations dropped where an earlier source suffices |
| E26 | The task-8 files were not safe to publish. They reproduced verbatim excerpts, personal names, employee IDs, exact figures, vendor and invoice identifiers, and payroll details, and log rows E19 and E21 described individual-level facts | Owner | Paraphrases with neutral locators; role labels; figures and identifiers removed; E19 and E21 generalised |
| E27 | Instructions, release rules and observed facts were filed as assumptions | Owner | Moved to §0 of `ASSUMPTIONS.md`, with stubs kept at the old IDs |
| E28 | Further problems found during this pass: <br>• `ARCHITECTURE.md` §25.1 and `DECISIONS.md` DEC-18 stated a result derived from D3 material; <br>• two file-name locators embedded identifiers; <br>• two roles had been given gendered pronouns | AI self-check | Reworded or removed, as described above |

### What remains unresolved (owner review)

1. **Later-pack content in `ARCHITECTURE.md`**, not changed in this pass because it is part of the approved traceability:
   - §25.3 rows H-13, H-17, H-18 and H-19 describe the content of voice notes released in D2–D4;
   - the example in the §14.6 lifecycle table anticipates a D4 event.
   
   The owner should decide whether to generalise them before the D1 commit.
2. **Earlier `AI_LOG.md` entries E6 and E11** (task 1–6 section) describe analysis of D3 material. They were preserved under the rule that earlier entries are never changed. The owner should decide whether that is acceptable in the D1 commit.
3. **C-21** concerned an individual payroll matter. *(since generalised to a payroll-process finding.)*
4. **Short source key terms** were kept in the private `TRANSCRIPT_NOTES.md` where the wording itself is the point in dispute (the list is held privately). *(replaced by paraphrases.)*
5. **Re-identification.** In a small company, a job title plus a row locator still identifies one person to anyone holding the data. That is the price of traceability. Owner to confirm the trade-off.
6. **`POINT_IN_TIME`** was not given the availability bound; the instruction covered the window mode only. Owner may ask for it.
7. Not approved and still open: A-011, A-016, A-017 and A-018; every deferred decision, including D-22.

### How the corrections were checked

- **Pack membership** of every cited file: taken from the release-pack folder structure and `email.md` lines 16–24.
- **Sheet-row locators:** derived in this pass from text that was extracted from the original challenge files earlier on 16 Nov 2026. Each row number was checked against the sheet's declared dimensions. The original binary files were not re-opened in this pass.
- **Scan of the five files:** no employee first names or surnames (taken from the HR export), nickname variants, employee IDs, vendor names, invoice, voucher, requisition or incident identifiers, work email addresses, or rupee amounts. The scan pattern was validated against a private research file, where it found 90 matching lines.
- **Owner fields:** every owner-conclusion field is still blank, and every owner-rationale field still reads "Owner to write".

### Confirmations

- AI did not decide which source is true, wrote no owner conclusion or rationale, approved nothing, and introduced no new assumption. The GR entries are reclassified existing content.
- No ADR was reopened, and the approval date is unchanged.
- The private research, challenge data and release packs were not modified.
- No code, tests, infrastructure, commits or pushes.

---

## Mon 16 Nov 2026 — Initial commits

### Task given to AI

| # | Task (owner instruction) | AI output |
|---|---|---|
| 10 | After the owner's explicit approval, create the two initial commits on `main`: repository setup, then the Day 1 project documents.| Two local commits on `main` |

- **Push:** the push to GitHub is done by the owner, not by AI.
- **Confirmations:** no source truth decided; no owner conclusion, rationale or approval written; no deferred decision approved.

---

## Mon 16 Nov 2026 — Day 1 implementation foundation

### Task given to AI

| # | Task (owner instruction) | AI output |
|---|---|---|
| 11 | Audit the Day 1 requirements against the actual repository. Decide whether a frontend is needed. Implement the missing Day 1 foundation from the approved architecture: PostgreSQL 18 schema, D1 ingestion with provenance and release-time admission, tests, Docker Compose, CI, one-command setup. Use only D1 material; no source-truth or identity decisions; no Day 2–5 features; no commit or push without approval | Python package, migrations, D1 ingestion, API foundation, 44 tests, Docker Compose, CI workflow, documentation updates (uncommitted, for owner review) |

**Implemented**

- **Database:** PostgreSQL 18 with:
  - a bootstrap of the role set (`veloxa_owner`, `veloxa_migrator`, `veloxa_app`, `veloxa_ingest`) and the `btree_gist` and `vector` extensions;
  - migration `0001_foundation.sql`: the §6.2 schema boundaries, a tenant directory, `ledger.admission_batch`, `raw_index.raw_object`, `ledger.source_record` and `audit.event`;
  - triggers making history append-only, enforcing admission order, and allowing only a one-time seal;
  - a K-dependent `security_invoker` view;
  - FORCE RLS with restrictive tenant policies;
  - a restrictive policy hiding individual payroll rows from the app role;
  - least-privilege grants.
- **Ingestion:** the release schedule and clock, the source catalogue, a SHA-256 manifest, versioned parsers, and single-transaction admission and sealing with an audit event. Admission is decided before a pack's folder is opened.
- **Runtime:** startup self-checks (§9.3); an API with `/health`, `/v1/meta/clock` and `/v1/meta/releases`; a command line; a Dockerfile; Docker Compose; and a CI workflow with lint, tests and a fresh-clone Compose job.
- **Tests:** 44 in total. 43 use synthetic data only; one real-data test is local-only and checks counts and hashes, never values.

**Implementation choices for owner visibility (no approved decision changed)**

- `ARCHITECTURE.md` is unchanged.
- The tenant directory (C03) is a `directory` schema in the single hackathon cell. Migration bookkeeping lives in a `veloxa_meta` schema. Neither holds tenant business data.
- Sealing follows the §6.4 DDL (a `sealed` column), with a trigger that permits only the unsealed → sealed change, once.
- The backend stack (Python 3.12, FastAPI, psycopg 3) is recorded in `DECISIONS.md` Part C as pending owner confirmation.
- The frontend is not needed for Day 1. Its decision stays deferred, with no research or scaffolding done.
- New draft assumptions A-024 to A-026 (data classes, file-drop extraction time, templates). No new source conflicts were found. Ingestion makes no source-truth or identity decision.

### Where AI was wrong, and what changed

| # | AI error or weakness | Who caught it | Correction |
|---|---|---|---|
| E29 | The first bootstrap SQL used psql variable syntax, which does not work through the database driver | AI self-check before the first run | Replaced with a placeholder that the runner substitutes with a safely quoted identifier |
| E30 | The first spreadsheet parser used a read-only mode in which row numbering can start at the sheet's recorded dimensions rather than row 1, which would shift every locator | AI self-check before the first run | Normal mode, iterating from row 1 explicitly; locators verified by tests and by the real-data check |
| E31 | The first audit hash used the timestamp's text form as returned by the database, which depends on the session time zone, so verification could fail | AI self-check before the first run | The timestamp is normalised to UTC inside the hash; chain verification is tested |
| E32 | The first full test run hung: on Windows, `localhost` tried IPv6 first against an IPv4-only port, and connections had no timeout | AI, when the run exceeded its time limit | Default host `127.0.0.1` and a 10-second connect timeout; documented in the README |

### Verification actually run (16 Nov 2026)

- `ruff check .`: all checks passed.
- `pytest` against PostgreSQL 18.7 (pgvector 0.8.7, btree_gist 1.8): 44 passed, including the real-data Day 1 test. `pytest -m "not realdata"` (what CI runs): 43 passed.
- Real Day 1 pack:
  - 15 files and 112 records admitted as sealed batch 1;
  - stored hashes equal the released files;
  - 56 payroll rows classed `sensitive_personal` and hidden from the app role;
  - D2 refused before its folder was opened (only the Day 1 folder was read).
- `docker compose up --build`, with the real pack, then from a copy of exactly the files a fresh clone would contain:
  - healthy, D1 admitted, D2–D4 not released;
  - a restart reported `already_admitted`;
  - a missing `VELOXA_DATA_PATH` stopped Compose with the documented message.
- **CI fresh-clone job, run locally:**
  - the same commands with a synthetic pack: healthy, D1 admitted;
  - the workflow file passed `actionlint`;
  - it has **not** run on GitHub yet, because nothing has been pushed.

### Confirmations

- No source truth decided; no owner conclusion, rationale or approval written; no deferred decision approved (D-22 still unselected).
- No D2–D4 material opened. No raw challenge data in the repository, the tests or the logs.
- No commit or push.

---

## Mon 16 Nov 2026 — Day 1 foundation fixes after owner review

### Task given to AI

| # | Task (owner instruction) | AI output |
|---|---|---|
| 12 | Fix five review findings in the Day 1 foundation only, with focused regression tests: <br>1. `extracted_at` must be the actual extraction time; <br>2. the long-running API must not hold privileged credentials; <br>3. future query routes must not read payroll aggregates without the disclosure auditor; <br>4. stronger provenance (record hash bound to the manifest; hash the bytes that are parsed); <br>5. re-check admission state under the lock, and restrict `VELOXA_CLOCK` to tests. <br>Use a forward migration; no change to `ARCHITECTURE.md`; no commit or push | Migration `0002_hardening.sql`, code fixes, 16 new or updated tests, documentation updates (uncommitted) |

**What changed**

- **Times (finding 1).**
  - `extracted_at` is now the time each file's bytes were read.
  - `release_at`, `extracted_at`, `admitted_at` and `sealed_at` are distinct.
  - `0002` adds `admitted_at >= release_at` and `sealed_at >= admitted_at`, and the seal trigger rejects any record extracted outside `[release_at, admitted_at]`.
  - A-025 was revised as r2 (r1 kept as history); still a draft.
- **Least privilege (finding 2).**
  - Compose now runs a one-shot `init` service (administrator, migrator and ingest credentials, read-only data mount) and a long-running `api` service (application-role password only, no data mount) that starts after `init` succeeds.
  - The API refuses to start if any privileged credential is in its environment.
  - The all-in-one `bootstrap` command was replaced by `veloxa init` and `veloxa serve`.
- **Protected pay (finding 3).**
  - `0002` replaces the app-role policy with one that hides both `sensitive_personal` and `payroll_aggregate` rows.
  - Aggregates are therefore unreadable through any app-role query route until the disclosure auditor exists.
  - A startup self-check fails if that policy is missing or weakened.
- **Provenance (finding 4).**
  - `0002` adds a foreign key requiring each record's `raw_object` hash to equal the raw manifest's hash for the same file and batch.
  - Each file is read once; parsers receive those in-memory bytes and never re-open the file.
  - Unknown files are rejected before they are read.
- **Concurrency and clock (finding 5).**
  - After taking the per-tenant lock, admission re-reads the batch state. A concurrent run that already admitted the pack yields `already_admitted`; changed content is refused; order is re-checked.
  - `VELOXA_CLOCK` is honoured only with `VELOXA_TEST_MODE=1`; setting it otherwise is an error. It was removed from Compose and `.env.example`.

### Where AI was wrong, and what changed

| # | AI error or weakness | Who caught it | Correction |
|---|---|---|---|
| E33 | A-025 r1 set each record's extraction time to its pack's release time, conflating knowledge time with extraction | Owner review | Actual read time; four distinct times enforced in the database |
| E34 | One container ran migrations, admission and the API, so the long-running API held the administrator, migrator and ingest passwords | Owner review | Separate `init` and `api` services; the API refuses privileged credentials |
| E35 | Payroll aggregates were readable by the application role, so any future query route could release them without the disclosure auditor | Owner review | Restrictive policy covering aggregates; self-check guard |
| E36 | A record's hash was not tied to the manifest's hash by the database, and files were read twice (once to hash, once to parse) | Owner review | Composite foreign key; single read, with hashed bytes parsed in memory |
| E37 | The admission state was checked only before the lock, so concurrent retries could fail instead of being idempotent; `VELOXA_CLOCK` was honoured in normal runs | Owner review | Re-check under the lock (tested with two concurrent runs); clock restricted to test mode |
| E38 | The first version of `0002` failed: validating the new foreign key under FORCE RLS was filtered by the tenant policy, with no tenant context | AI, from the test run (the failed migration rolled back and was never applied anywhere) | Inside the migration's single transaction, FORCE RLS is suspended on the two tables only while the constraint is validated, then restored. The DDL locks block other sessions throughout |

### Verification actually run (16 Nov 2026)

- `ruff check .`: passed. `actionlint`: no findings.
- `pytest` against PostgreSQL 18.7: 60 passed, including the real-data Day 1 test. `pytest -m "not realdata"` (what CI runs): 59 passed.
- **Forward migration on the existing development database** (`0001` applied, D1 admitted):
  - `init` applied `0002`, reported D1 `already_admitted` and exited 0;
  - `api` started healthy;
  - the API container held only the application password and had no mounts;
  - the application role saw 50 of 112 D1 records, with no payroll lines and no payroll aggregates.
- **Fresh-clone copy with the real pack:** `0001` and `0002` applied; D1 admitted (112 records); D2–D4 not released.
- **CI path with a synthetic pack:** healthy; D1 admitted (11 records).
- **Missing `VELOXA_DATA_PATH`:** documented message.

### Remaining limitations

- The development database's 112 earlier records keep their r1 `extracted_at` values (append-only history). A fresh database has the corrected values.
- From a later pack's release time, `init` fails until that pack's parsers and catalogue rules are added, so the API does not start (fail closed). This is documented in the README.
- The CI workflow has still not run on GitHub.

### Confirmations

- `ARCHITECTURE.md` unchanged; no ADR reopened; no deferred decision approved (D-22 unselected); A-025 r2 remains a draft.
- No D2–D4 material opened. No raw challenge data in the repository, the tests or the logs.
- No commit or push.

---

## Mon 16 Nov 2026 — Owner approvals applied; availability on failed admission

### Task given to AI

| # | Task (owner instruction) | AI output |
|---|---|---|
| 13 | Record five owner approvals: <br>• A-024 (D1 data classification, strict fail-closed); <br>• A-025 r2 (actual extraction timestamps); <br>• A-026 (templates are not evidence); <br>• the backend stack (Python 3.12, FastAPI, psycopg 3, plain-SQL migrations); <br>• API availability: if a later pack fails ingestion, keep serving the latest sealed batch, and never expose unsealed data. <br>No architecture change, no frontend decision, no commit or push | Approvals recorded; availability behaviour implemented with tests (uncommitted) |

**What changed**

- **`ASSUMPTIONS.md`:** A-024, A-025 r2 and A-026 marked as approved by the owner, with a revision-log row. A-024's status states the strict fail-closed handling, which the code already enforces: a file without an explicit catalogue rule is rejected before it is read.
- **`DECISIONS.md`:**
  - Part A: the owner's statements, verbatim.
  - Part B: new DEC-23 (backend stack) and DEC-24 (availability). Owner rationale is left for the owner.
  - Part C: the pending backend row is closed.
  - No frontend decision.
- **Code:**
  - `veloxa init` no longer fails when a released pack fails admission. It logs the failure, appends an `admission.failed` audit event (pack and reason code only), and still exits 0, so the API starts.
  - `init` still fails if the data folder is missing, a migration fails, or the ingest self-check fails.
  - `veloxa ingest` run on its own still exits non-zero on a failed admission.
  - The API reports each pack as `sealed`, `pending_admission` or `not_released`; `/health` is `degraded` with a `pending_admission` list. All counts come from sealed batches only.
  - The API accepts an injected clock as a test-only code parameter, not configuration.
- **Tests (5 new):**
  - the API keeps serving the latest sealed batch with a pending later pack;
  - an end-to-end `init` with a failing later pack succeeds, audits the failure, and leaves the earlier sealed batch unchanged;
  - `ingest` alone still reports the failure;
  - `init` fails when the data folder is missing;
  - unsealed rows are invisible even inside the writing transaction.
- **`README.md`:** updated for the new behaviour.
- **`ARCHITECTURE.md`:** unchanged. DEC-24 is consistent with §8.2 (only sealed batches are visible) and §22.1 (a failed batch does not seal).

### Verification actually run (16 Nov 2026)

- `ruff check .`: passed.
- `pytest` against PostgreSQL 18.7: 65 passed, including the real-data Day 1 test.
- `docker compose up --build --wait` on the development database:
  - `init` exited 0 with D1 already admitted;
  - the API was healthy (`pending_admission` empty);
  - D1 was sealed with 112 records, and D2–D4 were `not_released`.
- The failed-later-pack path was verified by the end-to-end `init` test rather than in Docker: today's real clock cannot release D2, and the clock may move forward only in tests.

### Confirmations

- No source truth decided. Only the owner's stated approvals were recorded, and no owner rationale was written.
- No frontend decision. `ARCHITECTURE.md` unchanged.
- No D2–D4 material opened. No commit or push.

---

## Mon 16 Nov 2026 — Sealed-baseline rule; Day 1 milestone commits

### Task given to AI

| # | Task (owner instruction) | AI output |
|---|---|---|
| 14 | Require at least one successfully sealed baseline batch before the API may start on a fresh database, with regression tests for both scenarios. Then run the full suite and lint, review the diff, repeat the public-safety scan, create the three logical commits, push them to `main` and verify the remote hashes and the GitHub Actions result. No force-push, no history rewrite, no change to `ARCHITECTURE.md` | Baseline rule and tests; the commits and push were made **by AI on the owner's explicit authorization** (16 Nov 2026) |

**What changed**

- `veloxa init` now checks the sealed watermark after admission. With no sealed baseline batch (a fresh database whose first pack failed, or no pack released yet), it exits with code 3 and the API does not start. With D1 sealed, a later pack's failure stays non-fatal and the API serves D1 in a degraded state (DEC-24, refined).
- **Two new tests:**
  - fresh database plus a D1 failure: `init` exits 3, nothing is admitted, and the failure is audited;
  - existing database with D1 sealed plus a D2 failure: `init` succeeds, the API reports `degraded` with D2 pending, the watermark stays 1, and reads beyond K = 1 are rejected.
- **Earlier note on pushes:** the task-10 entry records that pushes are done by the owner. These milestone commits are pushed by AI because the owner explicitly authorized it for this step. Commits remain authored solely by the owner's account, with no AI co-author trailer.

### Confirmations

- No source truth decided; no owner rationale written; no frontend decision; `ARCHITECTURE.md` unchanged.
- No D2–D4 material opened. No force-push and no history rewrite.

---

## Mon 16 Nov 2026 — Day 2 development milestone (work that does not depend on the Day 2 pack)

### Task given to AI

| # | Task (owner instruction) | AI output |
|---|---|---|
| 15 | Start the Day 2 development milestone from the approved architecture and roadmap, in order: D2 source analysis; parsers, classification, provenance and validation; the visibility abstraction, temporal predicate builder and leakage canaries; governed metrics and the declared query tools; `/api/ask` v0 with an offline renderer, evidence citations and a deterministic validator; risk rules v0 and the Today briefing contract; the T6a response; the first 12 gold questions (three refusals) and regression tests. Keep the K/R/V semantics and the D2 release boundary; no model-authored SQL; numbers only from governed metrics; protect HR, payroll and personal data; no source-truth or identity decisions; no frontend; no change to `ARCHITECTURE.md`; small verified milestones, committed and pushed regularly | Four milestones so far, each tested and pushed by AI under the owner's standing authorization for verified milestones: export validation contracts; temporal model and leakage canaries; projections, governed metrics and query tools; Ask Veloxa v0 |

**Held until the Day 2 release (17 Nov 2026, 10:00 IST).** Everything that depends on Day 2 material waits for the release: catalogue rules and contracts for the new exports, the hiring tool, Day 2 metrics, the T6a response, gold questions over Day 2 facts, and Day 2 entries in `ASSUMPTIONS.md` and `TRANSCRIPT_NOTES.md`. The Day 2 pack was inspected privately for planning (step 1); those notes stay in the private register, and nothing from the pack is admitted, queryable or published before its release.

**What was built**

- **Validation contracts** for row exports (required columns, uniqueness, ISO dates, integers, arithmetic identities). A violation refuses the whole pack (fail closed) and is audited.
- **Temporal model** (§8): read basis and the four query modes; a date means the end of that day in IST; a predicate builder that uses identifiers and bind parameters only and refuses a table lacking the time column a mode needs. A leakage canary harness checks every surface at every batch boundary.
- **Projections and governed metrics** (§6.2, §11): rebuildable read models per sealed batch, written by a separate projection role; versioned metric definitions compiled to parameterised SQL; protected pay excluded in SQL and by RLS; the payroll total fails closed until the disclosure auditor exists.
- **Declared query tools** (§13.1–13.2): person, headcount, finance totals, payroll total (withheld), board statements, timeline, conflicts (reports that no owner-reviewed conflict exists) and text search. Emails are usable only by their parties. Identities are never guessed.
- **Ask Veloxa v0** (`POST /v1/ask`, §13, §19.4), in facts-only mode:
  - refusal catalogue (§13.6) checked before any data is read; each refusal has a policy ID, reason and alternative;
  - temporal parse from explicit cue wording or parameters, always echoed;
  - bounded deterministic planner over the declared tools, with argument validation;
  - evidence pack, then claims rendered verbatim from facts or excerpts;
  - deterministic verifier: citations exist and are visible at K, numbers and names are contained in each claim's own evidence, a policy scan, and kind downgrade only;
  - grades A–D and status precedence;
  - every answer, its citations and an audit event recorded in one transaction (migration `0004`).

### Points for owner attention (not decided by AI)

- **Facts-only planner and composer.** v0 implements the architecture's degraded path (C16: deterministic, status at most `qualified`) behind the same stage contracts. The model planner and composer come later; no approved decision is reopened.
- **Headcount is a declared operation.** It was added beside the roadmap's tools so that headcount questions use the governed metric. Hiring is added with Day 2.
- **The refusal patterns are a policy judgement.** They err towards refusing: for example, any prediction of attrition, even at team level, is refused. The owner may want to review the wording in `src/veloxa/ask/policy.py`.
- **Principal.** With no identity provider, the API answers as the one person whose HR title equals `VELOXA_PRINCIPAL_TITLE` (default `CEO`). If that does not resolve to exactly one person, the request is refused as `not_authorised`.
- **Recording.** `runtime.answer` stores the question text, under tenant RLS and append-only. Audit details hold structure and counts only. The application role can now read the audit chain's `seq` and `hash` columns (needed to extend the chain), but not event details.

### Where AI was wrong, and what changed

| # | AI error or weakness | Who caught it | Correction |
|---|---|---|---|
| E39 | The first temporal check treated today's date as "in the future" and refused it | AI, from the test run | A date is refused only if that day has not begun; instants and dates are compared separately |
| E40 | The raw manifest table carries a data class but had no protected-pay policy, so the app role could see manifest entries for payroll files | AI, through a generalised self-check covering every table with a data class | Policy added in `0003` before that migration was committed or applied to a persistent database |
| E41 | **Process error.** For the metrics milestone, the public-safety scan reported "review needed". AI read only the last lines of the output, and the chained commit-and-push command did not stop on the finding, so the commit was pushed before the finding was reviewed | AI, immediately after the push | The finding was an invented year-2099 amount in a synthetic test fixture, confirmed absent from all challenge material, so nothing sensitive was published. The scan now exits non-zero on any finding and the commit runs only if the scan passes; the synthetic fixture is allowlisted explicitly |
| E42 | The first refusal patterns refused legitimate work questions (a vendor payment ranking; a factual question about past exits) | AI, from a pattern check before the tests | Pay refusals now exclude vendor and ledger contexts; attrition is refused only with predictive wording, while past facts remain answerable |
| E43 | A shell edit turned regex escapes in the refusal patterns into control characters | AI, on reading the file back | Lines rewritten from a file; a check for control characters returns none |
| E44 | The "who is <name>" fallback was case-sensitive, so "Who is …" with an unknown name ran a text search instead of reporting the name as unresolved | AI, from the test run | Case-insensitive cue; a test covers it |

### Verification actually run (16 Nov 2026)

- `ruff check .`: passed.
- `pytest` against PostgreSQL 18.7: **188 passed**, including the three real-data tests. `pytest -m "not realdata"` (what CI runs): 185 passed.
- **Real Day 1 pack (structure only):**
  - the headcount answer equals the governed metric;
  - payroll-total and attrition questions are refused;
  - a basis before admission abstains;
  - every cited record is from batch 1, with no protected class;
  - the configured principal sees no email it was not party to.
- `docker compose up -d --build` on the development database:
  - `init` applied `0004` and found D1 already admitted;
  - health `ok`;
  - `/v1/ask` returned qualified answers with citations, and refusals with policy IDs.
- GitHub Actions passed for the three earlier milestone commits.

### Confirmations

- No source truth decided; no identity resolved; no owner conclusion or rationale written.
- No frontend decision. `ARCHITECTURE.md` unchanged. No Day 2–4 material admitted, queried or published.
- No force-push and no history rewrite. Commits authored by the owner's account, without an AI co-author trailer.

---

## Mon 16 Nov 2026 — Unweighted risk comparison, interim briefing, D-15 gate, requester authentication

### Task given to AI

| # | Task (owner instruction) | AI output |
|---|---|---|
| 16 | Implement option 2 for M5: a risk comparison with no invented weights, scores, thresholds, tiers or tie-breaks. Dominance only when a risk is at least as bad on every comparable approved measure and worse on one. Never force a comparison over missing, uncertain or incompatible measures. Build an interim `GET /v1/briefing` (unranked watchlist, `top_three` null, explicit ranking mode and D-15 status). Make D-15 an enforceable, audited, versioned gate, and prepare a blank owner decision sheet. Fix `/v1/ask` assuming the CEO by default: fail closed without a trusted authenticated principal, with any development fixture explicit and isolated. Keep release, evidence, privacy and tenant controls; no change to `ARCHITECTURE.md`; no owner value judgements | Risk engine v0 (`src/veloxa/risk/`), migration `0005`, `GET /v1/briefing` and `GET /v1/briefing/{snapshot_id}`, `veloxa policy record-d15`, `src/veloxa/auth.py`, the decision sheet `docs/D-15_owner_decision_sheet.md`, 38 new tests. Committed and pushed by AI under the owner's standing authorization for verified milestones |

**What was built**

- **Comparison** (`compare.py`): interval dominance over C1–C5 (§16.2–§16.3) and nothing else.
  - A pair is compared only if every criterion that applies to either risk is measured for both, with the identical measure definition, version and unit.
  - "Missing", "uncertain", or "not applicable" on one side only, make a pair *not comparable*. They are never read as zero.
  - The other outcomes are *dominates*, *trade-off* (needs the D-15 weights) and *indistinguishable* (no tie-break).
  - The relation is transitive and is never completed into a ranking.
- **Detector** (`detectors.py`): one detector, for C4.
  - It compares a figure reported in a formal document with a governed metric, only for pairings the owner has approved in `config/figure_mappings.toml`. None are approved, so on real data it checks nothing.
  - A signal says the sources differ, never which is right.
- **Briefing** (`briefing.py`): an unranked watchlist in `risk_id` order, labelled as carrying no priority.
  - Each entry carries evidence, time context, measures, unknowns and its comparison.
  - The briefing also lists signals needing data. `top_three` is null.
  - Every briefing is an append-only snapshot recording the read basis, engine version, D-15 status, version and hash, and an input hash. An identical request reuses its snapshot; a later policy creates a new one.
- **D-15 gate** (`governance.py`, migration `0005`):
  - Decisions live in `policy.ranking_decision`. The privileged command line writes them; the application role can only read them.
  - Decisions are approved-only, consecutive-versioned, append-only, hashed and audited.
  - `weighted_ranking` refuses while D-15 is pending, and refuses even after approval, because no weighted implementation exists.
  - The database rejects a snapshot recorded as weighted without an approved policy.
- **Authentication** (`auth.py`):
  - by default no authenticator is configured, and `/v1/ask` and `/v1/briefing` return 401;
  - the development fixture needs three explicit settings with no defaults: the flag, a secret of 32 or more characters, and an employee ID;
  - the API refuses to start if the fixture is half-configured;
  - the principal comes from server configuration, never from the request;
  - `VELOXA_PRINCIPAL_TITLE` was removed.

### Points for owner attention (not decided by AI)

- **Directions of the natural scales** (higher exposure is worse; fewer days to a commitment is worse) are implemented as read from the §16.2 wording. They are listed for confirmation in the decision sheet (item A).
- **The C4 measure** `C4.absolute_discrepancy@1` (absolute difference in the metric's unit) is an implementation reading of §16.2. It is listed for confirmation (item B).
- **The C4 detector's criterion declarations:**
  - C1, C3 and C5 are "not applicable" to a reporting discrepancy;
  - C2 is "missing" when the paired figures are currency, so such risks stay unquantified and are not compared.
- **The real Day 1 briefing is an empty watchlist.** That is honest, not a defect hidden by filler: no figure pairing is approved, and the other criteria have no detector yet.
- **Production authentication remains to be built:** identity-provider integration, principal resolution through the tenant directory, ReBAC/ABAC, revocation. The fixture is a single static token for local development only.

### Where AI was wrong, and what changed

| # | AI error or weakness | Who caught it | Correction |
|---|---|---|---|
| E45 | The Ask Veloxa v0 milestone answered as the CEO by default (a configured title, default `CEO`). Any caller who could reach the API received the CEO's view | Owner review | Requester endpoints now need an authenticated principal and fail closed with 401. The development fixture is explicit, needs a secret, and acts as a configured employee ID, never a title. Tests prove that unauthenticated or spoofed requests get nothing |
| E46 | Shell here-documents mangled escape sequences in two scripted edits; one produced a syntactically broken test helper | AI, from the immediate syntax error | Edits are written as script files instead; the helper was rewritten and verified by parsing a generated document |

### Verification actually run (16 Nov 2026)

- `ruff check .`: passed.
- `pytest` against PostgreSQL 18.7: **226 passed**, including the four real-data tests. `pytest -m "not realdata"` (what CI runs): 222 passed.
- **Real Day 1 pack:** the briefing returns D-15 pending, `top_three` null, an empty watchlist, and the reasons in `signals_needing_data`. No source truth was changed.
- `docker compose up -d --build --wait` on the development database:
  - `init` applied `0005`;
  - health reported `auth: none`;
  - `/v1/ask` and `/v1/briefing` returned 401, including with a spoofed title header.
- Then, with the fixture enabled explicitly:
  - a wrong token got 401;
  - the briefing and ask worked for the configured principal.
- The stack was then restored to the default (`auth: none`).

### Confirmations

- No weights, anchors, thresholds, tiers or tie-breaks chosen; no figure pairing approved; D-15 owner fields left blank; no weighted ranking started.
- No source truth, classification, identity or historical evidence decision changed. `ARCHITECTURE.md` unchanged.
- No Day 2–4 material admitted, queried or published. No force-push and no history rewrite.
