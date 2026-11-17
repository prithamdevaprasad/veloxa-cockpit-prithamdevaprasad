"""Provider-neutral model interface for agents (ARCHITECTURE.md §18 `LLMClient`; C27 is NOT implemented).

No provider is selected or called. Implementations here:
- `UnavailableLLM`: what production has today. Agent turns that need composition report `unavailable` (§5.2 C23).
- `FactsOnlyComposer`: deterministic, no model. Renders each permitted fact verbatim (the same facts-only mode as
  Ask). It is not a model and is labelled as such.
- `ScriptedTestModel` (test-only): returns a fixed structured output, so tests can drive malicious or malformed
  model behaviour through the real runtime.

The model's structured output can only propose claims that cite fact ids it was given, plus an optional reflection.
Its schema has no field for memory writes, authorization, identity approval or policy: the runtime decides those,
and verifies every claim against the evidence before returning it.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError


@dataclass(frozen=True)
class ModelRequest:                      # §18.1 contract
    tenant_id: uuid.UUID
    stage: Literal["plan", "compose", "verify", "agent", "extract", "risk_narrative"]
    data_classes: frozenset[str]
    schema: dict
    prompt_version: str
    messages: list[dict]
    budget_estimate_tokens: int
    idempotency_key: str

    def request_hash(self) -> str:
        body = json.dumps({"stage": self.stage, "prompt": self.prompt_version, "messages": self.messages},
                          sort_keys=True, default=str)
        return hashlib.sha256(body.encode()).hexdigest()


@dataclass(frozen=True)
class ModelResponse:
    model: str
    output: dict
    request_hash: str


class ModelUnavailable(Exception):
    pass


class ModelOutputInvalid(Exception):
    """The structured output failed validation after its one retry (stage failure, §18.2)."""


class LLMClient(Protocol):
    name: str

    def call(self, request: ModelRequest) -> ModelResponse: ...


class ClaimOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=600)
    cites: list[str] = Field(min_length=1, max_length=10)


class AgentModelOutput(BaseModel):
    """The ONLY shape accepted from a model for an agent turn."""
    model_config = ConfigDict(extra="forbid")
    claims: list[ClaimOut] = Field(max_length=12)
    reflection: str | None = Field(default=None, max_length=1000)


def parse_output(raw: dict) -> AgentModelOutput | None:
    try:
        return AgentModelOutput.model_validate(raw)
    except ValidationError:
        return None


class UnavailableLLM:
    name = "unavailable"

    def call(self, request: ModelRequest) -> ModelResponse:
        raise ModelUnavailable("no LLM provider is configured (C27 not implemented)")


class FactsOnlyComposer:
    """Deterministic rendering of permitted facts; not a model."""
    name = "facts-only/1"

    def call(self, request: ModelRequest) -> ModelResponse:
        facts = request.messages[-1]["facts"]
        return ModelResponse(self.name, {"claims": [{"text": f["display"], "cites": [f["fact_id"]]}
                                                    for f in facts][:12]}, request.request_hash())


@dataclass
class ScriptedTestModel:
    """TEST-ONLY: returns `outputs` in order (the last one repeats). Records the requests it received."""
    outputs: list[dict]
    name: str = "test/scripted"
    seen: list[ModelRequest] = field(default_factory=list)

    def call(self, request: ModelRequest) -> ModelResponse:
        self.seen.append(request)
        out = self.outputs[min(len(self.seen) - 1, len(self.outputs) - 1)]
        if out == "unavailable":
            raise ModelUnavailable("scripted outage")
        return ModelResponse(self.name, out, request.request_hash())
