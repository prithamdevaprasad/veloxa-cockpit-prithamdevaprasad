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
| 10 | After the owner's explicit approval, create the two initial commits on `main`: repository setup, then the Day 1 project documents. The owner is the sole commit author, using the repository-local identity, with no AI co-author trailer | Two local commits on `main` |

- **Push:** the push to GitHub is done by the owner, not by AI.
- **Confirmations:** no source truth decided; no owner conclusion, rationale or approval written; no deferred decision approved.
