"""The shared employee-agent runtime (ARCHITECTURE.md §5.2 C23, §14; one runtime, agents are data).

Lifecycle of one turn (`execute`), identical for every agent:
 1. validate the request; load the interviewer's own session (RLS) and the PINNED configuration version;
 2. idempotency: the same (session, key) with the same request returns the stored turn; a different request with a
    used key is refused;
 3. the agent must still be activated (a later `blocked` configuration stops it immediately);
 4. refusal pre-screen (the same catalogue as Ask; policies cannot be configured away);
 5. each tool call: agent-tool PDP (deny overrides) -> input schema -> run under the INTERVIEWER's principal with a
    timeout -> output schema;
 6. evidence D = KB ∩ AZ ∩ POL (represented person's historical eligibility, interviewer's current authorization,
    interview data-class policy); facts whose inputs are not all in D are dropped;
 7. composition through `LLMClient` (facts only, a test model, or unavailable);
 8. verification: a claim survives only if it cites permitted facts and states exactly one of them; the runtime
    applies the §14.2 hedge for `possible` and `role_plausible` knowledge (access is not reading);
 9. memory (working; episodic for participant evidence; reflective for model reflections, labelled inference),
    the execution record and the audit event are written in ONE transaction.
Every turn is labelled as an AI representation, carries its read basis and runtime/config versions, and never acts.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import psycopg
from pydantic import ValidationError

from .. import audit, ledger
from .. import tools as vt
from ..ask import policy as ask_policy
from ..db import tenant_tx
from ..temporal import Mode, ReadBasis
from . import registry
from . import tools as agent_tools
from .authorization import (
    AgentToolPDP,
    C32KnowledgeBoundary,
    CurrentAuthorization,
    InProcessAgentToolPDP,
    KnowledgeBoundary,
    ToolRequest,
    policy_allows,
)
from .memory import MemoryScope, MemoryStore
from .model import FactsOnlyComposer, LLMClient, ModelRequest, ModelUnavailable, parse_output

LABEL = "AI representation of an employee role, built from records. It is not the employee and takes no action."
PROMPT_VERSION = "agent-turn/1"
HEDGES = {"participant": "", "possible": "I'd have had access to this record (not that I read it): ",
          "role_plausible": "In my role I'd plausibly have been aware of this: "}
_STRENGTH = {"participant": 0, "possible": 1, "role_plausible": 2}
ESCALATION = {"to": "the accountable human owner", "how": "an approval workflow; the agent takes no action"}
_ROUTES = (
    ("people.headcount", r"\b(headcount|how many (people|employees|staff))\b"),
    ("finance.ledger_spend", r"\b(spend|spent|ledger|costs?|expenses?)\b"),
    ("delivery.board_statements", r"\b(board|reported|arr|figures?)\b"),
    ("delivery.timeline", r"\b(timeline|when|dates?|events?)\b"),
    ("conflicts.register", r"\b(conflicts?|disagree\w*)\b"),
    ("briefing.today", r"\b(today|worry|attention)\b"),
    ("coverage.oncall", r"\b(on[- ]?call|coverage|rota)\b"),
)


class AgentError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code, self.detail = code, detail


@dataclass(frozen=True)
class ToolCall:
    tool_id: str
    args: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AgentRequest:
    tenant_id: uuid.UUID
    interviewer: vt.Principal                 # authenticated by the caller (C02); never taken from the message
    session_id: uuid.UUID
    message: str
    idempotency_key: str
    tool_calls: tuple[ToolCall, ...] | None = None
    purpose: str = "interview"

    def digest(self) -> str:
        body = {"message": self.message, "purpose": self.purpose,
                "tools": [[c.tool_id, c.args] for c in self.tool_calls] if self.tool_calls is not None else None}
        return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


class AgentRuntime:
    def __init__(self, conn: psycopg.Connection, tz: str, *, pdp: AgentToolPDP | None = None,
                 kb: KnowledgeBoundary | None = None, llm: LLMClient | None = None, authorizer=None,
                 tools: dict | None = None):
        self.conn, self.tz = conn, tz
        self.pdp = pdp or InProcessAgentToolPDP()
        self.kb = kb or C32KnowledgeBoundary(conn)
        self.llm = llm or FactsOnlyComposer()
        self.authorizer = authorizer or CurrentAuthorization(conn)
        self.tools = tools if tools is not None else agent_tools.REGISTRY

    # ------------------------------------------------------------------------------------------------ sessions
    def open_session(self, tenant_id: uuid.UUID, interviewer: vt.Principal, agent_id: str, basis: ReadBasis) -> dict:
        if not interviewer.principal_id.startswith("person:"):
            raise AgentError("not_authorised", "the interviewer does not resolve to a person")
        if basis.mode not in (Mode.CURRENT, Mode.KNOWN_AT):
            raise AgentError("invalid_time", "agent sessions read at CURRENT or KNOWN_AT; the agent tools record no "
                                             "recorded or valid time")
        cfg = registry.latest(self.conn, tenant_id, agent_id)
        if cfg is None:
            raise AgentError("not_found", "no such agent")
        if cfg.activation not in ("synthetic_test", "active"):
            raise AgentError("agent_not_activated", "the agent's mapping and configuration are not approved")
        sid = uuid.uuid4()
        with tenant_tx(self.conn, tenant_id, interviewer.principal_id, basis.batch_seq):
            self.conn.execute(
                "INSERT INTO runtime.agent_session (tenant_id, session_id, agent_id, config_version, interviewer_id,"
                " batch_seq, mode, as_of) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant_id, sid, agent_id, cfg.config_version, interviewer.principal_id, basis.batch_seq,
                 str(basis.mode), basis.as_of))
            audit.append(self.conn, tenant_id, "agent.session_opened",
                         {"session_id": str(sid), "agent_id": agent_id, "config_version": cfg.config_version,
                          "batch_seq": basis.batch_seq})
        return {"session_id": str(sid), "agent_id": agent_id, "config_version": cfg.config_version,
                "batch_seq": basis.batch_seq, "label": LABEL}

    def close_session(self, tenant_id, interviewer: vt.Principal, session_id) -> int:
        s = self._session(tenant_id, interviewer, session_id)
        store = MemoryStore(self.conn, self.authorizer)
        return store.close_session(MemoryScope(tenant_id, s["agent_id"], interviewer.principal_id), session_id)

    def _session(self, tenant_id, interviewer, session_id) -> dict:
        with tenant_tx(self.conn, tenant_id, interviewer.principal_id):
            s = self.conn.execute("SELECT * FROM runtime.agent_session WHERE session_id = %s",
                                  (session_id,)).fetchone()
        if s is None:
            raise AgentError("not_found", "no such session for this interviewer")
        return s

    def _basis(self, tenant_id, s) -> ReadBasis:
        """The session's pinned K with its assessment instant: now for CURRENT, the release of K for KNOWN_AT."""
        if s["mode"] == "CURRENT":
            now = datetime.now(UTC)
            return ReadBasis(tenant_id, s["batch_seq"], Mode.CURRENT, now, now, None)
        released = dict(ledger.sealed_releases(self.conn, tenant_id)).get(s["batch_seq"])
        return ReadBasis(tenant_id, s["batch_seq"], Mode(s["mode"]), released, released, s["as_of"],
                         nothing_admitted=released is None)

    def _config(self, tenant_id, agent_id, version) -> registry.AgentConfig:
        with tenant_tx(self.conn, tenant_id):
            r = self.conn.execute("SELECT * FROM runtime.agent_config WHERE tenant_id = %s AND agent_id = %s AND"
                                  " config_version = %s", (tenant_id, agent_id, version)).fetchone()
        return registry._row(r)

    # ------------------------------------------------------------------------------------------------ turns
    def execute(self, req: AgentRequest) -> dict:
        if not 8 <= len(req.idempotency_key) <= 200:
            raise AgentError("invalid_request", "idempotency_key must be 8 to 200 characters")
        if not req.message.strip() or len(req.message) > 2000:
            raise AgentError("invalid_request", "message must be 1 to 2000 characters")
        s = self._session(req.tenant_id, req.interviewer, req.session_id)
        digest = req.digest()
        with tenant_tx(self.conn, req.tenant_id, req.interviewer.principal_id):
            prior = self.conn.execute("SELECT request_sha256, response FROM runtime.agent_execution WHERE"
                                      " session_id = %s AND idempotency_key = %s",
                                      (req.session_id, req.idempotency_key)).fetchone()
        if prior is not None:
            if prior["request_sha256"] != digest:
                raise AgentError("idempotency_conflict", "the idempotency key was used for a different request")
            return prior["response"] | {"replayed": True}

        cfg = self._config(req.tenant_id, s["agent_id"], s["config_version"])
        current = registry.latest(self.conn, req.tenant_id, s["agent_id"])
        basis = self._basis(req.tenant_id, s)
        turn = self._turn(req, s, cfg, current, basis)
        return self._record(req, s, cfg, basis, digest, turn)

    def _turn(self, req, s, cfg, current, basis) -> dict:
        base = {"label": LABEL, "agent_id": cfg.agent_id, "config_version": cfg.config_version,
                "runtime_version": registry.RUNTIME_VERSION, "session_id": str(req.session_id),
                "read_basis": {"batch_seq": basis.batch_seq, "mode": str(basis.mode), "as_of": basis.as_of},
                "claims": [], "evidence": [], "tools": [], "unknowns": [], "excluded": {}, "refusal": None,
                "escalation": None, "persona": None, "_episodic": [], "_reflection": None}
        if current is None or current.activation not in ("synthetic_test", "active"):
            return base | {"status": "refused", "refusal": {"policy": "AGENT-ACTIVATION",
                                                             "reason": "agent_not_activated"},
                           "escalation": ESCALATION}
        if req.purpose != "interview":
            return base | {"status": "refused", "refusal": {"policy": "AGENT-PURPOSE",
                                                             "reason": "purpose_missing_or_undeclared"}}
        try:          # names at the basis, only to recognise that a question targets an individual (as in Ask)
            names = [p["full_name"] for p in vt._person_snapshot(self.conn, basis)]
        except ValueError:
            names = []
        refusal = ask_policy.prescreen(req.message, names)
        if refusal:
            return base | {"status": "refused", "escalation": ESCALATION,
                           "refusal": {"policy": refusal.policy.policy_id, "reason": refusal.policy.reason,
                                       "alternative": refusal.policy.alternative}}

        calls = list(req.tool_calls) if req.tool_calls is not None else self._route(req.message, cfg)
        facts, evidence = [], {}
        for call in calls:
            outcome = self._run_tool(req, s, cfg, basis, call)
            base["tools"].append({k: v for k, v in outcome.items() if k not in ("output",)})
            out = outcome.get("output")
            if out is None:
                continue
            base["unknowns"] += out["gaps"] + ([out["reason"]] if out["reason"] else [])
            facts += out["facts"]
            for e in out["evidence"]:
                evidence[e["record_id"]] = e

        permitted, labels, excluded = self._knowledge(req, cfg, basis, evidence)
        base["excluded"] = excluded
        usable = [f for f in facts if f["inputs"] and all(i in permitted for i in f["inputs"])]
        if len(usable) < len(facts):
            base["unknowns"].append(f"{len(facts) - len(usable)} fact(s) rest on evidence this agent may not use "
                                    "(outside KB ∩ AZ ∩ POL); they are not shown")
        if not usable:
            esc = ESCALATION if excluded.get("authorization") else None
            return base | {"status": "abstained", "escalation": esc,
                           "unknowns": base["unknowns"] or ["no permitted evidence answers this"]}

        req_model = ModelRequest(req.tenant_id, "agent", frozenset(permitted[r]["data_class"] for r in permitted),
                                 {"type": "object"}, PROMPT_VERSION,
                                 [{"role": "system", "content": LABEL},
                                  {"role": "user", "content": req.message,
                                   "facts": [{"fact_id": f["fact_id"], "display": f["display"]} for f in usable]}],
                                 512, f"{req.session_id}:{req.idempotency_key}")
        parsed = None
        for _ in range(2):                                       # invalid output -> one retry -> stage failure
            try:
                resp = self.llm.call(req_model)
            except ModelUnavailable:
                return base | {"status": "unavailable",
                               "unknowns": base["unknowns"] + ["the model is unavailable; nothing was composed"]}
            parsed = parse_output(resp.output)
            if parsed is not None:
                break
        if parsed is None:
            return base | {"status": "failed", "unknowns": base["unknowns"] + ["model output failed validation"]}

        by_id = {f["fact_id"]: f for f in usable}
        claims, dropped = [], 0
        for c in parsed.claims:
            cited = [by_id[i] for i in c.cites if i in by_id]
            if len(cited) != len(c.cites) or c.text.strip() not in {f["display"] for f in cited}:
                dropped += 1                                     # uncited, unknown citation or unsupported wording
                continue
            fact = next(f for f in cited if f["display"] == c.text.strip())
            weakest = max((labels[i] for i in fact["inputs"]), key=lambda b: _STRENGTH[b])
            claims.append({"text": HEDGES[weakest] + fact["display"], "fact_id": fact["fact_id"],
                           "evidence": list(fact["inputs"]), "knowledge_basis": weakest})
        if dropped:
            base["unknowns"].append(f"{dropped} composed claim(s) failed verification and were dropped")
        cited_ids = sorted({i for c in claims for i in c["evidence"]})
        base["evidence"] = [permitted[i] | {"knowledge_basis": labels[i]} for i in cited_ids]
        base["_episodic"] = [r for r in cited_ids if labels[r] == "participant"]
        base["_reflection"] = (parsed.reflection, getattr(self.llm, "name", "unknown")) if parsed.reflection else None
        status = ("abstained" if not claims else
                  "qualified" if base["unknowns"] or any(c["knowledge_basis"] != "participant" for c in claims)
                  else "answered")
        return base | {"status": status, "claims": claims}

    def _route(self, message: str, cfg: registry.AgentConfig) -> list[ToolCall]:
        calls = [ToolCall(t) for t, rx in _ROUTES if t in cfg.tools and re.search(rx, message, re.IGNORECASE)]
        if not calls and "evidence.search" in cfg.tools:
            q = " ".join(re.findall(r"[A-Za-z][\w-]{2,}", message))[:120]
            if len(q) >= 2:
                calls = [ToolCall("evidence.search", {"query": q})]
        return calls

    def _run_tool(self, req, s, cfg, basis, call: ToolCall) -> dict:
        decision = self.pdp.decide(ToolRequest(req.tenant_id, cfg, req.tenant_id, req.interviewer.principal_id,
                                               call.tool_id, req.purpose))
        result = {"tool": call.tool_id, "decision": "allowed" if decision.allowed else "denied",
                  "reason": decision.reason, "policy_version": decision.policy_version, "status": None}
        if decision.allowed:
            spec = self.tools.get(call.tool_id)
            try:
                args = spec.input_model.model_validate(call.args)
            except ValidationError:
                result |= {"status": "invalid_input"}
            else:
                started = time.monotonic()
                try:
                    raw = spec.fn(agent_tools.ToolContext(self.conn, req.tenant_id, basis, req.interviewer, self.tz),
                                  args)
                except Exception as e:  # noqa: BLE001 - a tool failure is reported, never the whole turn
                    result |= {"status": "error", "error": type(e).__name__}
                else:
                    if time.monotonic() - started > spec.timeout_s:
                        result |= {"status": "timeout"}          # late output is discarded, never used
                    else:
                        try:
                            out = agent_tools.ToolOutput.model_validate(raw).model_dump()
                            result |= {"status": out["status"], "output": out}
                        except ValidationError:
                            result |= {"status": "malformed_output"}
        with tenant_tx(self.conn, req.tenant_id, req.interviewer.principal_id):
            audit.append(self.conn, req.tenant_id, "agent.tool_call",
                         {"session_id": str(req.session_id), "agent_id": cfg.agent_id, "tool": call.tool_id,
                          "decision": result["decision"], "reason": result["reason"], "status": result["status"]})
        return result

    def _knowledge(self, req, cfg, basis, evidence: dict) -> tuple[dict, dict, dict]:
        """D = KB ∩ AZ ∩ POL over the evidence the tools returned. Returns (permitted, labels, excluded counts)."""
        excluded = {"policy": 0, "authorization": 0, "knowledge": 0}
        pol = {r: e for r, e in evidence.items() if policy_allows(e["data_class"])}
        excluded["policy"] = len(evidence) - len(pol)
        az = {r: e for r, e in pol.items()
              if self.authorizer.allowed(req.tenant_id, req.interviewer.principal_id, e["system"], e["data_class"])[0]}
        excluded["authorization"] = len(pol) - len(az)
        labels = self.kb.labels(cfg, sorted(az), basis) if az else {}
        permitted = {r: e for r, e in az.items() if labels.get(r)}
        excluded["knowledge"] = len(az) - len(permitted)
        return permitted, {r: labels[r] for r in permitted}, excluded

    def _record(self, req, s, cfg, basis, digest, turn: dict) -> dict:
        episodic, reflection = turn.pop("_episodic"), turn.pop("_reflection")
        execution_id = uuid.uuid4()
        turn["execution_id"] = str(execution_id)
        turn["executed_at"] = datetime.now(UTC).isoformat()
        turn["replayed"] = False
        store = MemoryStore(self.conn, self.authorizer)
        scope = MemoryScope(req.tenant_id, cfg.agent_id, req.interviewer.principal_id)
        with self.conn.transaction():                            # memory is transactional with the turn
            store.append_working(scope, req.session_id, {"message": req.message, "status": turn["status"],
                                                         "claims": [c["fact_id"] for c in turn["claims"]]},
                                 basis.batch_seq, execution_id)
            for rid in episodic:
                e = next(x for x in turn["evidence"] if x["record_id"] == rid)
                with tenant_tx(self.conn, req.tenant_id, req.interviewer.principal_id):
                    self.conn.execute("SELECT set_config('app.agent_id', %s, true),"
                                      " set_config('app.interviewer_id', %s, true)",
                                      (cfg.agent_id, req.interviewer.principal_id))
                    seen = self.conn.execute("SELECT 1 FROM runtime.agent_memory_current WHERE tier = 'episodic'"
                                             " AND %s = ANY(source_records)", (rid,)).fetchone()
                if not seen:
                    store.append_episodic(scope, e, "participant", basis.batch_seq, execution_id)
            if reflection:
                store.append_reflective(scope, reflection[0], reflection[1], PROMPT_VERSION, basis.batch_seq,
                                        req.session_id)
            with tenant_tx(self.conn, req.tenant_id, req.interviewer.principal_id):
                self.conn.execute(
                    "INSERT INTO runtime.agent_execution (tenant_id, execution_id, session_id, idempotency_key,"
                    " request_sha256, status, runtime_version, response) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (req.tenant_id, execution_id, req.session_id, req.idempotency_key, digest, turn["status"],
                     registry.RUNTIME_VERSION, json.dumps(turn, default=str)))
                audit.append(self.conn, req.tenant_id, "agent.executed", {
                    "execution_id": str(execution_id), "session_id": str(req.session_id), "agent_id": cfg.agent_id,
                    "config_version": cfg.config_version, "status": turn["status"],
                    "tools": [[t["tool"], t["decision"], t["status"]] for t in turn["tools"]],
                    "claims": len(turn["claims"]), "excluded": turn["excluded"],
                    "policy": (turn["refusal"] or {}).get("policy")})
        return turn
