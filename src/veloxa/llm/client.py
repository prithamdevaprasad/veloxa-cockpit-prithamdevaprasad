"""C27 `LLMClient`: the single, governed path to any model (ARCHITECTURE.md §18; migration 0015).

No provider is selected (owner decision, §26). The provider is an adapter (`Provider`); none is configured in
production, so the default tenant policy (`DENY_ALL`) refuses every call before anything is sent. Policy is
in-house logic, never a third-party proxy (§18.1). In order, every call:

1. **Policy (model permissions, residency):** the tenant's policy names the permitted provider, model, region,
   stages and data classes. Anything not permitted is refused before any call.
2. **Redaction:** facts whose data class the tenant forbids sending are removed before prompt assembly, and the
   count is recorded. If a forbidden class would still be present, the call is refused.
3. **Budget:** a hard pre-call reservation (`runtime.llm_reserve`, one atomic UPDATE). No budget row or not enough
   headroom means the call is refused before it is made. Owner values set the limits; with none set, every call is
   refused.
4. **Call:** bounded retries on provider errors.
5. **Structured output:** validated on receipt. Invalid output gets one retry, then the stage fails.
6. **Reconcile:** the reservation is released and actual usage counted. Replayed calls count no usage.
7. **Record:** one append-only `runtime.llm_call` row per call or refusal. Hashes, versions, classes and token
   counts only; never prompt or response text.

Record/replay: `ReplayProvider` serves recorded responses by request hash. A missing recording fails loudly
(`RecordingMissing`), never falling back to a live call.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import psycopg

from ..agents.model import ModelOutputInvalid, ModelRequest, ModelResponse, ModelUnavailable, parse_output
from ..db import tenant_tx

__all__ = ["ModelOutputInvalid", "GovernedLLMClient", "TenantModelPolicy", "DENY_ALL", "ReplayProvider",
           "ScriptedProvider", "RecordingMissing", "ProviderError"]


class ProviderError(Exception):
    """A transient provider failure (retried within the bound)."""


class RecordingMissing(Exception):
    """Replay mode found no recording for this request: fail loudly, never call live."""


@dataclass(frozen=True)
class TenantModelPolicy:
    """Per-tenant model permissions and residency (§18.2). Owner values; DENY_ALL until the owner sets them."""
    provider: str | None
    model: str | None
    region: str | None = None
    stages: frozenset[str] = frozenset()
    allowed_classes: frozenset[str] = frozenset()
    max_estimate_tokens: int = 0


DENY_ALL = TenantModelPolicy(None, None)


class Provider(Protocol):
    name: str
    region: str | None

    def complete(self, model: str, request: ModelRequest) -> tuple[dict, int]: ...      # (output, tokens used)


@dataclass
class ScriptedProvider:
    """TEST-ONLY provider: returns scripted outputs in order; a string `"error"` raises ProviderError."""
    outputs: list
    tokens: int = 10
    name: str = "test-scripted"
    region: str | None = "local"
    calls: int = 0

    def complete(self, model: str, request: ModelRequest) -> tuple[dict, int]:
        out = self.outputs[min(self.calls, len(self.outputs) - 1)]
        self.calls += 1
        if out == "error":
            raise ProviderError("scripted provider error")
        return out, self.tokens


@dataclass
class ReplayProvider:
    """Serves recorded responses (`<request_sha256>.json`) from a directory; no live calls, ever."""
    directory: Path
    name: str = "replay"
    region: str | None = "local"

    def complete(self, model: str, request: ModelRequest) -> tuple[dict, int]:
        f = self.directory / f"{request.request_hash()}.json"
        if not f.is_file():
            raise RecordingMissing(f"no recording for request {request.request_hash()[:12]}")
        return json.loads(f.read_text(encoding="utf-8"))["output"], 0


VALIDATORS: dict[str, Callable[[dict], bool]] = {"agent-turn/1": lambda o: parse_output(o) is not None}


def _period(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y-%m")


@dataclass
class GovernedLLMClient:
    conn: psycopg.Connection
    policy: TenantModelPolicy = DENY_ALL
    provider: Provider | None = None
    max_provider_attempts: int = 2
    validators: dict = field(default_factory=lambda: dict(VALIDATORS))
    name: str = "governed"

    def _record(self, req: ModelRequest, status: str, *, redacted=0, reserved=0, used=0, attempts=0) -> None:
        replayed = isinstance(self.provider, ReplayProvider)
        with tenant_tx(self.conn, req.tenant_id):
            self.conn.execute(
                "INSERT INTO runtime.llm_call (tenant_id, call_id, stage, provider, model, prompt_version,"
                " request_sha256, idempotency_key, data_classes, redacted_items, reserved_tokens, used_tokens,"
                " status, replayed, attempts) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (req.tenant_id, uuid.uuid4(), req.stage, self.provider.name if self.provider else "none",
                 self.policy.model or "none", req.prompt_version, req.request_hash(), req.idempotency_key,
                 sorted(req.data_classes), redacted, reserved, 0 if replayed else used, status, replayed, attempts))

    def _refuse(self, req, status, reason, **kw):
        from .. import telemetry

        telemetry.METRICS.inc("veloxa_model_refusals_total", status=status)
        self._record(req, status, **kw)
        raise ModelUnavailable(f"{status}: {reason}")

    def call(self, req: ModelRequest) -> ModelResponse:
        p = self.policy
        # 1. policy: provider, region, stage, estimate
        if self.provider is None or p.provider is None or p.provider != self.provider.name or p.model is None:
            self._refuse(req, "refused_policy", "no permitted model provider is configured for this tenant")
        if p.region is not None and self.provider.region != p.region:
            self._refuse(req, "refused_policy", "the provider's region is not permitted (residency)")
        if req.stage not in p.stages:
            self._refuse(req, "refused_policy", f"stage {req.stage!r} is not permitted for this tenant")
        if req.budget_estimate_tokens <= 0 or req.budget_estimate_tokens > p.max_estimate_tokens:
            self._refuse(req, "refused_policy", "the token estimate is missing or above the per-call limit")
        # 2. redaction before prompt assembly
        msgs = json.loads(json.dumps(req.messages, default=str))
        redacted = 0
        for m in msgs:
            if isinstance(m.get("facts"), list):
                keep = [f for f in m["facts"] if set(f.get("data_classes", ["unknown"])) <= p.allowed_classes]
                redacted += len(m["facts"]) - len(keep)
                m["facts"] = keep
        remaining = {c for m in msgs for f in m.get("facts", []) for c in f.get("data_classes", [])}
        if remaining - p.allowed_classes or (req.data_classes - p.allowed_classes and not redacted):
            self._refuse(req, "refused_policy", "a data class the tenant forbids sending would be sent",
                         redacted=redacted)
        sent = ModelRequest(req.tenant_id, req.stage, frozenset(remaining), req.schema, req.prompt_version, msgs,
                            req.budget_estimate_tokens, req.idempotency_key)
        # 3. hard pre-call reservation
        period = _period()
        with tenant_tx(self.conn, req.tenant_id):
            ok = self.conn.execute("SELECT runtime.llm_reserve(%s, %s, %s) AS ok",
                                   (req.tenant_id, period, req.budget_estimate_tokens)).fetchone()["ok"]
        if not ok:
            self._refuse(req, "refused_budget", "no budget, or not enough headroom, for this call", redacted=redacted)
        attempts, used, status, output = 0, 0, "provider_error", None
        import time as _time

        from .. import telemetry

        started = _time.perf_counter()
        try:
            # 4-5. bounded provider retries; one retry on invalid structured output
            validate = self.validators.get(req.prompt_version, lambda o: isinstance(o, dict))
            for _ in range(2):
                for _try in range(self.max_provider_attempts):
                    attempts += 1
                    try:
                        output, tokens = self.provider.complete(p.model, sent)
                        used += tokens
                        break
                    except ProviderError:
                        output = None
                if output is None:
                    status = "provider_error"
                    break
                if validate(output):
                    status = "ok"
                    break
                status, output = "invalid_output", None
        except RecordingMissing:
            status = "replay_missing"
            raise
        finally:
            # 6. reconcile, 7. record
            with tenant_tx(self.conn, req.tenant_id):
                self.conn.execute("SELECT runtime.llm_reconcile(%s, %s, %s, %s)",
                                  (req.tenant_id, period, req.budget_estimate_tokens,
                                   0 if isinstance(self.provider, ReplayProvider) else used))
            self._record(req, status, redacted=redacted, reserved=req.budget_estimate_tokens, used=used,
                         attempts=attempts)
            telemetry.METRICS.observe("veloxa_model_call_seconds", _time.perf_counter() - started, stage=req.stage,
                                      status=status)
        if status == "invalid_output":
            raise ModelOutputInvalid("the model's structured output failed validation twice")
        if status != "ok":
            raise ModelUnavailable("provider_error: the provider failed within the retry bound")
        return ModelResponse(f"{self.provider.name}/{p.model}", output, req.request_hash())
