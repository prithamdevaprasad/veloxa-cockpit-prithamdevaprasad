# Durable workflows (C26): operations

**Design source.** `ARCHITECTURE.md` §17: DBOS is the approved initial runtime, and Temporal is the scale option.

**Code.**
- `src/veloxa/workflows/`: `runner.py` holds the `ActivityRunner` contract and its DBOS adapter; `catalogue.py` holds the workflows.

**Status.** Integrated and tested locally against real DBOS 3.5.0 and PostgreSQL 18. **This is not a production deployment.** Missing:
- high availability;
- multi-executor operation;
- monitoring and alerting on workflow state;
- the Conductor service, which is not used.

## Configuration

| Setting | Meaning |
|---|---|
| `VELOXA_WORKFLOW_DATABASE_URL` | The DBOS **system database**: a database **separate** from the Veloxa application database. DBOS creates and migrates its `dbos` schema there; it never touches application tables. Use a dedicated role that owns only that database. Required; there is no default |
| Application database credentials | The usual `VELOXA_DB_*` and role passwords. Steps resolve them at run time and **never** receive them as workflow input |
| `application_version` | `veloxa-workflows/1`, recorded on every run. Bump it when workflow code changes incompatibly; runs in flight under the old version are not replayed by new code |
| `executor_id` | `veloxa-local`. On launch, DBOS recovers this executor's interrupted runs |

## Catalogue

Names are pinned and versioned, and a compatibility test guards them.

| Workflow | Steps | Idempotency |
|---|---|---|
| `veloxa.pack_pipeline.v1` (tenant, pack) | `admit` → `project` → `identity` → `veloxa.workflow.audit` | `admit` never re-admits a sealed pack, and still enforces the release time and the admission authorisation register. `project` and `identity` are idempotent per batch |
| `veloxa.agent_turn.v1` (tenant, request id, interviewer id) | `turn` | The message is in `runtime.agent_request` (RLS, own interviewer). The turn runs through the shared agent runtime (KB ∩ AZ ∩ POL), whose (session, key) guard makes a recovered step return the stored turn |

**Workflow ID.** `<name>:<idempotency key>`:
- starting the same key again returns the same run;
- reusing a key with different arguments is refused.

**Retries.** Allowed only for transient database errors (`psycopg.OperationalError`, 3 attempts with backoff). Refused admission, authorization denial and validation failures are permanent.

## What DBOS persists (privacy)

DBOS stores workflow inputs, step outputs and errors outside Veloxa's RLS. Therefore:
- **Inputs:** opaque IDs only (tenant, pack, request, interviewer, an argument digest).
- **Outputs:** IDs, counts and statuses only.
- **Never stored:** messages, evidence excerpts, payroll values, tokens or connection strings. A test scans the DBOS tables for these.

**Retention: proposed, NOT approved (owner decision OD-A).**

**Verified against DBOS 3.5.0:** there is no automatic garbage collection. Records stay until `delete_workflows` is called. Until the owner approves a period, keep the system database under the same access controls and backups as the application database.

`veloxa workflow-retention` (`workflows/retention.py`):
- **Default (dry run):** reports what the proposed periods would remove (successful runs after 90 days; failed runs after 180 days). It prints IDs and counts only.
- **`--apply`:** deletes, but only if all three of these are set; otherwise it is refused with exit 2:
  - `VELOXA_WORKFLOW_RETENTION_DECISION` (the owner decision record);
  - `VELOXA_WORKFLOW_RETENTION_SUCCESS_DAYS`;
  - `VELOXA_WORKFLOW_RETENTION_ERROR_DAYS`.
- **Never removed:**
  - pending, enqueued or other unfinished runs (recovery needs them);
  - any ID in `VELOXA_WORKFLOW_LEGAL_HOLD` (comma-separated).
- **Audit:** Veloxa's audit chain keeps a `workflow.completed` event per run. Removing an old run loses checkpoint replay for that run, not the evidence that it ran.

## Commands

```
veloxa workflow-pack --pack D1 [--key K]   # needs ingest and projection credentials and VELOXA_WORKFLOW_DATABASE_URL
veloxa workflow-status <workflow_id>       # status only
veloxa workflow-retention [--apply]         # dry run by default; --apply needs an approved owner decision
```

## Tests

`tests/test_workflows_dbos.py` runs on real DBOS and PostgreSQL, using disposable databases. It covers:
- success;
- transient failure with retry;
- permanent failure;
- recovery after a real process kill, with the checkpointed step proven not re-executed;
- cancellation;
- idempotency and conflicting keys;
- invalid input;
- tenant and interviewer isolation;
- audit;
- versioning;
- pinned names;
- the sensitive-payload scan;
- concurrency;
- shutdown and restart.
