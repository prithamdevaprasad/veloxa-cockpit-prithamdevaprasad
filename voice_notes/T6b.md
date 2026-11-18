# Response to CEO voice note T6b (released D3, 18 Nov 2026)

## What was asked (paraphrase)

Marketing will report to a new chief revenue officer, next month or the month after. The cockpit must not break when people move.

## Decision: accepted, with one pushback

### Accepted, and proven by tests

- **Reporting lines follow the HR record at the time you ask about.** Each admitted HR export is a dated snapshot. A move is a new record version from the batch that records it, and earlier answers stay exactly as they were.
- **The 14 agent people follow the same rule.** An agent's manager and direct reports are read from the HR snapshot at the knowledge point you ask about (`veloxa.agents.people.profile`). When the record changes, the agent changes from that point on, not before.
- **Announcements are not moves.** A plan announced in a call or a document changes nothing until an HR record states the move. No date is guessed and no new leader is assumed.
- **Moving people grants no access.** Access comes only from authorisation records.

### Pushback: no move before the record

The move date is not final and the new leader is not named. Until an HR record states the move (with its effective date, if the record gives one), marketing's reporting line stays as recorded, and the cockpit says so if asked.

## Verification

`tests/test_reporting_lines.py`:
- a recorded move is visible from its own batch only;
- an earlier knowledge point is unchanged;
- an announced move changes nothing;
- no effective date is inferred;
- tenants are isolated;
- a manager change is not an access grant.
