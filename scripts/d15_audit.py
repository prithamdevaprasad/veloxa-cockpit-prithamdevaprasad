"""Independent D-15 audit (reproducible). Usage:
    python scripts/d15_audit.py --dsn <admin dsn to the lab database> --tenant <slug> --policy <D15 toml>
        [--api http://127.0.0.1:4175 --token ...] [--out audit.json]

1. Recomputes every item's severity with a SEPARATE implementation of the D-15 v1 formula (not `ranking.score`)
   and checks it equals the production result, at every knowledge point K = 1..latest.
2. Checks the production API (/v1/worry) agrees with the library at the latest K (when --api is given).
3. Sensitivity: all weight moves of +/-0.05, +/-0.10, +/-0.15 (renormalised) and anchor moves (+/-20 percent).
4. A D-15 v2 CANDIDATE side by side (never activated): see `v2_eligible`.
Prints item IDs, titles and numbers only (no record content)."""

from __future__ import annotations

import argparse
import itertools
import json
import tomllib
from datetime import date
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from veloxa import tenancy
from veloxa.db import tenant_tx
from veloxa.risk import ranking, worry

C = ("C1", "C2", "C3", "C4")


def indep(policy: dict, item: ranking.Item, weights=None, anchors=None) -> tuple[float, float, float, float]:
    """(S_lo, S_hi, counted_weight, truly_measured_weight), written from the decision text, not from ranking.py."""
    w = weights or policy["weights"]
    a = anchors or policy["anchors"]
    lo = hi = counted = measured = 0.0
    for c in C:
        m = item.measures.get(c)
        if m is not None and m.status == "not_applicable":
            counted += w[c]
            continue
        if m is None or m.status != "measured":
            hi += w[c]
            continue
        if m.measure and m.measure == a[c].get("fallback_measure"):
            best, worst = a[c]["fallback_best"], a[c]["fallback_worst"]
        else:
            best, worst = a[c]["best"], a[c]["worst"]
        f = lambda x, b=best, wo=worst: min(1.0, max(0.0, (x - b) / (wo - b)))  # noqa: E731
        lo, hi = lo + w[c] * f(m.low), hi + w[c] * f(m.high)
        counted += w[c]
        measured += w[c]
    return round(lo, 4), round(hi, 4), round(counted, 4), round(measured, 4)


def bands_of(scores: dict[str, tuple[float, float]]) -> dict[str, int]:
    order = sorted(scores, key=lambda k: (-scores[k][1], -scores[k][0], k))
    out, band, floor = {}, 0, None
    for k in order:
        if floor is None or scores[k][1] < floor:
            band, floor = band + 1, scores[k][0]
        else:
            floor = min(floor, scores[k][0])
        out[k] = band
    return out


def v1_eligible(policy, item, s) -> bool:
    return s[2] + 1e-9 >= policy["gate_thresholds"]["eligibility_min_measured_weight"] and bool(item.evidence)


def v2_eligible(policy, item, s) -> bool:
    """v2 candidate change 1: in addition to v1, at least one criterion must be MEASURED from evidence. Under v1 an
    item whose applicable criteria are all unknown can pass the 50 percent gate on not-applicable weight alone."""
    return v1_eligible(policy, item, s) and s[3] > 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--tenant", default="veloxa")
    ap.add_argument("--policy", required=True)
    ap.add_argument("--api")
    ap.add_argument("--token")
    ap.add_argument("--out")
    a = ap.parse_args()
    policy = tomllib.loads(Path(a.policy).read_text(encoding="utf-8"))["content"]
    report: dict = {"policy": {"weights": policy["weights"], "gate": policy["gate_thresholds"]}, "by_k": {},
                    "mismatches": []}
    with psycopg.connect(a.dsn, autocommit=True, row_factory=dict_row) as conn:
        tid = tenancy.tenant_by_slug(conn, a.tenant)
        with tenant_tx(conn, tid):
            ks = [r["k"] for r in conn.execute("SELECT DISTINCT batch_seq AS k FROM knowledge.claim WHERE tenant_id"
                                               " = %s ORDER BY 1", (tid,)).fetchall()]
        for k in ks:
            with tenant_tx(conn, tid, batch_seq=k):
                items = worry.build_items(conn, tid, k, date.today())
            prod = ranking.rank(items, policy)
            pmap = {r["item_id"]: r for r in prod["ranked"]}
            mine = {i.item_id: indep(policy, i) for i in items}
            for i in items:
                ps = (pmap.get(i.item_id) or next(u for u in prod["unrankable"] if u["item_id"] == i.item_id))
                if (ps["severity"]["low"], ps["severity"]["high"]) != mine[i.item_id][:2]:
                    report["mismatches"].append({"k": k, "item": i.item_id, "prod": ps["severity"],
                                                 "independent": mine[i.item_id]})
                if (i.item_id in pmap) != v1_eligible(policy, i, mine[i.item_id]):
                    report["mismatches"].append({"k": k, "item": i.item_id, "issue": "eligibility differs"})
            el1 = {k2: v[:2] for k2, v in mine.items() if v1_eligible(policy, next(x for x in items
                                                                                  if x.item_id == k2), v)}
            b1 = bands_of(el1)
            if any(b1[r["item_id"]] != r["rank"] for r in prod["ranked"]):
                report["mismatches"].append({"k": k, "issue": "band assignment differs"})
            el2 = {k2: v[:2] for k2, v in mine.items() if v2_eligible(policy, next(x for x in items
                                                                                  if x.item_id == k2), v)}
            sens = {}
            for d in (0.05, 0.10, 0.15):
                moved = set()
                for signs in itertools.product((-1, 0, 1), repeat=4):
                    if not any(signs):
                        continue
                    raw = {c: max(0.0, policy["weights"][c] + s * d) for c, s in zip(C, signs, strict=True)}
                    tot = sum(raw.values())
                    w = {c: v / tot for c, v in raw.items()}
                    alt = {i.item_id: indep(policy, i, w) for i in items}
                    el = {x: v[:2] for x, v in alt.items()
                          if v1_eligible(policy, next(y for y in items if y.item_id == x), v)}
                    if set(el) != set(el1):
                        moved.add("eligibility set changed")
                    bb = bands_of(el)
                    moved |= {x for x in bb if b1.get(x) != bb[x]}
                sens[f"weights_pm_{d:.2f}"] = sorted(moved)
            for crit, field in (("C1", "fallback_worst"), ("C3", "best"), ("C3", "worst")):
                for f in (0.8, 1.2):
                    an = json.loads(json.dumps(policy["anchors"]))
                    an[crit][field] = an[crit][field] * f
                    if an[crit].get("best", 0) >= an[crit].get("worst", 1):
                        continue
                    alt = {i.item_id: indep(policy, i, None, an) for i in items if i.item_id in el1}
                    bb = bands_of({x: v[:2] for x, v in alt.items()})
                    sens[f"anchor_{crit}_{field}_x{f}"] = sorted(x for x in bb if b1.get(x) != bb[x])
            report["by_k"][k] = {
                "items": [{"item_id": i.item_id, "title": i.title, "confidence": i.confidence,
                           "S": mine[i.item_id][:2], "counted_weight": mine[i.item_id][2],
                           "truly_measured_weight": mine[i.item_id][3],
                           "measures": {c: (i.measures[c].status if c in i.measures else "no measure") for c in C},
                           "v1": f"band {b1[i.item_id]}" if i.item_id in b1 else "unassessed",
                           "v2_candidate": f"band {bands_of(el2)[i.item_id]}" if i.item_id in el2 else "unassessed",
                           "unknowns": i.unknowns} for i in items],
                "sensitivity": sens}
    if a.api:
        import httpx

        r = httpx.get(f"{a.api}/v1/worry", headers={"Authorization": f"Bearer {a.token}"}, timeout=60).json()
        latest = report["by_k"][max(report["by_k"])]
        lib = {x["item_id"]: (x["v1"], tuple(x["S"])) for x in latest["items"]}
        api = {x["item_id"]: (f"band {x['rank']}", (x["severity"]["low"], x["severity"]["high"])) for x in r["ranked"]}
        api |= {x["item_id"]: ("unassessed", (x["severity"]["low"], x["severity"]["high"])) for x in r["unrankable"]}
        report["api_agrees_with_library"] = api == lib
        report["api_k"] = r.get("as_known_at") or r.get("k")
        if api != lib:
            report["api_diff"] = {k: {"api": api.get(k), "lib": lib.get(k)} for k in set(api) | set(lib)
                                  if api.get(k) != lib.get(k)}
    out = json.dumps(report, indent=1, default=str)
    if a.out:
        Path(a.out).write_text(out, encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
