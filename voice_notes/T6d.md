# Response to CEO voice note T6d (released D4, 19 Nov 2026)

## What was asked (paraphrase)

An engineer resigned this morning. The notice period is "two months, I think". What does this do to version three, before the board call?

## Decision: an assessment from the records, separated into facts, implications and unknowns

There is no probability, and no judgement of the person.

### Facts (as recorded)

**The resignation is not yet a record.**
- The HR export (as at its extraction) shows the engineer as active, with no exit date.
- No resignation record about this engineer has been admitted. (The resignation letter in the data room is about a different person.)
- Until HR records it, the cockpit treats the resignation as reported by you and not yet recorded. It does not change the HR facts.

**Who is meant is assumed.**
- The note's spelling matches one HR person only by first-name prefix.
- The cockpit says "identity assumed" whenever it makes such a match.

**"Version three" is not a version name.**
- The tracker has one release version for this product. Several of its epics are the "v3" workstreams.
- The release's date has already moved once. The original and revised dates are both kept: the original is never overwritten.

**Open scope.**
- At the tracker's export, every open item belongs to that release.
- A substantial share of its open scope is assigned to this engineer, including one epic with no other assignee.
- **Caveat:** the tracker's assignee field holds initials. They match this engineer uniquely within the HR list, but that link is a candidate, not a confirmed identity.

**Code.**
- This engineer's commits are concentrated in the core engine repository.
- A second author address in the log may be the same person. It is **not** merged: that needs a human decision.
- This is evidence of where knowledge sits, not of productivity.

**Cover.**
- The engineer appears on a few weeks of the on-call rota (scheduled cover, not attendance).
- The engineer is not a responder on any recorded incident.

### Implications (what follows from the facts, and no further)

- **Delivery dependency.** If the departure is confirmed, the revised release date depends on reassigning a large share of the open scope. That includes a workstream that today has a single owner.
- **Handover.** Knowledge of the core engine is concentrated. Handover is a work-level continuity risk, not a statement about the person.
- **Timing.** If the notice period is two months from today, it ends after the revised release date. Whether that holds depends on the unknowns below.
- **Cover.** On-call cover is less affected than delivery, on the records as they stand.

### Unknowns (stated, not filled in)

- **The resignation.** Has it been recorded by HR, and from what date?
- **Notice.** How long it is ("I think" is not a record), and the last working day.
- **Identity.** Whether the tracker initials and the second git address are this engineer. Both are unresolved, pending a human decision.
- **Scope.** Whether any of the open scope can be descoped or reassigned, and who would take it.
- **The board call.** Its date, and what was already said to the board about the release date.

### Not done, deliberately

- **No probability** that the release slips. There is no calibrated basis.
- **No certainty** about the notice period.
- **No judgement** of anyone's productivity from commits or tickets (POL-PROD-01).
- **No reason** for the resignation (POL-SENS-01).

## What the cockpit does with this

- **The tracker, rota, incident log and git log** (D3) are admitted and sealed, and the two D4 voice notes are admitted as batch 4. The facts above were re-checked against the admitted records on 19 Nov 2026 after the D4 release.
- **The voice note is a report, not an HR record** (A-020). It is cited as "reported by the CEO"; the HR export is unchanged.
- **When HR records the resignation,** it arrives as a new record version in its own batch. Earlier answers stay as they were.
- **Identity links** are resolved only by a human decision.
- **Point in time.** A question asked "as known at" any moment before 19 Nov 2026, 10:00 IST uses knowledge point 3 and cannot cite this note.
