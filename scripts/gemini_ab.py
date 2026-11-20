"""Gemini wording experiment: A/B on the 99-case Profile B benchmark, SYNTHETIC Lumora data only.

    python scripts/gemini_ab.py --env-file <path to .env with GEMINI_API_KEY> [--limit N] [--out results.json]

- Builds a fresh synthetic Lumora Demo Lab in a disposable database (`veloxa_test_ab_<hex>`, dropped at the end).
  The supplied company's Day folders are never read; the hackathon tenant is refused by the adapter by name.
- Mode A: the deterministic facts-only answer (baseline). Mode B: the SAME answer, then `ask.wording.reword` through
  GovernedLLMClient (policy, redaction, budget reservation, call record) with the Gemini adapter; the deterministic
  check accepts or rejects the wording; rejected or failed wording falls back to facts-only.
- Facts, citations, confidence and status are identical in both modes by construction; the comparison measures what
  the wording changes: whether the expected fact is still stated, readability, latency, tokens, errors, fallbacks.
- The key is read from the env file into this process only; it is never printed or written anywhere.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests"), str(ROOT / "scripts")]
ROLE_PW = {"migrator": "local-dev-migrator", "app": "local-dev-app", "ingest": "local-dev-ingest",
           "projection": "local-dev-projection"}


def _dsn(admin, db, role=None):
    p = urlsplit(admin)
    pw = os.environ.get(f"VELOXA_{role.upper()}_PASSWORD", ROLE_PW[role]) if role else None
    netloc = p.netloc if role is None else f"veloxa_{role}:{pw}@{p.netloc.rsplit('@', 1)[-1]}"
    return urlunsplit((p.scheme, netloc, f"/{db}", p.query, p.fragment))


def load_key(env_file: Path) -> None:
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("GEMINI_API_KEY="):
            os.environ["GEMINI_API_KEY"] = line.split("=", 1)[1].strip()
    if not os.environ.get("GEMINI_API_KEY"):
        raise SystemExit("GEMINI_API_KEY not found in the env file")


def flesch(text: str) -> float:
    """Flesch reading ease (deterministic readability proxy; higher = easier)."""
    words = re.findall(r"[A-Za-z]+", text)
    if not words:
        return 0.0
    sentences = max(1, len(re.findall(r"[.!?]+", text)))
    syll = sum(max(1, len(re.findall(r"[aeiouy]+", w.lower()))) for w in words)
    return round(206.835 - 1.015 * len(words) / sentences - 84.6 * syll / len(words), 1)


def main() -> int:  # noqa: PLR0915
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", required=True, type=Path)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", type=Path, default=Path("gemini_ab_results.json"))
    ap.add_argument("--pause", type=float, default=4.5, help="seconds between model calls (free-tier pacing)")
    args = ap.parse_args()
    admin = os.environ.get("VELOXA_TEST_ADMIN_DSN")
    if not admin:
        raise SystemExit("VELOXA_TEST_ADMIN_DSN is required")
    load_key(args.env_file)

    import psycopg
    from demo_lab import populate
    from synthetic_company import gold_b

    from veloxa import tools
    from veloxa.ask import pipeline, wording
    from veloxa.authz import evidence
    from veloxa.db import connect
    from veloxa.llm.client import GovernedLLMClient, TenantModelPolicy
    from veloxa.llm.gemini import DEFAULT_MODEL, GeminiProvider
    from veloxa.migrate import migrate
    from veloxa.releases import current_time
    from veloxa.storage import LocalContentStore

    db = f"veloxa_test_ab_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{db}"')
    tmp = Path(tempfile.mkdtemp(prefix="veloxa-ab-"))
    try:
        for r, pw in ROLE_PW.items():
            os.environ.setdefault(f"VELOXA_{r.upper()}_PASSWORD", pw)
        migrate(_dsn(admin, db), _dsn(admin, db, "migrator"), db)
        slug = f"demo-ab-{uuid.uuid4().hex[:6]}"
        dsns = {"admin": _dsn(admin, db), "app": _dsn(admin, db, "app"), "ingest": _dsn(admin, db, "ingest"),
                "projection": _dsn(admin, db, "projection")}
        lab = populate(dsns, LocalContentStore(tmp / "originals"), tmp / "files", slug=slug) | {"dsns": dsns}
        tid = uuid.UUID(lab["tenant_id"])
        os.environ["VELOXA_GEMINI_TENANTS"] = slug
        with connect(dsns["admin"]) as a:
            classes = sorted({r["data_class"] for r in a.execute(
                "SELECT DISTINCT data_class FROM ledger.source_record WHERE tenant_id = %s", (tid,)).fetchall()}
                - {"sensitive_personal", "payroll_aggregate"})
            a.execute("INSERT INTO runtime.llm_budget (tenant_id, period, limit_tokens, set_by, decision_record)"
                      " VALUES (%s, %s, 400000, 'Owner', 'Gemini synthetic experiment, directive 18 Nov 2026')",
                      (tid, datetime.now(UTC).strftime("%Y-%m")))
            tenant = {"tenant_id": tid, "slug": slug, "synthetic": True}
        provider = GeminiProvider()
        policy = TenantModelPolicy("gemini", DEFAULT_MODEL, "google-ai-studio", frozenset({"compose"}),
                                   frozenset(classes), 2000)
        rows = []
        with connect(dsns["app"]) as c, connect(dsns["admin"]) as llm_conn:
            client = GovernedLLMClient(llm_conn, policy, provider)
            cases = gold_b.cases(lab["facts"])[: args.limit]
            for case in cases:
                acc = evidence.access_for(c, tid, case["principal"], "rebac")
                p = tools.Principal(case["principal"], "authenticated", None, acc, tid)
                kw = {"as_known_at": case["as_known_at"]} if case["as_known_at"] else {}
                ans = pipeline.answer(c, tid, "Asia/Kolkata", p, case["question"], current_time(), **kw)
                base_text = " ".join([ans.get("answer", "")] + [cl["text"] for cl in ans["claims"]]).replace(
                    "⟦", "").replace("⟧", "")
                t0 = time.perf_counter()
                worded = wording.reword(llm_conn, tenant, ans, client)
                w = worded["wording"]
                called = w["mode"] == "experimental_model" or w.get("fallback")
                model_text = (w.get("summary", "") + " " + " ".join(w.get("claims", {}).values())) \
                    if w["mode"] == "experimental_model" else base_text
                expect = (case["text"] or "").lower()
                rows.append({
                    "id": case["id"], "category": case["category"], "status": ans["status"],
                    "baseline_has_expected": (not expect) or expect in base_text.lower()
                    or any(expect in (e.get("excerpt") or "").lower().replace("⟦", "").replace("⟧", "")
                           for e in ans["evidence"]),
                    "model_has_expected": (not expect) or expect in model_text.lower()
                    or any(expect in (e.get("excerpt") or "").lower().replace("⟦", "").replace("⟧", "")
                           for e in ans["evidence"]),
                    "mode": w["mode"], "reason": w.get("reason"), "rejections": w.get("rejections"),
                    "latency_ms": w.get("latency_ms") if called else None, "usage": w.get("usage") if called else None,
                    "readability_baseline": flesch(ans.get("answer", "")),
                    "readability_model": flesch(w.get("summary", "")) if w["mode"] == "experimental_model" else None,
                    "wall_ms": round((time.perf_counter() - t0) * 1000)})
                if called:
                    time.sleep(args.pause)
        accepted = [r for r in rows if r["mode"] == "experimental_model"]
        calls = [r for r in rows if r["latency_ms"] is not None]
        summary = {
            "model": DEFAULT_MODEL, "cases": len(rows), "model_calls": len(calls),
            "accepted_wordings": len(accepted),
            "fallbacks": sum(1 for r in rows if r["mode"] == "facts_only" and r["reason"]
                             and "bypassed" not in r["reason"]),
            "bypassed": sum(1 for r in rows if r["reason"] and "bypassed" in r["reason"]),
            "rejected_by_check": sum(1 for r in rows if r["rejections"]),
            "provider_errors": sum(1 for r in rows if r["reason"] and "unavailable" in r["reason"]),
            "baseline_expected_fact_rate": round(sum(r["baseline_has_expected"] for r in rows) / len(rows), 3),
            "model_expected_fact_rate": round(sum(r["model_has_expected"] for r in rows) / len(rows), 3),
            "readability_baseline_mean": round(statistics.mean([r["readability_baseline"] for r in accepted]), 1)
            if accepted else None,
            "readability_model_mean": round(statistics.mean([r["readability_model"] for r in accepted]), 1)
            if accepted else None,
            "latency_ms_p50": statistics.median([r["latency_ms"] for r in calls]) if calls else None,
            "latency_ms_max": max([r["latency_ms"] for r in calls]) if calls else None,
            "input_tokens": sum((r["usage"] or {}).get("input_tokens", 0) for r in calls),
            "output_tokens": sum((r["usage"] or {}).get("output_tokens", 0) for r in calls),
            "facts_citations_confidence_status": "identical in both modes by construction (wording only)",
        }
        args.out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2, default=str), encoding="utf-8")
        print(json.dumps(summary, indent=2))
    finally:
        with psycopg.connect(admin, autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
