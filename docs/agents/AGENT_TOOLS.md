# Agent tool registry specification

Code: `src/veloxa/agents/tools.py`. Each tool is a `ToolSpec` with these fields:

| Field | Meaning |
|---|---|
| `tool_id` | Stable identifier |
| `description` | What the tool does |
| `input_model` | Pydantic input schema; extra fields are rejected |
| Output | The shared `ToolOutput` schema: facts with their input record IDs, evidence, gaps |
| `capability` | The capability the tool needs |
| `data_classes` | Classes the tool may return. Never the protected pay classes |
| `purpose` | `interview` |
| `timeout_s` | Late output is discarded |
| `audited` | One `agent.tool_call` audit event per decision |
| `availability` | Whether the tool's source is admitted |
| `read_only` | Always true. No action tool exists |

**How tools reach data.** Tools call existing governed services: the declared query operations, the conflict register and the Today view. They run under the interviewer's principal. Agents never query PostgreSQL and never author SQL.

| Tool | Capability | Backed by |
|---|---|---|
| `people.headcount` | people_directory | governed metric `headcount` |
| `people.profile` | people_directory | HR snapshot at the read basis |
| `finance.ledger_spend` | finance_ledger | governed metric `ledger_spend` (payroll excluded) |
| `delivery.timeline` | delivery | dated record events |
| `delivery.board_statements` | delivery | reported board figures |
| `conflicts.register` | conflicts | `ask.conflicts.register` (no value chosen) |
| `briefing.today` | briefing | the Today view (unranked while D-15 is pending) |
| `evidence.search` | evidence_search | lexical search over admitted documents |
| `coverage.oncall` | coverage | **Declared, blocked:** its source is not admitted. Refused before it runs |

**Payroll.** There is deliberately no payroll tool: protected aggregates go only through C19.

## Decision order

Each call is decided in this order:
1. the PDP: tenant, interviewer, purpose, activation, declared, configured, capability, category, read-only, availability;
2. input schema;
3. execution;
4. timeout;
5. output schema.
