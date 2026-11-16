# TRANSCRIPT_NOTES

**Release status: material released up to D1 (Mon 16 Nov 2026).** Items that depend on later packs are added to this file on those packs' release days (see "Held for later release").

The register of suspected mishearings, contradictions and open uncertainties across the call summaries and exports. For each item it gives the evidence, the candidate readings and what would settle it, plus a conclusion for the owner to fill in.

**Rules for this file**

- **AI does not decide which source is true** (`email.md` line 65). The evidence, candidate interpretations and "what would settle it" were prepared by AI from the challenge material. **Every owner-conclusion field is deliberately blank.** The owner completes it in their own words, saying which source they believe and why.
- **Candidate interpretations are not ranked.** Their order carries no meaning, and the list is not exhaustive.
- **Revise, don't delete.** When the owner changes a conclusion, add a dated line under the entry. Keep the earlier conclusion.
- **Public-repository safety.**
  - Evidence is cited by **opaque references** (`EV-nnn`), each tagged with its source type and release pack. The mapping from references to exact source locations is kept privately by the owner.
  - Evidence is paraphrased, never quoted.
  - People are never named or identified.
  - No financial figures, vendor names, internal identifiers or individual pay details appear.

**Evidence tags.** `[EV-nnn · source · pack]`. Sources: Brief, README, Board update, Email, Ledger, Payroll register, HR export, or a call summary (T1, T2). Packs: **D1** (Mon 16 Nov, 10:00 IST); **—** for the hackathon email (`email.md`).

| Visible from | Items currently in this file |
|---|---|
| D1 | M-01*, M-02, M-03*, M-04*, M-07*, M-08, M-09*, M-10*, M-12*, M-13*; C-01*, C-02, C-03, C-04, C-18, C-21, C-22, C-23, C-30*; U-01, U-02 |

\* **Day 1 version:** the item's full evidence spans later packs. This file currently carries only its Day 1 evidence, and the full version replaces it on the release day shown under "Held for later release".

---

## Part A. Suspected mishearings (names and tools)

The "candidate reading" is the most direct match in the exports. It is **not** a conclusion.

| ID | What is suspected | Evidence | Candidate reading(s) | What would settle it | Visible from | Owner conclusion |
|---|---|---|---|---|---|---|
| M-01* | A first name in the CEO call is spelled differently from the HR export | [EV-040 · T1 · D1]; [EV-031 · HR export · D1] | The single HR-export employee with a matching first name | A record using the call's spelling | D1 | |
| M-02 | A finance team member's first name is spelled differently from the HR export | [EV-049 · T2 · D1]; [EV-032 · HR export · D1] | The HR-export employee in that department with a matching name and duties | — | D1 | |
| M-03* | A finance team member's surname is spelled differently from the HR export | [EV-049 · T2 · D1]; [EV-033 · HR export · D1]; [EV-012 · Email · D1] | The HR-export employee with matching duties; an email refers to the same person by first name | — | D1 | |
| M-04* | A nickname in the CFO call matches no export | [EV-052 · T2 · D1]; [EV-034 · HR export · D1] | (a) A nickname for a leadership-team member; (b) someone not in the exports | A record using the nickname | D1 | |
| M-07* | The issue tracker's name is misspelled phonetically, and a second tracker product is mentioned | [EV-039 · T1 · D1]; [EV-007 · README · D1] | Jira (the README lists it); the second product's status is unclear | — | D1 | |
| M-08 | The accounting system's name is misspelled phonetically | [EV-047 · T2 · D1]; [EV-007 · README · D1]; [EV-015 · Ledger · D1] | Zoho Books. When the books moved systems is not recorded (U-02) | — | D1 | |
| M-09* | The HR system's name is misspelled phonetically | [EV-048 · T2 · D1]; [EV-007 · README · D1]; [EV-026 · HR export · D1] | Keka | — | D1 | |
| M-10* | The survey tool's name is misspelled phonetically | [EV-042 · T1 · D1]; [EV-007 · README · D1] | Culture Amp | — | D1 | |
| M-12* | The CRM product's name is misspelled phonetically (an action item asks for its export) | [EV-044 · T1 · D1] | HubSpot. No CRM export is supplied (GR-04) | — | D1 | |
| M-13* | The chat product's name is misspelled phonetically (a channel is mentioned) | [EV-045 · T1 · D1] | Slack. No chat export is supplied (GR-04) | — | D1 | |

---

## Part B. Contradictions and uncertainties

### C-01* · Company size
- **Visible from:** D1 (Day 1 version)
- **Evidence:**
  - The brief describes a company of a few hundred people [EV-001 · Brief · D1].
  - The CEO call gives a figure in the tens, with a parenthetical figure in the hundreds [EV-038 · T1 · D1].
  - The HR export lists a headcount in the tens [EV-054 · HR export · D1], and both board updates report the same headcount [EV-009, EV-010 · Board update · D1].
- **Candidate interpretations:**
  - (a) A small company; the brief's figure is a misstatement or mishearing.
  - (b) A larger company, with the data room covering a slice (the brief asks to begin with a subset of people) [EV-002 · Brief · D1].
  - (c) People outside the HR export (e.g. contractors) account for part of the difference.
- **What would settle it:** Any headcount-bearing record beyond the HR export.

| Owner conclusion | Owner reasoning (source believed, and why) | Date |
|---|---|---|
| | | |

### C-02 · ARR
- **Visible from:** D1
- **Evidence:**
  - The CEO call gives an ARR figure in crore [EV-038 · T1 · D1].
  - The CFO call gives a start-of-year figure with no unit [EV-046 · T2 · D1]. It is *higher* than the CEO call's current figure, so the two calls also disagree in direction.
  - Both board updates report ARR several times lower than the CEO call's figure [EV-009, EV-010 · Board update · D1].
- **Candidate interpretations:**
  - (a) Mishearing of magnitude or unit.
  - (b) A different metric (e.g. bookings, contract value, pipeline).
  - (c) A different period (the start of the year read as April or as January).
  - (d) A speaker error.
- **What would settle it:** An ARR schedule (not supplied, GR-04).

| Owner conclusion | Owner reasoning (source believed, and why) | Date |
|---|---|---|
| | | |

### C-03 · Monthly burn
- **Visible from:** D1
- **Evidence:**
  - The CEO call gives a burn figure with no unit [EV-041 · T1 · D1].
  - The CFO call gives a similar figure in lakhs a month [EV-046 · T2 · D1].
  - Both board updates report a monthly burn more than an order of magnitude larger [EV-009, EV-010 · Board update · D1].
  - The ledger's monthly payroll line alone exceeds the CFO-call figure many times over [EV-018 · Ledger · D1].
- **Candidate interpretations:**
  - (a) Mishearing of the number or the unit.
  - (b) A different metric or period (e.g. net burn, or a figure that is not monthly).
  - (c) The summariser supplied the unit.
- **What would settle it:** A cash-flow statement (not supplied).

| Owner conclusion | Owner reasoning (source believed, and why) | Date |
|---|---|---|
| | | |

### C-04 · Financial-year label
- **Visible from:** D1
- **Evidence:**
  - The CFO call says the year runs April to March and names the current FY, noting that the speaker may have said the next year's label [EV-046 · T2 · D1].
  - The payroll register labels Jan–Mar 2026 as Q4, which makes Apr 2025 – Mar 2026 the year that ended in March 2026 [EV-022 · Payroll register · D1].
  - The call date is unknown (A-006).
- **Candidate interpretations:**
  - (a) Apr 2026 – Mar 2027 is labelled by its end year.
  - (b) It is labelled by its start year.
  - (c) The call took place before April 2026, and the label meant the year then running.
- **What would settle it:** A finance document that uses an FY label.

| Owner conclusion | Owner reasoning (source believed, and why) | Date |
|---|---|---|
| | | |

### C-18 · Summit vendor(s)
- **Visible from:** D1
- **Evidence:**
  - An events cost-centre line in late August names an events vendor, with a narration about the summit venue [EV-019 · Ledger · D1].
  - A line in early September, in the same cost centre, has a near-identical vendor name, a different reference format and a catering narration that does not mention the summit [EV-020 · Ledger · D1].
  - A third, similarly named vendor appears in the sales-travel cost centre, for a sales offsite [EV-021 · Ledger · D1].
- **Candidate interpretations:**
  - (a) One vendor, with a duplicate or misspelt vendor-master entry.
  - (b) Two different vendors.
  - (c) The September spend is not summit spend.
- **What would settle it:** The vendor master or tax registration numbers, or the invoices (not supplied).

| Owner conclusion | Owner reasoning (source believed, and why) | Date |
|---|---|---|
| | | |

### C-21 · An unresolved joining-month payroll query (payroll-process finding)
- **Visible from:** D1
- **Evidence:**
  - A joining-month payroll calculation was prepared by a stand-in from the previous month's sheet while the usual processor was away [EV-012 · Email · D1].
  - The resulting payslip was queried as possibly too high for a part month, and the reply promised a check [EV-013, EV-014 · Email · D1].
  - The register's days-payable entries and the HR export's dates bear on the query [EV-023, EV-024 · Payroll register · D1; EV-036 · HR export · D1].
  - No resolution or correction is recorded within the extract (GR-05).
  - No individual pay information is reproduced here.
- **Candidate interpretations:**
  - (a) A proration error in the joining-month calculation.
  - (b) A policy that pays the full joining month.
  - (c) A correction made later, outside the extract (later payroll registers were not supplied).
- **What would settle it:** A later payroll register or a correction record.

| Owner conclusion | Owner reasoning (source believed, and why) | Date |
|---|---|---|
| | | |

### C-22 · The ledger payroll line is identical every month
- **Visible from:** D1
- **Evidence:**
  - The monthly payroll line in the ledger carries the same amount every month from April to September 2026 [EV-018 · Ledger · D1].
  - That amount matches the totals of the last two months of the payroll register [EV-025 · Payroll register · D1].
  - The HR export has no join or exit dates in that period [EV-028, EV-030 · HR export · D1].
- **Candidate interpretations:**
  - (a) Genuine: no headcount or pay changes over the period.
  - (b) A standing journal or accrual rather than actuals.
  - (c) Pay changes (e.g. annual increments) were not reflected in the ledger.
- **What would settle it:** A payroll register for April–September (not supplied).

| Owner conclusion | Owner reasoning (source believed, and why) | Date |
|---|---|---|
| | | |

### C-23 · Burn vs runway
- **Visible from:** D1
- **Evidence:**
  - Between the two board updates, the reported monthly burn falls slightly and the reported runway falls by one month [EV-009, EV-010 · Board update · D1].
  - **Arithmetic:** even allowing for whole-month rounding of the runway, the implied fall in cash between the two reports is smaller than one quarter's burn at the reported rate.
  - The extracted ledger costs (selected cost centres) average *more* per month in Jul–Sep than in Apr–Jun, while the reported burn fell [EV-055 · Ledger · D1].
- **Candidate interpretations:**
  - (a) Rounding (it accounts for part of the gap).
  - (b) Net vs gross burn (collections or revenue).
  - (c) Inflows such as a bridge or the round.
  - (d) A different runway definition.
  - (e) Costs outside the ledger extract.
- **What would settle it:** A cash-balance statement (not supplied).

| Owner conclusion | Owner reasoning (source believed, and why) | Date |
|---|---|---|
| | | |

### C-30* · Timing of the hiring freeze in the CFO call
- **Visible from:** D1 (Day 1 version)
- **Evidence:**
  - In the CFO call, a hiring freeze is planned, conditional on the funding round slipping (future tense) [EV-046 · T2 · D1].
  - The call mentions two backend roles in passing, and an action item to check the hiring-freeze decision with a named person, which implies a decision exists [EV-050, EV-052 · T2 · D1].
  - The later board update says hiring is paused while the round closes [EV-011 · Board update · D1].
- **Candidate interpretations:**
  - (a) The call predates the pause the board reports, while the freeze was still conditional.
  - (b) The call came after it, and the summary mixes tenses.
  - (c) The freeze refers to something broader than the two roles.
- **What would settle it:** The call date (A-006).

| Owner conclusion | Owner reasoning (source believed, and why) | Date |
|---|---|---|
| | | |

---

## Part D. Open ambiguities with no conflicting source

| ID | Ambiguity | Evidence | Note | Visible from | Owner note |
|---|---|---|---|---|---|
| U-01 | Which figure the board wants by a stated month | [EV-043 · T1 · D1] (an unidentified second speaker [EV-037 · T1 · D1]) | No record identifies it | D1 | |
| U-02 | When the books moved accounting systems (the call is unsure of the month) | [EV-047 · T2 · D1] | The ledger extract starts in Apr 2026 [EV-015 · Ledger · D1], and the call warns that older numbers may not match | D1 | |

---

## Held for later release

These items exist in the owner's private working register. They are withheld for now because their evidence includes later-pack material. Topics are not listed, because a topic could reveal later content.

| Release pack | Items added (full) | Items whose Day 1 version is replaced by the full version |
|---|---|---|
| D2 (Tue 17 Nov) | M-11; C-12, C-13, C-14, C-16, C-28, C-32; U-03 | M-03, M-04, M-09, M-10; C-01, C-30 |
| D3 (Wed 18 Nov) | M-05, M-06, M-14; C-05, C-06, C-07, C-08, C-09, C-10, C-11, C-17, C-19, C-20, C-26, C-27, C-31; I-01; U-04 | M-07, M-12 |
| D4 (Thu 19 Nov) | C-15 (its D2 evidence alone can be released on D2), C-24, C-25, C-29 | M-01, M-13 |

---

## Revision log

| Date | Change | By |
|---|---|---|
| 16 Nov 2026 | Day 1 public edition prepared from the owner's private working register:<br>• D1 items only, plus Day 1 versions of ten mixed-pack items;<br>• opaque evidence references;<br>• no quotations, names, identifiers, figures or individual pay details;<br>• C-21 generalised to a payroll-process finding.<br>Every owner-conclusion field is blank | AI (Claude Code), for owner review |
