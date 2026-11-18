"""Gemini provider adapter for the C27 governed client (owner-approved EXPERIMENT, 18 Nov 2026; synthetic only).

Scope and safety (all enforced here or by GovernedLLMClient; nothing bypasses the client):
- Free-tier terms allow Google to use submitted content, so the adapter serves ONLY tenants that are (a) marked
  synthetic in the tenant directory and (b) listed in `VELOXA_GEMINI_TENANTS` (e.g. `demo`, the Lumora Demo Lab).
  The hackathon tenant (the supplied company's Day folders) is refused by name, whatever the configuration says.
- The key is read only from the backend environment variable `GEMINI_API_KEY`; it is never logged, returned,
  recorded or sent to the browser. There is no `VITE_` variable and no frontend model call.
- No tools, no Google Search grounding, no function calling: the model receives the verified facts of ONE answer and
  returns JSON matching a fixed schema. Validation, the deterministic verifier and fallback happen downstream.
- Usage controls: per-request output limit, a daily request cap (`VELOXA_GEMINI_DAILY_REQUESTS`, default 200), a
  concurrency cap (2), a timeout (20 s), retries only for transient errors (the client's bounded retry), and HTTP 429
  / quota errors are NOT retried (`QuotaExceeded`). There is never a fallback to another (paid) model.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .client import ProviderError

DEFAULT_MODEL = "gemini-3.1-flash-lite"
REFUSED_TENANT_SLUGS = frozenset({"veloxa"})       # the supplied company: NEVER sent to the free tier


class QuotaExceeded(Exception):
    """HTTP 429 or a daily cap: not retried, no fallback model."""


class TenantNotPermitted(Exception):
    """The tenant is not an allowlisted synthetic tenant."""


@dataclass
class UsageMeter:
    """In-process daily request counter and concurrency limit (per adapter instance)."""
    daily_cap: int
    concurrency: int = 2
    day: str = ""
    count: int = 0
    _sem: threading.Semaphore = field(default=None, repr=False)   # type: ignore[assignment]

    def __post_init__(self):
        self._sem = threading.BoundedSemaphore(self.concurrency)

    def acquire(self) -> None:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        if today != self.day:
            self.day, self.count = today, 0
        if self.count >= self.daily_cap:
            raise QuotaExceeded(f"daily request cap ({self.daily_cap}) reached")
        if not self._sem.acquire(timeout=30):
            raise QuotaExceeded("concurrency cap reached")
        self.count += 1

    def release(self) -> None:
        self._sem.release()


def check_tenant(slug: str, synthetic: bool, allowed: frozenset[str] | None = None) -> None:
    allowed = allowed if allowed is not None else frozenset(
        x.strip() for x in os.environ.get("VELOXA_GEMINI_TENANTS", "").split(",") if x.strip())
    if slug in REFUSED_TENANT_SLUGS or not synthetic or slug not in allowed:
        raise TenantNotPermitted(f"tenant {slug!r} may not use the free-tier experiment (synthetic allowlist only)")


SYSTEM = ("You rewrite verified facts for a company CEO in plain English. Rules: use ONLY the facts given; never add a "
          "number, date, name or claim that is not in them; keep every fact's meaning and its fact_id; if facts "
          "disagree, say so; do not give advice. Facts labelled as an assumption stay an assumption and facts "
          "labelled as not established stay unknown; never add a cause, likelihood, consequence or recommendation. "
          "Return JSON only.")


@dataclass
class GeminiProvider:
    """`complete(model, request)` for GovernedLLMClient. `client_factory` is injectable for tests (no network)."""
    name: str = "gemini"
    region: str | None = "google-ai-studio"
    max_output_tokens: int = 1500       # an explanation re-words up to ~12 items; 600 truncated the JSON
    timeout_s: float = 30.0
    meter: UsageMeter = field(default_factory=lambda: UsageMeter(int(os.environ.get("VELOXA_GEMINI_DAILY_REQUESTS",
                                                                                    "200"))))
    client_factory: object = None
    last_latency_ms: int = 0
    last_usage: dict = field(default_factory=dict)

    def _client(self):
        if self.client_factory is not None:
            return self.client_factory()
        if not os.environ.get("GEMINI_API_KEY"):
            raise ProviderError("GEMINI_API_KEY is not configured on the backend")
        from google import genai
        from google.genai import types

        return genai.Client(http_options=types.HttpOptions(timeout=int(self.timeout_s * 1000)))

    def complete(self, model: str, request) -> tuple[dict, int]:
        if request.prompt_version != "answer-wording/1":      # defence in depth: the experiment's only task
            raise TenantNotPermitted(f"prompt {request.prompt_version!r} is not part of the wording experiment")
        prompt = json.dumps({"task": request.stage, "schema": request.schema, "messages": request.messages},
                            ensure_ascii=False, default=str)
        self.meter.acquire()
        started = time.perf_counter()
        try:
            client = self._client()
            try:
                from google.genai import types

                cfg = types.GenerateContentConfig(system_instruction=SYSTEM, temperature=0.2,
                                                  max_output_tokens=self.max_output_tokens,
                                                  response_mime_type="application/json")
            except ImportError:            # injected test clients do not need the SDK
                cfg = {"system_instruction": SYSTEM, "max_output_tokens": self.max_output_tokens}
            resp = client.models.generate_content(model=model, contents=prompt, config=cfg)
        except QuotaExceeded:
            raise
        except Exception as e:  # noqa: BLE001  (classify provider failures without leaking details)
            code = getattr(e, "code", None) or getattr(e, "status_code", None)
            if code == 429 or "RESOURCE_EXHAUSTED" in str(e):
                raise QuotaExceeded("provider quota exhausted (HTTP 429)") from None
            raise ProviderError(f"provider error ({type(e).__name__})") from None
        finally:
            self.meter.release()
            self.last_latency_ms = round((time.perf_counter() - started) * 1000)
        meta = getattr(resp, "usage_metadata", None)
        tin = int(getattr(meta, "prompt_token_count", 0) or 0)
        tout = int(getattr(meta, "candidates_token_count", 0) or 0)
        self.last_usage = {"input_tokens": tin, "output_tokens": tout}
        try:
            out = json.loads(resp.text)
        except (TypeError, ValueError):
            out = {"_invalid": True}
        return (out if isinstance(out, dict) else {"_invalid": True}), tin + tout
