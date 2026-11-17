# Shared agent runtime (C23): architecture and authorization status

Design source: `ARCHITECTURE.md` §5.2 (C23/C24), §10.6, §14 and §18. Code: `src/veloxa/agents/`. Migration: `0013_agent_runtime.sql`.

## One runtime

All 14 agent slots run on **one** engine: `agents.runtime.AgentRuntime`.
- **Agents are data.** Each is a versioned configuration in `runtime.agent_config` (see `AGENT_REGISTRY.md`).
- **No agent-specific code.** Behaviour comes only from the configuration: its category, capabilities and tools. There are no agent-specific programs.

## Lifecycle of one turn

`POST /v1/sessions/{id}/messages` runs `execute`:

1. **Validate.** Check the request, then load the interviewer's own session. RLS hides other interviewers' sessions. The configuration version is pinned when the session opens.
2. **Idempotency.**
   - A repeated `(session, idempotency_key)` with the same request returns the stored turn (`replayed: true`).
   - The same key with a different request is refused (HTTP 409).
3. **Activation.** The latest configuration must still be `synthetic_test` or `active`. A later `blocked` version stops the agent at once.
4. **Refusal pre-screen.** It uses the Ask catalogue: every policy applies to every agent, and none can be configured away. HR names at the read basis are used only to recognise individual targeting.
5. **Tools.** Each call goes through these checks in order:
   - the agent-tool PDP;
   - input schema;
   - execution under the **interviewer's** principal, with a timeout;
   - output schema.

   Failures (`error`, `timeout`, `malformed_output`, `invalid_input`) are reported per tool. They never fail the whole turn.
6. **Evidence D = KB ∩ AZ ∩ POL** (§14.2):
   - **POL:** the interview data-class policy.
   - **AZ:** the interviewer's **current** authorization.
   - **KB:** the represented person's **historical** eligibility (C32).
   - Facts whose inputs are not all in D are dropped, and the drop is counted.
7. **Composition** through `LLMClient` (`agents.model`):
   - **Default:** the deterministic facts-only composer.
   - **No provider:** `UnavailableLLM` reports `unavailable`.
   - **Invalid output:** one retry, then `failed`.
8. **Verification.** A claim survives only if it cites permitted facts and states exactly one of them. The runtime then applies the §14.2 hedge:
   - `possible` becomes "I'd have had access to this record (not that I read it)";
   - `role_plausible` gets a role hedge.
9. **One transaction** writes:
   - working memory;
   - episodic memory, for `participant` evidence;
   - reflective memory, only for a model reflection, labelled `inference`;
   - the execution record;
   - the audit event.

Every turn carries the AI-representation label, its read basis, and the runtime and configuration versions. Agents hold no credentials and have no action tools (§14.3).

## Status of each dependency

| Layer | Component | Status |
|---|---|---|
| Interviewer identity | C02 | **Interface + dev fixture only.** The API resolves the authenticated principal. No identity provider is selected or deployed |
| AZ: current ReBAC | C20 | **Implemented, synthetic-tested, integrated.** The relation is `read` on `evidence_source:<system>`, with PostgreSQL tuples. SpiceDB is not deployed |
| AZ: current ABAC | C21 | **Implemented, synthetic-tested, integrated.** Policy `abac/1`, purpose `interview`. OPA is not deployed |
| KB: historical eligibility | C32 | **Integrated (`C32KnowledgeBoundary`).** Real packs carry no ACL history, so real KB is empty and agents abstain. Unassigned agents have an empty KB |
| Agent-tool policy | C22 | **Hackathon profile:** in-process `agent-tools/1` behind the PDP interface (§25). **Cedar is not deployed.** `CedarAgentToolPDP` denies everything |
| Model | C27 | **Interface only.** No provider. The facts-only composer is not a model |
| Protected aggregates | C19 | **Not wired for agents.** There is no payroll tool, and protected classes are excluded from POL and from memory (enforced by a database check) |
| Durable execution | C26 | Not used. A turn is one database transaction |

**Test-only adapters**, never wired by default:
- `tests/agent_fixtures.SyntheticTestKnowledgeBoundary` answers only for `syn-` agents;
- `agents.model.ScriptedTestModel`.

**No production deployment exists for any external system listed above.**
