# Retention and erasure: policy matrix (every row OPEN; deletion and erasure DISABLED)

**Rules:**
- No universal retention period is set.
- Append-only evidence is never silently deleted.
- Every period below is an **owner decision**. A "starting position" is a proposal, not a policy.
- No code deletes anything on a schedule.
- The only deletion path is `veloxa workflow-retention --apply`. It refuses unless an owner decision record and both periods are configured.

| Category | Where | Current behaviour | Proposed starting position (NOT approved) |
|---|---|---|---|
| Original uploaded files (release packs) | Outside the repository (`VELOXA_DATA_PATH`) | Never modified or deleted by Veloxa | Keep for the engagement; the owner sets the end date |
| Parsed records / raw index | `raw_index` | Append-only per sealed batch | Same as the originals (rebuildable from them) |
| Normalised evidence | `knowledge`, `projection` | Append-only (time axes R/V/E/I/K); projections are rebuildable | No time-based deletion; erasure only by the D-06a design |
| Evidence ledger | `ledger` | Append-only, hash-chained; mutation triggers reject changes | Never deleted; tombstones only (D-06a) |
| Identity mappings | `directory`, `judgement` | Append-only, guarded decisions | Tied to the engagement and the erasure design |
| Agent messages and memory | `runtime.agent_*` | Append-only; memory expires by events; each interviewer sees only their own sessions (RLS) | Per data class, set by the owner |
| DBOS workflow records | DBOS system database | Never removed automatically (DBOS 3.5.0) | 90 days successful / 180 days failed; approval-gated |
| Model call records | `runtime.llm_call` | Append-only; never stores prompt or response text | Same as audit records |
| Audit records | `audit` | Append-only, hash-chained | Never shorter than the longest retention of the data it audits |
| Backups | Operator-chosen location | `veloxa backup` writes verified dumps; nothing expires | Per RPO/RTO (K-17). Erasure must reach backups (see PITR_WAL_DESIGN.md) |

**Erasure.**
- **Approved direction (D-06a):** crypto-shredding plus tombstones that keep the ledger verifiable.
- **Not built:** there is no erasure operation in the code, by design, until the design and legal review close D-06a.
