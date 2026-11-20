"""Run the Orbitra expected-answer conversation suite against a running Synthetic Company Lab API.

    python scripts/run_conversations.py --port 8790 --out ../../SYNTHETIC_CONVERSATION_RESULTS.json

Refuses to run unless the API's workspace is the synthetic `orbitra` tenant (never the supplied company).
Exit code 0 only when every case passes. Prints per-category pass rates and every failure.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--token", default="synthetic-orbitra-lab-fixture-token-0123456789")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    H = {"Authorization": f"Bearer {args.token}", "Content-Type": "application/json", "X-Veloxa-Request": "1"}

    def call(m, p, b=None):
        r = urllib.request.Request(f"http://127.0.0.1:{args.port}{p}", method=m, headers=H,
                                   data=None if b is None else json.dumps(b).encode())
        try:
            with urllib.request.urlopen(r, timeout=120) as x:  # noqa: S310  (fixed http://127.0.0.1 URL)
                return x.status, json.loads(x.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    _, me = call("GET", "/v1/me")
    if not me or me.get("tenant", {}).get("slug") != "orbitra" or not me["tenant"].get("synthetic"):
        print("refused: this suite runs only against the synthetic Orbitra workspace")
        return 2
    from synthetic_company import conversations, orbitra

    facts = orbitra.generate(Path(tempfile.mkdtemp(prefix="orbitra-facts-")))["facts"]

    def check(exp, out):
        text = " ".join([out.get("answer") or out.get("reply") or ""] + [c["text"] for c in out.get("claims", [])])
        text = text.replace("⟦", "").replace("⟧", "").lower()
        problems = []
        if out.get("status") not in exp["status"]:
            problems.append(f"status {out.get('status')} not in {list(exp['status'])}")
        if exp["has"] and not all(h.lower() in text for h in exp["has"]):
            problems.append(f"missing {[h for h in exp['has'] if h.lower() not in text]}")
        if any(x.lower() in text for x in exp["lacks"]):
            problems.append(f"contains {[x for x in exp['lacks'] if x.lower() in text]}")
        if exp.get("system") and exp["system"] not in {e["system"] for e in out.get("evidence", [])}:
            problems.append(f"no evidence from {exp['system']}")
        return problems

    results, lat = [], []
    for case in conversations.cases(facts):
        if case["kind"] == "ask":
            body = {"question": case["question"]} | ({"as_known_at": case["as_known_at"]} if case["as_known_at"]
                                                     else {})
            t0 = time.perf_counter()
            code, out = call("POST", "/v1/ask", body)
            lat.append(time.perf_counter() - t0)
            probs = [f"HTTP {code}"] if code != 200 else check(case, out)
            results.append({"id": case["id"], "category": case["category"], "question": case["question"],
                            "passed": not probs, "problems": probs, "status": (out or {}).get("status")})
        else:
            _, s = call("POST", f"/v1/agents/{case['agent']}/sessions", {})
            probs = []
            for t in case["turns"]:
                t0 = time.perf_counter()
                code, out = call("POST", f"/v1/sessions/{s['session_id']}/messages",
                                 {"message": t["q"], "idempotency_key": str(uuid.uuid4())})
                lat.append(time.perf_counter() - t0)
                p = [f"HTTP {code}"] if code != 200 else check(t, out)
                probs += [f"[{t['q']}] {x}" for x in p]
            results.append({"id": case["id"], "category": case["category"], "question": " / ".join(
                t["q"] for t in case["turns"]), "passed": not probs, "problems": probs})
    by = defaultdict(list)
    for r in results:
        by[r["category"]].append(r["passed"])
    turns = sum(len(c["turns"]) if c["kind"] == "agent" else 1 for c in conversations.cases(facts))
    print(f"{sum(r['passed'] for r in results)}/{len(results)} cases passed ({turns} conversational turns)")
    for k, v in sorted(by.items()):
        print(f"  {k:15} {sum(v)}/{len(v)}")
    for r in results:
        if not r["passed"]:
            print(f"FAIL {r['id']} {r['question'][:70]!r}: {'; '.join(r['problems'])[:260]}")
    lat.sort()
    summary = {"cases": len(results), "passed": sum(r["passed"] for r in results), "turns": turns,
               "by_category": {k: f"{sum(v)}/{len(v)}" for k, v in by.items()},
               "latency_s": {"p50": round(lat[len(lat) // 2], 3), "p95": round(lat[int(len(lat) * .95)], 3)},
               "results": results}
    if args.out:
        Path(args.out).write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return 0 if summary["passed"] == summary["cases"] else 1


if __name__ == "__main__":
    sys.exit(main())
