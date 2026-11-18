"""Executive explanations: deterministic plain-English sections, and the guard on model wording (MOCKED; no network).

Every input is synthetic and invented for these tests.
"""

import uuid
from types import SimpleNamespace

import pytest

from veloxa import explain
from veloxa.agents.model import ModelResponse, ModelUnavailable

TID = uuid.uuid4()


@pytest.fixture(autouse=True)
def _synthetic_allowlist(monkeypatch):
    monkeypatch.setenv("VELOXA_GEMINI_TENANTS", "demo")       # the synthetic tenant the experiment may use
SYN = {"tenant_id": TID, "slug": "demo", "synthetic": True}

SCENARIO = {
    "scenario": "commitment_shift",
    "business_object": "Helix 2.0",
    "observed": [
        {
            "statement": "Helix 2.0 committed for 2026-12-01",
            "stated_date": "2026-12-01",
            "recorded_on": "2026-10-02",
            "evidence": ["jira:key:Key=HLX-1"],
        }
    ],
    "derived": [
        {
            "statement": "shifted date 2026-12-15",
            "calculation": "2026-12-01 + 14 day(s)",
            "evidence": ["jira:key:Key=HLX-1"],
        },
        {
            "statement": "29 day(s) from the assessment date 2026-11-16 (was 15)",
            "calculation": "2026-12-15 − 2026-11-16",
            "evidence": [],
        },
    ],
    "assumptions": ["Helix 2.0 moves by 14 day(s): a scenario input, not evidence"],
    "unknowns": [
        "open scope and throughput are not admitted: the required rate (C1 measure) is not recomputed",
        "no probability is given: the evidence gates of §16.4 are not met",
    ],
    "probability": None,
    "ranked": False,
}

WORRY = {
    "rank": 1,
    "tied": True,
    "item_id": "slip:Helix 2.0",
    "title": "Release Helix 2.0 moved 30 days later",
    "severity": {
        "low": 0.3,
        "high": 0.65,
        "measured_weight": 0.65,
        "criteria": {
            "C1": {"status": "measured", "reason": "release date moved from 2026-11-01 to 2026-12-01"},
            "C2": {"status": "missing", "reason": "no contract or revenue record ties an amount to this slip"},
            "C3": {"status": "not_applicable", "reason": "n/a"},
            "C4": {"status": "not_applicable", "reason": "n/a"},
        },
    },
    "confidence": "high",
    "evidence": ["jira:key:Key=HLX-1"],
    "unknowns": [],
    "sensitive": False,
    "follow_up": "Ask the release owner what changed for Helix 2.0.",
}

CONFLICT = {
    "conflict_id": "CF-0123456789ab",
    "subject": "Q3 2026",
    "predicate": "arr_usd",
    "resolution": "needs_review",
    "rule": None,
    "preferred_claim_id": None,
    "rationale": "Two formal documents report different values.",
    "uncertainty": "which figure is final is not recorded",
    "decision": None,
    "claims": [
        {
            "claim_id": "CL-a",
            "object": "5,774,852",
            "record_id": "board_reporting:q3#document",
            "valid_at": "2026-09-30",
        },
        {"claim_id": "CL-b", "object": "5,700,000", "record_id": "fp_and_a:q3#row=4", "valid_at": "2026-09-30"},
    ],
}


class Fake:
    def __init__(self, output=None, exc=None):
        self.output, self.exc, self.calls = output, exc, 0
        self.provider = SimpleNamespace(last_latency_ms=5, last_usage={})

    def call(self, req):
        self.calls += 1
        if self.exc:
            raise self.exc
        return ModelResponse("gemini/test", self.output, req.request_hash())


def echo(e: dict, summary: str = "Summary.", edit=None) -> dict:
    """A model response that repeats each item (optionally edited)."""
    claims = []
    for i, (_, it) in enumerate(explain.items_of(e)):
        text = it["text"]
        if edit and i in edit:
            text = edit[i]
        claims.append({"fact_id": f"C{i + 1}", "text": text})
    return {"summary": summary, "claims": claims}


def test_what_if_explains_commitment_change_result_implications_and_unknowns():
    e = explain.scenario(SCENARIO)
    keys = [s["key"] for s in e["sections"]]
    assert keys == ["commitment", "change", "result", "implications", "unknown"]
    basis = {s["key"]: s["basis"] for s in e["sections"]}
    assert basis["commitment"] == "recorded" and basis["change"] == "assumption" and basis["unknown"] == "unknown"
    assert "2026-12-15" in e["headline"] and e["probability"] is None
    result = next(s for s in e["sections"] if s["key"] == "result")["items"]
    assert result[0]["detail"] == "Calculation: 2026-12-01 + 14 day(s)" and result[0]["evidence"]
    unknown = " ".join(i["text"] for i in next(s for s in e["sections"] if s["key"] == "unknown")["items"]).lower()
    assert (
        "c1 measure" not in unknown and "§16.4" not in unknown and "throughput" not in unknown
    )  # jargon translated, meaning kept
    assert "cannot say whether the team could still deliver" in unknown and "no likelihood" in unknown


def test_a_scenario_without_a_date_says_it_cannot_calculate():
    s = {**SCENARIO, "derived": [], "unknowns": ["no stated commitment date: a shifted date cannot be derived"]}
    e = explain.scenario(s)
    assert "cannot calculate" in e["headline"] and "implications" not in [x["key"] for x in e["sections"]]


def test_worry_explains_ranking_without_raw_jargon_and_keeps_unknowns():
    e = explain.worry(WORRY, ranked=True, total_ranked=3)
    text = " ".join(it["text"] for _, it in explain.items_of(e))
    assert "rank 1" in text and "tied" in text and "not a probability" in text and "0.30 to 0.65" in text
    assert "revenue or contract value exposed is not known" in text.lower() and "never as zero" in text
    unranked = explain.worry({**WORRY, "reasons": ["only 35% of the weight is measured"]}, ranked=False)
    assert "not enough is measured to rank" in unranked["headline"]


def test_conflict_shows_both_values_and_the_open_uncertainty():
    e = explain.conflict(CONFLICT)
    text = " ".join(it["text"] for _, it in explain.items_of(e))
    assert "5,774,852" in text and "5,700,000" in text and "needs a person to review" in text
    assert e["confidence"] == "conflicting"
    assert [it["evidence"] for _, it in explain.items_of(e)][:2] == [
        ["board_reporting:q3#document"],
        ["fp_and_a:q3#row=4"],
    ]


def test_answers_abstentions_and_refusals_are_explained_plainly():
    ok = explain.answer(
        {
            "status": "qualified",
            "interpretation": "I read this as: headcount. Time: now.",
            "claims": [{"text": "People in Engineering: 18", "evidence": ["F1", "keka_hrms:x#1"]}],
            "facts": [{"fact_id": "F1", "kind": "derived"}],
            "unknowns": [],
            "qualifiers": [],
            "confidence": {"label": "High"},
        }
    )
    assert ok["headline"] == "You asked about headcount." and ok["sections"][0]["basis"] == "calculated"
    assert ok["sections"][0]["items"][0]["evidence"] == ["keka_hrms:x#1"]
    no = explain.answer({"status": "abstained", "claims": [], "unknowns": ["no forecast is admitted"]})
    assert no["status"] == "abstained" and no["headline"].startswith("Veloxa cannot answer")
    assert no["sections"][0]["title"] == "Why there is no answer"
    refused = explain.answer({"status": "refused", "refusal": {"reason": "individual pay is protected"}})
    assert refused["status"] == "refused" and "pay is protected" in refused["sections"][0]["items"][0]["text"]


def test_faithful_wording_is_accepted_and_items_are_never_modified():
    e = explain.scenario(SCENARIO)
    before = [dict(it) for _, it in explain.items_of(e)]
    out = explain.reword(e, SYN, Fake(echo(e, "If Helix 2.0 moves 14 days, the new date is 2026-12-15.")))
    assert out["wording"]["mode"] == "experimental_model", out["wording"]
    assert [dict(it) for _, it in explain.items_of(out)] == before


@pytest.mark.parametrize(
    "edit, summary, why",
    [
        ({2: "The new date would be 2026-12-20."}, "S.", "date was changed or dropped"),
        ({2: "The new date would be later."}, "S.", "date was changed or dropped"),
        ({3: "Some days from the assessment date 2026-11-16 (was 15)."}, "S.", "number was added, changed or dropped"),
        ({1: "Helix 2.0 moves by 14 day(s)."}, "S.", "assumption was worded as if"),
        ({5: "The team will deliver on time."}, "S.", "unknown was worded as if"),
        ({4: "The only effect is the new date for Helix 2.0, which will hurt revenue."}, "S.", "adds a cause"),
        (None, "This slip is likely to delay Acme.", "summary"),
    ],
)
def test_wording_that_changes_or_adds_to_the_facts_is_discarded(edit, summary, why):
    e = explain.scenario(SCENARIO)
    out = explain.reword(e, SYN, Fake(echo(e, summary, edit)))
    assert out["wording"]["mode"] == "facts_only" and out["wording"]["fallback"]
    assert any(why in r for r in out["wording"]["rejections"]), out["wording"]["rejections"]


def test_an_abstention_summary_may_not_sound_confident():
    e = explain.answer({"status": "abstained", "claims": [], "unknowns": ["no forecast is admitted"]})
    bad = explain.reword(e, SYN, Fake(echo(e, "Revenue grows next year.")))
    assert bad["wording"]["mode"] == "facts_only"
    good = explain.reword(
        explain.answer({"status": "abstained", "claims": [], "unknowns": ["no forecast is admitted"]}),
        SYN,
        Fake(echo(e, "Veloxa cannot answer this: there is no forecast in the records.")),
    )
    assert good["wording"]["mode"] == "experimental_model"


def test_refusals_failures_and_non_synthetic_tenants_never_produce_model_text():
    refused = explain.answer({"status": "refused", "refusal": "protected"})
    f = Fake({"summary": "x", "claims": []})
    assert explain.reword(refused, SYN, f)["wording"]["mode"] == "facts_only" and f.calls == 0
    for t in (
        {"tenant_id": TID, "slug": "veloxa", "synthetic": True},
        {"tenant_id": TID, "slug": "x", "synthetic": False},
    ):
        f = Fake({"summary": "x", "claims": []})
        assert explain.reword(explain.conflict(CONFLICT), t, f)["wording"]["mode"] == "facts_only" and f.calls == 0
    out = explain.reword(explain.conflict(CONFLICT), SYN, Fake(exc=ModelUnavailable("down")))
    assert out["wording"]["mode"] == "facts_only" and out["wording"]["fallback"]


def test_the_model_request_carries_the_evidence_data_classes():
    from veloxa.ask import wording

    e = explain.worry(WORRY, ranked=True, total_ranked=1)
    req = wording.request_for(explain.to_model_input(e, [{"record_id": "jira:x", "data_class": "internal"}]), TID)
    assert req.data_classes == frozenset({"internal"}) and req.prompt_version == "answer-wording/1"


def test_a_date_may_be_reformatted_but_never_changed_and_caveats_are_not_unknowns():
    e = explain.scenario(SCENARIO)
    ok = explain.reword(e, SYN, Fake(echo(e, "The new date would be December 15, 2026.",
                                          {2: "The new date would be 15 December 2026."})))
    assert ok["wording"]["mode"] == "experimental_model", ok["wording"]
    e = explain.scenario(SCENARIO)
    bad = explain.reword(e, SYN, Fake(echo(e, "S.", {2: "The new date would be December 16, 2026."})))
    assert any("date was changed" in r for r in bad["wording"]["rejections"])
    a = explain.answer({"status": "qualified", "claims": [{"text": "People: 18", "evidence": []}],
                        "qualifiers": ["Contractors are not included", "Facts only: rendered from verified facts"],
                        "unknowns": [], "interpretation": "I read this as: headcount."})
    secs = {s["key"]: [i["text"] for i in s["items"]] for s in a["sections"]}
    assert secs["caveat"] == ["Contractors are not included."] and "unknown" not in secs   # pipeline note dropped


def test_an_explanation_citing_no_record_is_labelled_internal_so_redaction_does_not_strip_it():
    from veloxa.ask import wording

    e = explain.answer({"status": "abstained", "claims": [], "unknowns": ["no forecast is admitted"]})
    req = wording.request_for(explain.to_model_input(e), TID)
    assert req.data_classes == frozenset({"internal"})
    assert req.messages[0]["facts"][0]["data_classes"] == ["internal"]


def test_readers_get_a_plain_notice_and_diagnostics_stay_separate(monkeypatch):
    from veloxa import explain as x

    e = x.scenario(SCENARIO)
    assert e["wording"]["notice"] == x.NOT_ENABLED and "DENY" not in e["wording"]["notice"]
    assert e["summary"].startswith("If Helix 2.0 moves") and "2026-12-15" in e["summary"]
    out = x.reword(x.conflict(CONFLICT), {"tenant_id": TID, "slug": "veloxa", "synthetic": True}, Fake())
    assert out["wording"]["notice"] == x.NOT_ENABLED and "veloxa" in out["wording"]["diagnostic"]
    bad = x.reword(x.scenario(SCENARIO), SYN, Fake(exc=ModelUnavailable("x")))
    assert bad["wording"]["notice"] == x.NOT_USED and "ModelUnavailable" in bad["wording"]["diagnostic"]
    abst = x.answer({"status": "abstained", "claims": [], "unknowns": ["no forecast is admitted"]})
    assert "cannot answer" in abst["summary"] and "no forecast" in abst["summary"].lower()
