"""Local load and concurrency probe against a RUNNING Veloxa API, normally the synthetic demo (scripts/demo_env.py).

LOCAL MEASUREMENT ONLY. It is not a capacity claim or an SLA: one laptop, one process, one synthetic tenant.
It never targets anything but the loopback address it is given.

    python scripts/load_test.py --base http://127.0.0.1:8765 --clients 8 --requests 40

**Phases:**
1. **read:** concurrent GETs of /health, /v1/today, /v1/conflicts, /v1/timeline, /v1/dependencies.
2. **ask:** concurrent POST /v1/ask (an expensive path; deterministic composition, since no model is configured).
3. **burst:** one client sends `--burst` rapid asks. With the gateway's expensive bucket, every request beyond the
   limit must be 429 with Retry-After, never a 5xx.

Prints JSON: per-phase count, status histogram, p50, p95 and max latency in ms, and throughput.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import httpx

DEMO_TOKEN = "synthetic-demo-fixture-token-0123456789abcdef"  # noqa: S105 - the documented local demo fixture
READS = ["/health", "/v1/today", "/v1/conflicts", "/v1/timeline", "/v1/dependencies"]


def _one(client: httpx.Client, method: str, path: str, body=None, token: str = DEMO_TOKEN):
    t = time.perf_counter()
    r = client.request(method, path, json=body, headers={"Authorization": f"Bearer {token}"})
    return r.status_code, (time.perf_counter() - t) * 1000, r.headers.get("retry-after")


def _summary(name: str, results: list, seconds: float) -> dict:
    ms = sorted(r[1] for r in results)
    hist: dict[str, int] = {}
    for r in results:
        hist[str(r[0])] = hist.get(str(r[0]), 0) + 1
    return {"phase": name, "requests": len(results), "status": hist,
            "p50_ms": round(statistics.median(ms), 1) if ms else None,
            "p95_ms": round(ms[min(len(ms) - 1, int(len(ms) * 0.95))], 1) if ms else None,
            "max_ms": round(ms[-1], 1) if ms else None, "seconds": round(seconds, 2),
            "per_second": round(len(results) / seconds, 1) if seconds else None}


def run(base: str, clients: int, per_client: int, burst: int) -> list[dict]:
    if urlsplit(base).hostname not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("load_test targets loopback only")
    out = []
    with httpx.Client(base_url=base, timeout=60) as c:
        c.get("/health").raise_for_status()

        def reads(i):
            return [_one(c, "GET", READS[(i + j) % len(READS)]) for j in range(per_client)]

        def asks(i):
            return [_one(c, "POST", "/v1/ask", {"question": "What are the open conflicts?"},
                         token=DEMO_TOKEN) for _ in range(max(1, per_client // 4))]

        for name, fn in (("read", reads), ("ask", asks)):
            t = time.perf_counter()
            with ThreadPoolExecutor(clients) as pool:
                res = [r for chunk in pool.map(fn, range(clients)) for r in chunk]
            out.append(_summary(name, res, time.perf_counter() - t))
        if burst:
            t = time.perf_counter()
            with ThreadPoolExecutor(min(burst, 16)) as pool:
                res = list(pool.map(lambda _: _one(c, "POST", "/v1/ask", {"question": "What changed?"}), range(burst)))
            s = _summary("burst", res, time.perf_counter() - t)
            s["limited_with_retry_after"] = sum(1 for r in res if r[0] == 429 and r[2])
            s["server_errors"] = sum(1 for r in res if r[0] >= 500)
            out.append(s)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8765")
    ap.add_argument("--clients", type=int, default=8)
    ap.add_argument("--requests", type=int, default=40, help="read requests per client")
    ap.add_argument("--burst", type=int, default=0, help="rapid asks from one client (rate-limit check)")
    args = ap.parse_args()
    results = run(args.base, args.clients, args.requests, args.burst)
    print(json.dumps(results, indent=1))
    return 1 if any(r.get("server_errors") or any(k.startswith("5") for k in r["status"]) for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
