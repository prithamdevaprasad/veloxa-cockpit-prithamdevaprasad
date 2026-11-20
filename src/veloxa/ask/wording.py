"""Experimental model WORDING of a verified answer (owner-approved Gemini experiment; synthetic tenants only).

Flow: the deterministic answer (retrieval, facts, citations, confidence) is complete BEFORE this runs. The model may
only re-word it. Refusals, "I don't know" and unresolved conflicts bypass the model entirely. The model's output is
accepted only if a strict deterministic check passes; otherwise the facts-only answer is shown and the reason is
recorded. The model never changes claims, citations, confidence, status or refusals: its text is attached as
`wording` (labelled experimental) next to the unchanged facts-only answer.

Deterministic acceptance check (`check`):
- valid JSON with the fixed schema; exactly the same fact ids as the verified claims, each once;
- every number in a re-worded claim appears in that claim's verified text; every number in the summary appears in
  some verified claim;
- every capitalised word that is not sentence-initial appears in the verified text (no new names or entities);
- markers of hedging or advice the facts do not contain ("probably", "should") are rejected.
"""

from __future__ import annotations

import re
import uuid

from ..agents.model import ModelOutputInvalid, ModelRequest, ModelUnavailable

PROMPT_VERSION = "answer-wording/1"
SCHEMA = {"type": "object", "required": ["summary", "claims"],
          "properties": {"summary": {"type": "string"}, "follow_ups": {"type": "array", "items": {"type": "string"}},
                         "claims": {"type": "array", "items": {"type": "object", "required": ["fact_id", "text"]}}}}
BYPASS = {"refused", "abstained", "conflict"}
_NUM = re.compile(r"\d+(?:[.,]\d+)*")
_CAP = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-zA-Z]{2,}\b")
_FORBIDDEN = re.compile(r"\b(probably|likely|should|must|recommend|I think|guess)\b", re.I)


def _clean(t: str) -> str:
    return t.replace("⟦", "").replace("⟧", "")


def _nums(t: str) -> set[str]:
    return {n.replace(",", "") for n in _NUM.findall(t)}


def request_for(result: dict, tenant_id: uuid.UUID) -> ModelRequest:
    classes = {e["data_class"] for e in result.get("evidence", [])}
    facts = [{"fact_id": f"C{i + 1}", "text": _clean(c["text"]), "data_classes": sorted(classes) or ["unknown"]}
             for i, c in enumerate(result["claims"])]
    msg = {"role": "user", "question_interpretation": result.get("interpretation", ""), "facts": facts,
           "confidence": result.get("confidence", {}).get("label"),
           "caveats": result.get("unknowns", [])[:3] + [q for q in result.get("qualifiers", [])[:3]]}
    return ModelRequest(tenant_id, "compose", frozenset(classes), SCHEMA, PROMPT_VERSION, [msg],
                        budget_estimate_tokens=1500, idempotency_key=f"wording:{result.get('audit_id', uuid.uuid4())}")


def check(output: dict, result: dict) -> list[str]:
    """Reasons to REJECT the model output (empty = accepted)."""
    if not isinstance(output, dict) or output.get("_invalid") or not isinstance(output.get("claims"), list) \
            or not isinstance(output.get("summary"), str):
        return ["output does not match the schema"]
    original = {f"C{i + 1}": _clean(c["text"]) for i, c in enumerate(result["claims"])}
    got = {c.get("fact_id"): str(c.get("text", "")) for c in output["claims"] if isinstance(c, dict)}
    why = []
    if set(got) != set(original) or len(output["claims"]) != len(original):
        why.append("fact ids changed, missing or duplicated")
    all_text = " ".join(original.values())
    for fid, text in got.items():
        src = original.get(fid, "")
        if not _nums(text) <= _nums(src):
            why.append(f"{fid}: a number not in the verified fact")
        new = {w for w in _CAP.findall(text) if w.lower() not in src.lower()}
        if new:
            why.append(f"{fid}: new names or entities {sorted(new)[:3]}")
        if _FORBIDDEN.search(text) and not _FORBIDDEN.search(src):
            why.append(f"{fid}: adds hedging or advice")
    if not _nums(output["summary"]) <= _nums(all_text):
        why.append("summary: a number not in any verified fact")
    new = {w for w in _CAP.findall(output["summary"]) if w.lower() not in all_text.lower()}
    if new:
        why.append(f"summary: new names or entities {sorted(new)[:3]}")
    return why


def reword(conn, tenant: dict, result: dict, client) -> dict:
    """Return `result` with a `wording` block. `tenant` = {"tenant_id", "slug", "synthetic"}. Never raises."""
    from ..llm.gemini import QuotaExceeded, TenantNotPermitted, check_tenant

    out = dict(result)
    if result.get("status") in BYPASS or not result.get("claims"):
        out["wording"] = {"mode": "facts_only", "reason": f"bypassed for status {result.get('status')}"}
        return out
    try:
        check_tenant(tenant["slug"], tenant["synthetic"])
    except TenantNotPermitted as e:
        out["wording"] = {"mode": "facts_only", "reason": str(e)}
        return out
    try:
        resp = client.call(request_for(result, tenant["tenant_id"]))
    except (ModelUnavailable, ModelOutputInvalid, QuotaExceeded) as e:
        out["wording"] = {"mode": "facts_only", "reason": f"model unavailable ({type(e).__name__})", "fallback": True}
        return out
    reasons = check(resp.output, result)
    provider = getattr(client, "provider", None)
    meta = {"latency_ms": getattr(provider, "last_latency_ms", None), "usage": getattr(provider, "last_usage", {})}
    if reasons:
        out["wording"] = {"mode": "facts_only", "reason": "model wording rejected by the deterministic check",
                          "rejections": reasons[:5], "fallback": True, **meta}
        return out
    out["wording"] = {"mode": "experimental_model", "label": "Experimental: worded by a model from the verified "
                      "facts below; facts, citations and confidence are unchanged", "model": resp.model,
                      "summary": resp.output["summary"],
                      "claims": {c["fact_id"]: c["text"] for c in resp.output["claims"]},
                      "follow_ups": [f for f in resp.output.get("follow_ups", []) if isinstance(f, str)][:3], **meta}
    return out
