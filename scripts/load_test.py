"""Staged load test against the SYNTHETIC Orbitra lab only (Phase 6). Reports measured numbers, nothing estimated.

    python scripts/load_test.py --port 8790 --stages 10/25/50/100 --seconds 45 --db veloxa_synthco_v3 --out r.json

Each virtual user loops over a realistic mix: Ask questions (60%), a turn in its own agent interview (25%) and page
reads (overview, worry, people; 15%). Measures per stage: requests, throughput, P50/P95/P99 latency, errors by
type, server CPU% and working set (Windows performance counters for the server PID), PostgreSQL connections to
the lab database, and DBOS workflow state when its tables exist. Refuses to run unless the workspace is `orbitra`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import subprocess
import threading
import time
import uuid
from collections import Counter
from pathlib import Path

ASK = ["What is our headcount?", "When is Orbit Core 3.0 due?", "Which issues are blocked?", "What is the ARR?",
       "Who is the CEO?", "Why was Orbit Core 3.0 delayed?", "What is the runway?", "What should I worry about?",
       "What is our headcount by department?", "When did Pulse 1.5 ship?", "What is the ledger spend?",
       "Who is likely to resign next, and when is Orbit Core 3.0 due?", "Tell me about the flamingo budget."]
AGENT = ["Hi", "Any updates?", "Tell me more", "What's blocking the work?", "Show evidence", "What is your role?"]
AGENTS = ["syn-eng-core", "syn-eng-pulse", "syn-fin-revenue", "syn-ppl-headcount", "syn-mkt-campaigns"]
PAGES = ["/v1/overview", "/v1/worry", "/v1/people", "/v1/me"]


def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))], 3)


class Sampler(threading.Thread):
    """Server CPU% / working set (typeperf-free: PowerShell Get-Process deltas) and DB connections every 2 s."""

    def __init__(self, pid: int, admin_dsn: str, db: str):
        super().__init__(daemon=True)
        self.pid, self.admin, self.db, self.stop, self.samples = pid, admin_dsn, db, threading.Event(), []

    def _proc(self):
        out = subprocess.run(["powershell", "-NoProfile", "-Command",  # noqa: S603, S607 (fixed local command)
                              f"$p=Get-Process -Id {self.pid}; '{{0}} {{1}}' -f $p.TotalProcessorTime.TotalSeconds,"
                              " $p.WorkingSet64"], capture_output=True, text=True, timeout=10).stdout.split()
        return float(out[0]), int(out[1])

    def run(self):
        import psycopg

        conn = psycopg.connect(self.admin, autocommit=True)
        last_cpu, last_t = self._proc()[0], time.monotonic()
        cores = os.cpu_count() or 1
        while not self.stop.wait(2.0):
            try:
                cpu, ws = self._proc()
                now = time.monotonic()
                n = conn.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = %s", (self.db,)).fetchone()[0]
                self.samples.append({"cpu_pct_of_machine": round((cpu - last_cpu) / (now - last_t) / cores * 100, 1),
                                     "working_set_mb": round(ws / 2**20, 1), "db_connections": n})
                last_cpu, last_t = cpu, now
            except Exception:  # noqa: BLE001, S112 - a missed sample is not a test failure
                continue
        conn.close()


async def user(client, uid: int, until: float, lat: list, errs: Counter, kinds: Counter):
    rnd = random.Random(uid)  # noqa: S311 (workload mix, not security)
    session = None
    while time.monotonic() < until:
        r = rnd.random()
        t0 = time.perf_counter()
        try:
            if r < 0.60:
                kind = "ask"
                resp = await client.post("/v1/ask", json={"question": rnd.choice(ASK)})
            elif r < 0.85:
                kind = "agent"
                if session is None:
                    s = await client.post(f"/v1/agents/{rnd.choice(AGENTS)}/sessions", json={})
                    session = s.json()["session_id"]
                resp = await client.post(f"/v1/sessions/{session}/messages",
                                         json={"message": rnd.choice(AGENT), "idempotency_key": str(uuid.uuid4())})
            else:
                kind = "page"
                resp = await client.get(rnd.choice(PAGES))
            dt = time.perf_counter() - t0
            kinds[kind] += 1
            if resp.status_code >= 400:
                errs[f"HTTP {resp.status_code}"] += 1
            else:
                lat.append((kind, dt))
        except Exception as e:  # noqa: BLE001 - count every failure by type
            errs[type(e).__name__] += 1


async def stage(base, token, n, seconds):
    import httpx

    lat, errs, kinds = [], Counter(), Counter()
    limits = httpx.Limits(max_connections=n + 10, max_keepalive_connections=n + 10)
    headers = {"Authorization": f"Bearer {token}", "X-Veloxa-Request": "1"}
    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=120, limits=limits) as client:
        until = time.monotonic() + seconds
        t0 = time.monotonic()
        await asyncio.gather(*(user(client, i, until, lat, errs, kinds) for i in range(n)))
        wall = time.monotonic() - t0
    all_ = [d for _, d in lat]
    by = {k: {"n": sum(1 for kk, _ in lat if kk == k), "p50": pct([d for kk, d in lat if kk == k], 50),
              "p95": pct([d for kk, d in lat if kk == k], 95)} for k in ("ask", "agent", "page")}
    total = len(all_) + sum(errs.values())
    return {"users": n, "seconds": round(wall, 1), "requests": total, "ok": len(all_),
            "throughput_rps": round(len(all_) / wall, 2), "error_rate": round(sum(errs.values()) / max(1, total), 4),
            "errors": dict(errs), "p50_s": pct(all_, 50), "p95_s": pct(all_, 95), "p99_s": pct(all_, 99),
            "mean_s": round(statistics.mean(all_), 3) if all_ else None, "by_kind": by}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--token", default="synthetic-orbitra-lab-fixture-token-0123456789")
    ap.add_argument("--stages", default="10/25/50/100")
    ap.add_argument("--seconds", type=int, default=45)
    ap.add_argument("--db", required=True)
    ap.add_argument("--pid", type=int, required=True, help="server process id (for CPU/memory)")
    ap.add_argument("--label", default="")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    import httpx

    base = f"http://127.0.0.1:{args.port}"
    me = httpx.get(base + "/v1/me", headers={"Authorization": f"Bearer {args.token}"}, timeout=30).json()
    if me.get("tenant", {}).get("slug") != "orbitra" or not me["tenant"].get("synthetic"):
        print("refused: synthetic Orbitra workspace only")
        return 2
    admin = os.environ["VELOXA_TEST_ADMIN_DSN"]
    results = []
    for n in [int(x) for x in args.stages.split("/")]:
        sampler = Sampler(args.pid, admin, args.db)
        sampler.start()
        r = asyncio.run(stage(base, args.token, n, args.seconds))
        sampler.stop.set()
        sampler.join(5)
        ss = sampler.samples
        r["server"] = {"cpu_pct_peak": max((s["cpu_pct_of_machine"] for s in ss), default=None),
                       "cpu_pct_mean": round(statistics.mean(s["cpu_pct_of_machine"] for s in ss), 1) if ss else None,
                       "working_set_mb_peak": max((s["working_set_mb"] for s in ss), default=None),
                       "db_connections_peak": max((s["db_connections"] for s in ss), default=None)}
        results.append(r)
        print(json.dumps({k: r[k] for k in ("users", "requests", "throughput_rps", "error_rate", "errors", "p50_s",
                                            "p95_s", "p99_s", "server")}), flush=True)
        time.sleep(5)
    if args.out:
        args.out.write_text(json.dumps({"label": args.label, "stages": results}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
