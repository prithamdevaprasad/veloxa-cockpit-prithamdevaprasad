# Five-tier agent memory: implementation

Specification: `ARCHITECTURE.md` §14.5. Code: `src/veloxa/agents/memory.py`. Tables: `runtime.agent_memory` and `runtime.agent_memory_event`, with the view `runtime.agent_memory_current`.

| Tier | Approved content | Label | Keyed by | Written by |
|---|---|---|---|---|
| working | current session turns, one K | `execution_context` | agent, interviewer, session | runtime, every turn; closed with the session |
| episodic | records the person was party to | `source_evidence` | agent | runtime, for `participant` evidence in D only |
| semantic | consolidated facts with validity | `derived_observation` / `approved_decision` | agent | deterministic consolidation from current episodes only |
| interview | summaries of prior sessions | `derived_observation` | agent, interviewer, session | deterministic summary of cited evidence |
| reflective | model-generated summaries | `inference` (always) | agent, interviewer | the model's optional reflection; never evidence |

The database enforces these rules with checks:
- each tier's required keys and label;
- `inference` is allowed only in the reflective tier;
- episodic and semantic items need sources, or provenance episodes;
- the protected pay classes never enter memory.

## Isolation

Tenant, agent and interviewer are **separate** columns, enforced by RLS policies `memory_scope` and `memory_event_scope`. The application role reads only rows of the agent and interviewer set in its transaction.
- **No partial scope.** An unset scope raises, so a read never succeeds without a full scope.
- **Per interviewer.** Interviewer V never sees interviewer U's working, interview or reflective memory.
- **Revoked knowledge.** Agent-level tiers (episodic, semantic) are re-checked against the interviewer's **current** authorization at recall. Withheld items are counted, never returned. Without an authorizer, recall fails closed.

## Time and history

- **Lineage.** Items carry `batch_seq` (T5: max of the session's K and its inputs) and are hidden at an earlier K.
- **Never mutated.** Memory is never updated or deleted. Expiry, supersession (`superseded_by`), invalidation (with the model decision record when model-assisted), retention expiry and session close are append-only events.
- **Replay.** `recall(known_at=…)` replays the state at an instant.
- **Retention.** Set per tier in the agent configuration; working memory lasts for the session.
- **Inheritance.** A consolidated item inherits the union of its inputs' sources and classes, and the weakest knowledge basis.
