# Agent registry specification

**Storage.** `runtime.agent_config`: append-only, one row per `(tenant, agent_id, config_version)`. The latest version is in force. Versions are consecutive, and the database enforces this with a trigger.

| Field | Meaning |
|---|---|
| `agent_id` | Stable identifier. `agent-<cat>-NN` for the 14 slots; `syn-…` reserved for synthetic test agents |
| `category` | `engineering`, `finance`, `people_ops` or `marketing` |
| `represented_person` | `person:<id>`, or NULL (unassigned) |
| `capabilities`, `tools` | Must lie within the category allowlist and the declared tool registry |
| `knowledge_scopes` | `company_records`, `own_participation`, `role_plausible` |
| `policy_refs` | Must include **every** refusal policy; policies cannot be configured away |
| `memory_config` | Exactly the five approved tiers, with their retention |
| `runtime_version` | `agent-runtime/1` |
| `activation` | `blocked`, `synthetic_test` or `active` |
| `approved_by`, `decision_record` | Required for `active` |
| `created_at`, `created_by` | Provenance. Audit event `agent.config_published` |

## Rules

Each rule is enforced in code (`registry.validate`). The rules marked ⛁ are also enforced in the database.

- A configuration grants nothing. The PDP, AZ, KB and POL still apply at every execution.
- The capabilities each category may be configured with:

  | Category | Capabilities |
  |---|---|
  | engineering | delivery, conflicts, briefing, evidence_search, coverage |
  | finance | finance_ledger, delivery, conflicts, briefing, evidence_search |
  | people_ops | people_directory, briefing, evidence_search |
  | marketing | delivery, conflicts, briefing, evidence_search |

- `synthetic_test` only applies in the `syn-` namespace, and only for a synthetic or no person ⛁. A `syn-` agent is never `active` ⛁.
- `active` requires a represented person, an approver and a decision record ⛁. No agent is active today: the 14-person mapping is an open owner decision (H-03).
- Only the configuration role (`veloxa_projection`, via `veloxa agents-seed` or `registry.publish`) can write. The API role can only read ⛁.

## The 14 slots

`veloxa agents-seed` publishes the proposed distribution:
- engineering: `agent-eng-01` to `agent-eng-05`;
- finance: `agent-fin-01` to `agent-fin-03`;
- people_ops: `agent-ppl-01` to `agent-ppl-03`;
- marketing: `agent-mkt-01` to `agent-mkt-03`.

Every slot is unassigned and `blocked`. No person, persona, manager or identity is invented.
