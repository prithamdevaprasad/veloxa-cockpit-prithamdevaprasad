"""C27 configuration from the environment: DENY_ALL unless fully configured with an IMPLEMENTED adapter."""

from veloxa.agents.model import FactsOnlyComposer
from veloxa.llm import config
from veloxa.llm.client import DENY_ALL, GovernedLLMClient

VARS = ("VELOXA_MODEL_PROVIDER", "VELOXA_MODEL_NAME", "VELOXA_MODEL_REGION", "VELOXA_MODEL_STAGES",
        "VELOXA_MODEL_ALLOWED_CLASSES", "VELOXA_MODEL_MAX_ESTIMATE_TOKENS", "VELOXA_MODEL_REPLAY_DIR")


def clear(monkeypatch):
    for v in VARS:
        monkeypatch.delenv(v, raising=False)


def test_nothing_configured_means_deny_all_and_facts_only(monkeypatch):
    clear(monkeypatch)
    assert config.status()["model"] == "disabled" and "DENY_ALL" in config.status()["reason"]
    assert config.policy_and_provider() == (DENY_ALL, None)
    assert isinstance(config.agent_composer(None), FactsOnlyComposer)


def test_unknown_or_incomplete_providers_stay_disabled(monkeypatch):
    clear(monkeypatch)
    monkeypatch.setenv("VELOXA_MODEL_PROVIDER", "some-vendor")
    s = config.status()
    assert s["model"] == "disabled" and "no implemented adapter" in s["reason"]
    assert s["implemented_adapters"] == ["replay", "gemini"]   # gemini: owner-approved synthetic experiment
    monkeypatch.setenv("VELOXA_MODEL_PROVIDER", "replay")
    s = config.status()
    assert s["model"] == "disabled" and "VELOXA_MODEL_NAME" in s["missing"]
    assert config.policy_and_provider() == (DENY_ALL, None)


def test_a_complete_replay_configuration_uses_the_governed_client_and_status_shows_no_values(monkeypatch, tmp_path):
    clear(monkeypatch)
    for k, v in {"VELOXA_MODEL_PROVIDER": "replay", "VELOXA_MODEL_NAME": "recorded", "VELOXA_MODEL_STAGES": "agent",
                 "VELOXA_MODEL_ALLOWED_CLASSES": "internal,personal", "VELOXA_MODEL_MAX_ESTIMATE_TOKENS": "2000",
                 "VELOXA_MODEL_REPLAY_DIR": str(tmp_path / "syn-secret-dir")}.items():
        monkeypatch.setenv(k, v)
    s = config.status()
    assert s["model"] == "configured" and "syn-secret-dir" not in str(s)
    policy, provider = config.policy_and_provider()
    assert policy.provider == "replay" and policy.stages == frozenset({"agent"}) and provider.name == "replay"
    assert isinstance(config.agent_composer(None), GovernedLLMClient)
    monkeypatch.setenv("VELOXA_MODEL_MAX_ESTIMATE_TOKENS", "lots")
    assert config.policy_and_provider() == (DENY_ALL, None)                  # malformed: disabled, not guessed
