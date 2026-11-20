"""Gemini adapter and experimental wording, with MOCKED responses (no network; CI-safe)."""

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from veloxa.agents.model import ModelResponse, ModelUnavailable
from veloxa.ask import wording
from veloxa.llm import gemini

ROOT = Path(__file__).resolve().parents[1]
TID = uuid.uuid4()
SYN = {"tenant_id": TID, "slug": "demo", "synthetic": True}


def answer(status="qualified"):
    return {"status": status, "interpretation": "I read this as: headcount.", "audit_id": "a1",
            "claims": [{"text": "People in the HR export: 17", "evidence": ["F1"]},
                       {"text": "People in the HR export with status Notice period: 1", "evidence": ["F2"]}],
            "evidence": [{"record_id": "keka_hrms:x#1", "data_class": "personal", "system": "keka_hrms"}],
            "confidence": {"label": "High"}, "unknowns": [], "qualifiers": []}


class FakeClient:
    def __init__(self, output=None, exc=None):
        self.output, self.exc, self.calls = output, exc, 0
        self.provider = SimpleNamespace(last_latency_ms=12, last_usage={"input_tokens": 5, "output_tokens": 3})

    def call(self, req):
        self.calls += 1
        if self.exc:
            raise self.exc
        return ModelResponse("gemini/test", self.output, req.request_hash())


GOOD = {"summary": "Seventeen people are in the HR export; 1 is in a notice period.",
        "claims": [{"fact_id": "C1", "text": "The HR export lists 17 people."},
                   {"fact_id": "C2", "text": "1 person is in their notice period."}]}


def test_valid_wording_is_attached_as_experimental_and_facts_are_unchanged(monkeypatch):
    monkeypatch.setenv("VELOXA_GEMINI_TENANTS", "demo")
    a = answer()
    out = wording.reword(None, SYN, a, FakeClient(GOOD))
    assert out["wording"]["mode"] == "experimental_model" and out["wording"]["label"].startswith("Experimental")
    assert out["claims"] == a["claims"] and out["evidence"] == a["evidence"] and out["confidence"] == a["confidence"]


@pytest.mark.parametrize("bad, why", [
    ({"summary": "17 people.", "claims": [{"fact_id": "C1", "text": "The HR export lists 18 people."},
                                          {"fact_id": "C2", "text": "1 person."}]}, "a number not in the verified"),
    ({"summary": "17 people.", "claims": [{"fact_id": "C1", "text": "17 people work at Acme Corp."},
                                          {"fact_id": "C2", "text": "1 person."}]}, "new names"),
    ({"summary": "17 people.", "claims": [{"fact_id": "C1", "text": "17 people."}]}, "fact ids"),
    ({"summary": "17 people.", "claims": [{"fact_id": "C1", "text": "17 people; you should hire more."},
                                          {"fact_id": "C2", "text": "1 person."}]}, "advice"),
    ({"claims": []}, "schema"),
])
def test_unsupported_or_altered_wording_is_rejected_and_falls_back(monkeypatch, bad, why):
    monkeypatch.setenv("VELOXA_GEMINI_TENANTS", "demo")
    out = wording.reword(None, SYN, answer(), FakeClient(bad))
    assert out["wording"]["mode"] == "facts_only" and out["wording"]["fallback"]
    assert any(why in r for r in out["wording"]["rejections"])


@pytest.mark.parametrize("status", ["refused", "abstained", "conflict"])
def test_refusals_unknowns_and_conflicts_never_reach_the_model(monkeypatch, status):
    monkeypatch.setenv("VELOXA_GEMINI_TENANTS", "demo")
    fake = FakeClient(GOOD)
    out = wording.reword(None, SYN, answer(status), fake)
    assert fake.calls == 0 and out["wording"]["mode"] == "facts_only" and out["status"] == status


def test_the_supplied_company_and_non_synthetic_tenants_are_refused_before_any_call(monkeypatch):
    monkeypatch.setenv("VELOXA_GEMINI_TENANTS", "demo,veloxa,real")
    for t in ({"tenant_id": TID, "slug": "veloxa", "synthetic": True},       # the Day-folder company: by name
              {"tenant_id": TID, "slug": "real", "synthetic": False},
              {"tenant_id": TID, "slug": "other", "synthetic": True}):        # not allowlisted
        fake = FakeClient(GOOD)
        assert wording.reword(None, t, answer(), fake)["wording"]["mode"] == "facts_only" and fake.calls == 0


def test_provider_failure_and_quota_fall_back_without_another_model(monkeypatch):
    monkeypatch.setenv("VELOXA_GEMINI_TENANTS", "demo")
    for exc in (ModelUnavailable("x"), gemini.QuotaExceeded("429")):
        out = wording.reword(None, SYN, answer(), FakeClient(exc=exc))
        assert out["wording"]["mode"] == "facts_only" and out["wording"]["fallback"]


def test_adapter_classifies_429_as_quota_and_never_retries_it():
    class E(Exception):
        code = 429

    p = gemini.GeminiProvider(client_factory=lambda: SimpleNamespace(models=SimpleNamespace(
        generate_content=lambda **_: (_ for _ in ()).throw(E()))))
    req = wording.request_for(answer(), TID)
    with pytest.raises(gemini.QuotaExceeded):
        p.complete("gemini-3.1-flash-lite", req)


def test_adapter_parses_json_and_reports_usage_and_enforces_the_daily_cap():
    resp = SimpleNamespace(text='{"summary": "s", "claims": []}',
                           usage_metadata=SimpleNamespace(prompt_token_count=7, candidates_token_count=2))
    p = gemini.GeminiProvider(meter=gemini.UsageMeter(daily_cap=1), client_factory=lambda: SimpleNamespace(
        models=SimpleNamespace(generate_content=lambda **_: resp)))
    out, tokens = p.complete("m", wording.request_for(answer(), TID))
    assert out == {"summary": "s", "claims": []} and tokens == 9 and p.last_usage == {"input_tokens": 7,
                                                                                     "output_tokens": 2}
    with pytest.raises(gemini.QuotaExceeded):
        p.complete("m", wording.request_for(answer(), TID))


def test_the_key_never_appears_in_source_or_the_frontend():
    for path in list((ROOT / "src").rglob("*.py")) + list((ROOT / "frontend" / "src").rglob("*.ts*")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert "VITE_GEMINI" not in text and "AIza" not in text
    assert "GEMINI" not in "".join(p.read_text(encoding="utf-8") for p in (ROOT / "frontend" / "src").rglob("*.ts*"))


def test_gemini_is_configurable_only_with_a_backend_key_and_never_composes_agent_turns(monkeypatch):
    from veloxa.agents.model import FactsOnlyComposer
    from veloxa.llm import config as llm_config

    env = {"VELOXA_MODEL_PROVIDER": "gemini", "VELOXA_MODEL_NAME": "gemini-3.1-flash-lite",
           "VELOXA_MODEL_STAGES": "compose", "VELOXA_MODEL_ALLOWED_CLASSES": "internal",
           "VELOXA_MODEL_MAX_ESTIMATE_TOKENS": "2000"}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert llm_config.status() == {"model": "disabled", "reason": "incomplete configuration",
                                   "missing": ["GEMINI_API_KEY"]}
    monkeypatch.setenv("GEMINI_API_KEY", "test-value-not-a-key")
    st = llm_config.status()
    assert st["model"] == "configured" and "test-value-not-a-key" not in str(st)
    policy, provider = llm_config.policy_and_provider()
    assert provider.name == "gemini" and policy.stages == frozenset({"compose"})
    assert isinstance(llm_config.agent_composer(None), FactsOnlyComposer)      # agents stay facts-only


def test_the_adapter_refuses_any_prompt_other_than_the_wording_task():
    from dataclasses import replace

    req = replace(wording.request_for(answer(), TID), prompt_version="agent-turn/1")
    with pytest.raises(gemini.TenantNotPermitted):
        gemini.GeminiProvider(client_factory=lambda: None).complete("m", req)
