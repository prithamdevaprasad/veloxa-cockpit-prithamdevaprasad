# Response to CEO voice note T6a (released D2, 17 Nov 2026)

## What was asked (paraphrase)

You do not want to type questions every morning. When you open the cockpit, it should tell you what to worry about: the top three.

## Decision: a change, with one pushback

### Accepted and built

- **A Today view that needs no question.** `GET /v1/today` is the call the cockpit makes when it opens.
  - It returns up to three items. Each has the business object, why it is shown, the records it rests on, what is still unknown, and its dates.
  - It is recorded in the same immutable briefing snapshot as `GET /v1/briefing`, with the read basis and policy state, so what you were shown on a given morning can be replayed exactly.
- **No padding.** If the evidence supports fewer than three worries, you get fewer, with the reason. Nothing is invented to fill the list.
- **Honest gaps.** The view also lists which kinds of risk cannot be assessed yet, and why. Today that is financial exposure, service continuity and reporting integrity, which lack the records they need. You see the blind spots, not just the items.

**On today's data,** the view shows one item. A delivery commitment that two board updates first reported as on track was later recorded as moved, with no new date stated. Both updates are cited.

### Pushed back: "top three" as a ranking

A real top three needs a ranking, and ranking risks needs your decisions on how to weigh delivery against money, continuity and reporting (decision D-15, still open). Picking three by any order I chose would present my judgement as yours.

So until you record D-15:

- **The rule.** An item qualifies only if the evidence itself supports it: a risk that no other risk demonstrably outweighs on every criterion, or a formally recorded adverse event (a missed or moved delivery date, a confirmed reporting discrepancy, a confirmed gap in service cover).
- **Never ranked.** Qualifying items are never ranked against each other.
- **More than three.** If more than three qualify, the view names every candidate and says that picking three needs your D-15 decision. It does not quietly cut the list.

**The safer alternative is already in place:** once D-15 is recorded, the same view takes the top three from the approved ranking, with no change to how you use it.

## Not included, deliberately

- **No probability that something will go wrong.** The evidence gates for a probability are not met.
- **Meeting summaries are excluded as worries.** Being auto-generated and undated, they are shown as provisional evidence only.
- **No screen yet.** There is no front-end decision yet. The Today call is ready for whichever interface is chosen.

## How this is verified

- `tests/test_today.py`:
  - **No question needed:** the view opens without one, and refuses without authentication.
  - **At most three:** never more, and never padded.
  - **No invented cut:** with more than three candidates, none is picked.
  - **Only real worries:** provisional or non-adverse signals never qualify.
  - **No ranking:** `top_three` stays empty while D-15 is pending.
  - **Replayable:** the snapshot replays exactly.
- `tests/test_signals_db.py::test_today_shows_the_delayed_commitment_without_a_question` exercises a synthetic delay end to end.
- A local check on the real Day 1 data shows the single item described above.
