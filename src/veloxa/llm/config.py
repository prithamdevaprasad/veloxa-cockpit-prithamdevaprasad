"""Model configuration from the environment (C27; ARCHITECTURE.md §18). DENY_ALL unless fully configured.

No real model provider is selected (owner decision). The only adapters implemented are `replay` (recorded
responses; no network, no key) and the TEST-ONLY scripted provider, which is never selectable here. So **no API-key
variable exists yet**: a key variable is added only together with an owner-approved provider adapter.

| Variable | Meaning |
|---|---|
| `VELOXA_MODEL_PROVIDER` | Name of an IMPLEMENTED adapter (`replay`); unset = DENY_ALL |
| `VELOXA_MODEL_NAME` | Model identifier recorded on every call |
| `VELOXA_MODEL_REGION` | Permitted region (residency); the adapter must match |
| `VELOXA_MODEL_STAGES` | Comma-separated permitted stages (e.g. `agent`) |
| `VELOXA_MODEL_ALLOWED_CLASSES` | Comma-separated data classes that may be sent (e.g. `internal,personal`) |
| `VELOXA_MODEL_MAX_ESTIMATE_TOKENS` | Per-call token ceiling |
| `VELOXA_MODEL_REPLAY_DIR` | Directory of recordings for the `replay` adapter |

Token budgets are not environment settings. They are owner-set rows in `runtime.llm_budget`, and with no row every
call is refused before it is made. A setting that is missing or unknown leaves the model DISABLED and is reported
by `status()`, which names settings but never their values.
"""

from __future__ import annotations

import os
from pathlib import Path

from .client import DENY_ALL, Provider, ReplayProvider, TenantModelPolicy

IMPLEMENTED_ADAPTERS = ("replay", "gemini")
_PROVIDERS: dict = {}          # one adapter instance per process, so its daily and concurrency caps hold
_REQUIRED = ("VELOXA_MODEL_PROVIDER", "VELOXA_MODEL_NAME", "VELOXA_MODEL_STAGES", "VELOXA_MODEL_ALLOWED_CLASSES",
             "VELOXA_MODEL_MAX_ESTIMATE_TOKENS")


def _csv(name: str) -> frozenset[str]:
    return frozenset(x.strip() for x in os.environ.get(name, "").split(",") if x.strip())


def status() -> dict:
    """What is configured, never the values of secrets (none exist yet) or paths."""
    provider = os.environ.get("VELOXA_MODEL_PROVIDER", "").strip()
    missing = [v for v in _REQUIRED if not os.environ.get(v, "").strip()]
    if not provider:
        return {"model": "disabled", "reason": "no provider configured (DENY_ALL); facts-only composition"}
    if provider not in IMPLEMENTED_ADAPTERS:
        return {"model": "disabled", "reason": f"provider {provider!r} has no implemented adapter",
                "implemented_adapters": list(IMPLEMENTED_ADAPTERS)}
    if missing:
        return {"model": "disabled", "reason": "incomplete configuration", "missing": missing}
    if provider == "gemini" and not os.environ.get("GEMINI_API_KEY", "").strip():
        return {"model": "disabled", "reason": "incomplete configuration", "missing": ["GEMINI_API_KEY"]}
    if provider == "replay" and not os.environ.get("VELOXA_MODEL_REPLAY_DIR", "").strip():
        return {"model": "disabled", "reason": "incomplete configuration", "missing": ["VELOXA_MODEL_REPLAY_DIR"]}
    return {"model": "configured", "provider": provider,
            "budget": "owner-set rows in runtime.llm_budget (none = refused)"}


def policy_and_provider() -> tuple[TenantModelPolicy, Provider | None]:
    if status()["model"] != "configured":
        return DENY_ALL, None
    name = os.environ["VELOXA_MODEL_PROVIDER"].strip()
    try:
        ceiling = int(os.environ["VELOXA_MODEL_MAX_ESTIMATE_TOKENS"])
    except ValueError:
        return DENY_ALL, None
    region = os.environ.get("VELOXA_MODEL_REGION", "").strip() or None
    if name == "gemini":
        from .gemini import GeminiProvider

        provider: Provider = _PROVIDERS.setdefault("gemini", GeminiProvider())
    else:
        provider = ReplayProvider(Path(os.environ["VELOXA_MODEL_REPLAY_DIR"]))   # region "local"; a different
    # permitted region is refused by the client (residency), never silently matched
    return (TenantModelPolicy(name, os.environ["VELOXA_MODEL_NAME"].strip(), region, _csv("VELOXA_MODEL_STAGES"),
                              _csv("VELOXA_MODEL_ALLOWED_CLASSES"), ceiling), provider)


def agent_composer(conn):
    """The composer the agent runtime uses: the governed client when a provider is configured, otherwise the
    deterministic facts-only composer (the architecture's fallback, §22.1). Never an ungoverned model call."""
    from ..agents.model import FactsOnlyComposer
    from .client import GovernedLLMClient

    policy, provider = policy_and_provider()
    if provider is None or getattr(provider, "name", "") == "gemini":   # the experiment is wording-only, never agents
        return FactsOnlyComposer()
    return GovernedLLMClient(conn, policy, provider)
