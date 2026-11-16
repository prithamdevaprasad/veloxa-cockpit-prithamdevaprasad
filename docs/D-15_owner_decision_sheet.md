# D-15 owner decision sheet: risk weights, anchors, guardrail tiers, gate thresholds

**Status: pending.** Every owner-choice field below is deliberately blank. Nothing here recommends or selects a value.

**Why this matters now.**
- The risk engine compares risks only by weight-free dominance. `GET /v1/briefing` returns `top_three: null` until this decision is recorded **and** a weighted implementation is separately approved.
- `ARCHITECTURE.md` §16.7 asks for rules and anchors to be committed before later release material is analysed, so that hindsight cannot shape them.

**Sources:**
- `ARCHITECTURE.md` §16.2–§16.5, §26.1;
- `DECISIONS.md` DEC-18 (structure approved) and D-15 (values not set);
- ADR-14.

## 1. Already approved (structure only)

- Detectors produce signals, grouped by business object.
- Five impact criteria, each scored as an interval.
- Ranking by dominance, with a few explicit guardrail tiers and declared ties.
- A probability is shown only behind evidence gates; otherwise a labelled deterministic bound.
- Quantification states: quantified, bounded, unquantified, insufficient.
- People criteria use allocation records only.

## 2. The documented measures (§16.2)

| Criterion | Natural scale | Minimum evidence to score | Direction the engine uses (from the §16.2 wording) |
|---|---|---|---|
| C1 External-commitment exposure | Required-rate ratio toward a dated customer or board commitment | A dated commitment, an open-scope fact, a throughput window | Higher is worse |
| C2 Financial exposure | Currency at stake, from records the principal may see | A record-backed amount; protected-pay amounts are not used | Higher is worse |
| C3 Operational-continuity exposure | Share of commitment-linked work or a critical service held by one person; on-call coverage concentration | Allocation records only | Higher is worse |
| C4 Information-integrity exposure | Discrepancy between figures reported upward and system records | Two independent sources in conflict | Higher is worse |
| C5 Time proximity | Days to the affected commitment | A dated commitment | Fewer days is worse |

**Measure definition implemented so far.** `C4.absolute_discrepancy@1` is the absolute difference between a figure as reported in a formal document and a governed metric, in the metric's unit.

Two values are compared only when their definitions, versions and units are identical. No other criterion has a detector yet.

## 3. What remains undecided (D-15)

| # | Decision | What it controls | Owner choice |
|---|---|---|---|
| 1 | **Weights**: a swing weight for each of C1–C5 | How much a full swing on one criterion counts against the others (additive value model, §16.2). Needed to order risks that trade off | C1: ____ C2: ____ C3: ____ C4: ____ C5: ____ |
| 2 | **Anchors**: the range each weight's swing refers to (worst and best plausible value per criterion, in its natural unit) | What a "full swing" means for each criterion | C1: ____ to ____ <br>C2: ____ to ____ <br>C3: ____ to ____ <br>C4: ____ to ____ <br>C5: ____ to ____ |
| 3 | **Guardrail tiers**: the few lexicographic overrides that rank first regardless of weights (§16.3 gives "a threatened external commitment ranks first" only as an example) | Which risks always rank above others | Tiers, in order: ____ |
| 4 | **Declared tie-break** for risks that are not distinguishable on current evidence (or "none: show them as tied") | Whether ties are broken, and how | ____ |
| 5 | **Evidence-gate thresholds** for showing any delivery probability (§16.4): <br>• stationarity: minimum weekly samples, and the trend test and level; <br>• tracker completeness test; <br>• scope-stability bound; <br>• unit-consistency rule | When a probability may be shown instead of a deterministic bound | Stationarity: ____ <br>Tracker completeness: ____ <br>Scope stability: ____ <br>Unit consistency: ____ |
| 6 | **Thresholds**, if any: values at which a risk is listed, or moves between quantification states | What enters the watchlist | ____ |

## 4. Related owner inputs (not D-15 values, but they gate what the engine can do)

| # | Input | Current state | Owner choice |
|---|---|---|---|
| A | Confirm the direction of each natural scale in section 2 | Implemented as read from the §16.2 wording; not owner-confirmed | Confirmed / changes: ____ |
| B | Confirm or replace the C4 measure definition `C4.absolute_discrepancy@1` | Implemented; not owner-confirmed | Confirmed / changes: ____ |
| C | Approve any pairing of a reported figure with a governed metric (`config/figure_mappings.toml`) | None approved, so the C4 detector checks nothing | Pairings: ____ |
| D | Approve implementing weighted ranking once D-15 is recorded | Not implemented. The engine refuses weighted ranking even with a recorded decision | Approved on: ____ |

## 5. How a decision is recorded (and audited)

The owner writes a decision file and records it with the privileged command line. The API cannot record or change a policy.

```toml
[decision]
decision_ref = "D-15"
status = "approved"
approved_by = ""            # owner
approved_on = 2026-01-01    # replace with the approval date
decision_record = ""        # e.g. the DECISIONS.md entry that states the decision and rationale

[content]
weights = {}                # section 3, item 1
anchors = {}                # item 2
guardrail_tiers = []        # item 3
gate_thresholds = {}        # item 5
# tie_break and thresholds (items 4 and 6) may be added here as well
```

```bash
veloxa policy record-d15 --file path/to/d15_decision.toml   # needs the privileged credentials, like `veloxa init`
```

- Each recording becomes the next version (1, 2, 3 …). It is append-only, hashed and audited (`policy.d15_recorded`).
- Changing a policy means recording a new version. Every briefing snapshot keeps the policy status, version and hash it was produced under, so a historical briefing is never rewritten.
- Recording a decision does **not** switch ranking on. The engine reports the approved version, keeps showing the unweighted comparison, and refuses weighted ranking until a weighted implementation is explicitly approved (item D).
