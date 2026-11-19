"""Gemini evaluation for conversation understanding and response composition, SYNTHETIC Orbitra data ONLY.

    python scripts/gemini_conversation_eval.py --env-file <.env with GEMINI_API_KEY> --port 8790 --out results.json

- Input is only the Orbitra conversation suite (invented questions) and answers from the running Orbitra lab; the
  script refuses to run unless that API's workspace is the synthetic `orbitra` tenant. Nothing from the supplied
  company (D1-D4) is read or sent.
- Understanding: Gemini labels each question with one intent from a fixed set; compared with the expected label
  and with Veloxa's deterministic classifier.
- Composition: Gemini rewords a deterministic synthetic answer; a deterministic check requires every number and
  date to survive unchanged (otherwise the rewording would be rejected and the facts-only text kept).
- The key is read from the env file into this process only; it is never printed or written.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
LABELS = ["greeting", "help", "thanks", "updates", "blockers", "follow_up", "evidence", "memory", "business",
          "privacy_refusal", "jira_action", "missing_or_unknown"]
CATEGORY_LABEL = {"business": "business", "conflict": "business", "historical": "business", "privacy":
                  "privacy_refusal", "missing": "missing_or_unknown", "jira": "jira_action"}
TURN_LABEL = {"Hi": "greeting", "Good morning": "greeting", "hello there!": "greeting", "Thanks!": "thanks",
              "What can you do?": "help", "Any updates?": "updates", "What's blocking the work?": "blockers",
              "Tell me more": "follow_up", "Show evidence": "evidence", "What did I ask you earlier?": "memory",
              "Can you explain that in simpler terms?": "follow_up", "What is the CEO's salary?": "privacy_refusal"}
DET = {"greeting": "greeting", "help": "help", "thanks": "thanks", "updates": "updates", "blockers": "blockers",
       "more": "follow_up", "simpler": "follow_up", "evidence": "evidence", "memory": "memory"}


def load_key(path: Path) -> None:
    """Into this process's environment only (the client reads GEMINI_API_KEY itself); never printed."""
    import os

    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("GEMINI_API_KEY="):
            os.environ["GEMINI_API_KEY"] = line.split("=", 1)[1].strip().strip('"').strip("'")
            return
    raise SystemExit("GEMINI_API_KEY not found in the env file")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", type=Path, required=True)
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--token", default="synthetic-orbitra-lab-fixture-token-0123456789")
    ap.add_argument("--model", default="gemini-3.1-flash-lite")
    ap.add_argument("--pause", type=float, default=4.5, help="seconds between calls (free-tier rate limit)")
    ap.add_argument("--compose", type=int, default=20)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--limit", type=int, default=0, help="first N understanding items only (smoke test)")
    args = ap.parse_args()
    H = {"Authorization": f"Bearer {args.token}", "Content-Type": "application/json", "X-Veloxa-Request": "1"}

    def call(m, p, b=None):
        r = urllib.request.Request(f"http://127.0.0.1:{args.port}{p}", method=m, headers=H,
                                   data=None if b is None else json.dumps(b).encode())
        with urllib.request.urlopen(r, timeout=120) as x:  # noqa: S310  (fixed http://127.0.0.1 URL)
            return json.loads(x.read() or b"null")

    me = call("GET", "/v1/me")
    if me.get("tenant", {}).get("slug") != "orbitra" or not me["tenant"].get("synthetic"):
        print("refused: synthetic Orbitra workspace only")
        return 2
    from google import genai
    from synthetic_company import conversations, orbitra

    from veloxa.agents import conversation as conv

    load_key(args.env_file)
    from google.genai import types

    client = genai.Client(http_options=types.HttpOptions(timeout=20_000))       # 20 s per call, never hang
    facts = orbitra.generate(Path(tempfile.mkdtemp(prefix="orbitra-facts-")))["facts"]
    items = []
    for c in conversations.cases(facts):
        if c["kind"] == "ask":
            items.append((c["question"], CATEGORY_LABEL[c["category"]]))
        else:
            items += [(t["q"], TURN_LABEL[t["q"]]) for t in c["turns"] if t["q"] in TURN_LABEL]
    items = list(dict.fromkeys(items))
    if args.limit:
        items = items[: args.limit]

    def gemini(prompt: str) -> tuple[str | None, float, str | None]:
        t0 = time.perf_counter()
        try:
            r = client.models.generate_content(model=args.model, contents=prompt)
            return (r.text or "").strip(), time.perf_counter() - t0, None
        except Exception as e:  # noqa: BLE001 - record the failure class, never the key
            return None, time.perf_counter() - t0, type(e).__name__
        finally:
            time.sleep(args.pause)

    nlu = []
    for q, want in items:
        text, dt, err = gemini("Classify the user's message to a company-intelligence assistant into exactly one "
                               f"label from this list: {', '.join(LABELS)}. Reply with the label only.\n"
                               f"Message: {q}")
        got = (text or "").lower().strip().strip(".")
        det = DET.get(conv.intent(q))
        print(f"nlu {len(nlu) + 1}/{len(items)} {'error ' + err if err else 'ok'} {dt:.1f}s", flush=True)
        nlu.append({"q": q, "want": want, "gemini": got, "gemini_ok": got == want, "latency": dt, "error": err,
                    "deterministic": det, "det_ok": det == want if det else None})
    comp = []
    for q in [i[0] for i in items if i[1] == "business"][: args.compose]:
        a = call("POST", "/v1/ask", {"question": q})
        base = a.get("answer") or ""
        if not base or a.get("status") not in ("answered", "qualified"):
            continue
        text, dt, err = gemini("Rewrite this answer for a CEO in plain, friendly English. Keep every number, date and "
                               "name exactly as written; add nothing.\n\n" + base)
        need = set(re.findall(r"\d[\d,.\-]*\d|\d", base))
        kept = text is not None and all(n in text for n in need)
        comp.append({"q": q, "latency": dt, "error": err, "facts_preserved": kept, "len_before": len(base),
                     "len_after": len(text or "")})
    ok = [r for r in nlu if not r["error"]]
    summary = {
        "model": args.model, "data": "synthetic Orbitra only", "nlu_items": len(nlu),
        "nlu_errors": sum(bool(r["error"]) for r in nlu),
        "gemini_label_accuracy": round(sum(r["gemini_ok"] for r in ok) / len(ok), 3) if ok else None,
        "deterministic_conversational_accuracy": round(
            sum(r["det_ok"] for r in nlu if r["det_ok"] is not None) / max(1, sum(r["det_ok"] is not None
                                                                                  for r in nlu)), 3),
        "nlu_latency_p50_s": round(statistics.median([r["latency"] for r in ok]), 2) if ok else None,
        "composition_items": len(comp), "composition_errors": sum(bool(r["error"]) for r in comp),
        "composition_facts_preserved": sum(r["facts_preserved"] for r in comp),
        "composition_latency_p50_s": round(statistics.median([r["latency"] for r in comp]), 2) if comp else None,
        "misclassified": [(r["q"], r["want"], r["gemini"]) for r in ok if not r["gemini_ok"]][:25],
    }
    print(json.dumps({k: v for k, v in summary.items() if k != "misclassified"}, indent=1))
    for q, w, g in summary["misclassified"]:
        print(f"  gemini mislabel: {q[:60]!r} want={w} got={g}")
    if args.out:
        args.out.write_text(json.dumps(summary | {"nlu": nlu, "composition": comp}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
