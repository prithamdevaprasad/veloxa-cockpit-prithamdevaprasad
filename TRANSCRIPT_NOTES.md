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
| M-11 | A misspelt hiring-tool name | [EV-083 · T3 · D2] [D2] | See C-13 (the conclusion is recorded there) | — | — | D2 | Keka Hire is the documented hiring tool. Treat the transcript wording as a likely mishearing; there is no supplied evidence establishing Lever. (19 Nov 2026) |
| M-05 | A short form of a first name | [EV-084 · T3 · D2] [D2], [EV-088 · T4 · D3] [D3] | The Product Marketing Manager ([EV-066 · HR export · D1]) | The work-email local part in [EV-103 · Email · D3] matches the short form [D3]. The resignation letter PDF in `documents/` is signed with the full first name [D2] | — | D3 | The short name is consistent with the Product Marketing Manager: the work-email local part and signed resignation letter provide corroborating evidence. Map it to that HR record, retaining the source references. (19 Nov 2026) |
| M-06 | A first name spelled differently from the HR export | [EV-087 · T4 · D3], [EV-088 · T4 · D3], [EV-086 · T4 · D3] [D3] | The Performance Marketing Manager ([EV-064 · HR export · D1]) | The role described in T4 (performance marketing) matches [D3] | — | D3 | The described performance-marketing role is consistent with the Performance Marketing Manager in the HR export. The role match supports the interpretation, but the supplied evidence does not independently prove the spelling is an alias for that person's name. (19 Nov 2026) |
| M-14 | Misspellings of the wiki and incident tools | [EV-093 · T5 · D3], [EV-097 · T5 · D3] [D3] | Confluence; PagerDuty | [EV-074 · README · D1] line 5 lists PagerDuty [D1]; the incident export [D3]. No wiki export supplied | — | D3 | PagerDuty is supported by the README and incident records. Confluence is a plausible reading of the wiki reference, but cannot be verified because no wiki export is supplied. Keep the two tool conclusions separate. (19 Nov 2026) |

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

### Released with D2 and D3 (owner conclusions recorded 19 Nov 2026)

### C-12 · Number of open roles
- **Visible from:** D2
- **Evidence:**
  - [EV-082 · T3 · D2]: the Talent Partner cites an April open-role count several times larger than the number of requisitions in the tracker, a number of roles dropped in August, and an offer-acceptance rate [D2].
  - [EV-069 · Hiring tracker · D2]: six requisitions in total, two put on hold in August; one requisition number is missing from the sequence [D2].
- **Candidate interpretations:**
  - (a) Mishearing of a small number as a larger one.
  - (b) Company-wide numbers not in this export (cf. C-01).
  - (c) Counting candidates or openings rather than requisitions.
- **What would settle it:** A complete hiring export, including the missing requisition.

| Owner conclusion | Owner reasoning (source believed, and why) | Date |
|---|---|---|
| The supplied tracker contains six requisitions, two of which were put on hold in August. | The spoken April figure cannot be reconciled to this extract. It may use a different scope or measure. Do not treat it as the confirmed company-wide number of open requisitions. | 19 Nov 2026 |

### C-13 · Hiring tool
- **Visible from:** D2
- **Evidence:**
  - [EV-083 · T3 · D2] names the hiring tool with a word resembling another product's name [D2].
  - [EV-069 · Hiring tracker · D2] title row names Keka Hire [D2]. [EV-074 · README · D1] line 3 lists the Keka Hire tracker [D1].
- **Candidate interpretations:**
  - (a) A different product (Lever) misheard.
  - (b) "Keka Hire" misheard.
  - (c) Both tools are in use.
- **What would settle it:** Any record naming the tool.

| Owner conclusion | Owner reasoning (source believed, and why) | Date |
|---|---|---|
| Keka Hire is identified by the supplied tracker and README. | The transcript's Lever-like wording is most likely a mishearing, but the evidence does not rule out another tool being used separately. | 19 Nov 2026 |

### C-14 · A People & Ops role
- **Visible from:** D2
- **Evidence:**
  - [EV-081 · T3 · D2] describes a People & Ops team member as an HR business partner who handles exits [D2].
  - [EV-067 · HR export · D1] gives that person a general HR and office title [D1].
- **Candidate interpretations:**
  - (a) Informal duties that differ from the formal title.
  - (b) The summariser's label.
  - (c) A role change after the export.
- **What would settle it:** A role record or job description.

| Owner conclusion | Owner reasoning (source believed, and why) | Date |
|---|---|---|
| Use the HR export for the formal title. | The transcript supports that the person performs HR business-partner and exit-related duties, but does not establish a formal title change. | 19 Nov 2026 |

### C-16 · When manager training started
- **Visible from:** D2
- **Evidence:**
  - [EV-079 · T3 · D2]: one speaker places the start of manager training after Diwali (around November); another says January [D2].
  - Diwali 2026 falls in early November (A-023) [—]. The call date is unknown (A-006).
- **Candidate interpretations:**
  - (a) Started in Nov 2025, after Diwali 2025.
  - (b) Planned for Nov 2026.
  - (c) The two speakers refer to different cohorts, or to "planned" vs "started".
- **What would settle it:** A training record (none supplied).

| Owner conclusion | Owner reasoning (source believed, and why) | Date |
|---|---|---|
| The actual start date is unresolved. | The speakers may refer to different cohorts or distinguish a planned start from an actual start. The unknown call date prevents a reliable calendar interpretation. | 19 Nov 2026 |

### C-28 · Which pulse round "dipped"
- **Visible from:** D2
- **Evidence:**
  - [EV-080 · T3 · D2]: Engineering scores dipped in the "July round" [D2].
  - [EV-073 · Pulse survey · D2] rows 4–7: Engineering engagement is flat for two quarters, dips slightly in Apr–Jun 2026, and drops more sharply in Jul–Sep 2026 (with one non-response) [D2].
  - [EV-108 · Email · D2] (29 Sep): the engineering pulse dropped "this quarter" [D2].
- **Candidate interpretations:**
  - (a) The "July round" is a survey run in July covering Apr–Jun (the small dip).
  - (b) It refers to the Jul–Sep quarter (the larger drop).
  - When the surveys were run within each quarter is not recorded.
- **What would settle it:** The survey run dates.

| Owner conclusion | Owner reasoning (source believed, and why) | Date |
|---|---|---|
| The survey shows a smaller dip in Apr–Jun and a larger decline in Jul–Sep. | The phrase "July round" cannot be mapped confidently to either quarter without the survey run date. Keep the two quarter results separate. | 19 Nov 2026 |

### C-32 · The attrition figure
- **Visible from:** D2
- **Evidence:**
  - [EV-080 · T3 · D2]: the Head of People gives an attrition figure, and the summary itself notes it is unclear whether it is a percentage, a headcount or a target [D2].
  - [EV-075 · T1 · D1]: the CEO feels attrition is high, but the Head of People calls it normal [D1].
  - [EV-084 · T3 · D2]: exit interviews mostly cite workload, which implies several exits [D2].
  - The HR export has one exit date. Leavers before the export may be missing (C-17) [D1].
- **Candidate interpretations:**
  - (a) An annual percentage.
  - (b) A headcount over a longer period.
  - (c) A company-wide figure (cf. C-01).
  - (d) A target.
- **What would settle it:** A leaver list (not supplied).

| Owner conclusion | Owner reasoning (source believed, and why) | Date |
|---|---|---|
| The spoken figure cannot be established as a percentage, headcount, target or annual rate. | The available HR export does not provide a complete historical leaver population. Do not publish it as a verified attrition metric. | 19 Nov 2026 |

### C-05 · GA status as described by the VP Engineering
- **Visible from:** D3
- **Evidence:**
  - [EV-095 · T5 · D3]: v3 is described as on track with a small buffer, the board date as still around September, and August incidents as the main blocker [D3].
  - [EV-071 · Jira · D3], Versions sheet: the GA release date was moved from 15 Sep to 15 Dec 2026, a change made on 2 Sep 2026 [D3].
  - The Jul–Sep board update says GA was moved, without giving a new date [D1].
- **Candidate interpretations:**
  - (a) The call took place before 2 Sep. It refers to the August incidents in the past tense, so it was possibly in late August.
  - (b) The call took place after 2 Sep, and the statement understates the change.
  - (c) The date given for the board refers to something other than the GA date (e.g. a board meeting).
- **What would settle it:** The call date (A-006); the missing T5 segment ([EV-099 · T5 · D3]).

| Owner conclusion | Owner reasoning (source believed, and why) | Date |
|---|---|---|
| The Jira version history records a move from 15 September to 15 December on 2 September. | The call date is unknown, so the VP's statement cannot be reliably placed before or after that change. Treat the version history as the dated plan and the transcript as time-ambiguous. | 19 Nov 2026 |

### C-06 · GA date told to the board vs the plan's condition
- **Visible from:** D3
- **Evidence:**
  - [EV-102 · Email · D3] (2 Jun): the VP Engineering makes the 15 Sep GA conditional on two backend hires landing by July [D3].
  - The Apr–Jun board update (sent 8 Jul) says GA is on track for 15 Sep and that two backend engineers will be added that quarter [D1].
  - [EV-069 · Hiring tracker · D2] rows 6–7: the two backend requisitions were opened in May, put on hold in August, and not filled [D2].
  - [EV-060 · Ledger · D1]: a recruiter advance for two backend roles [D1].
- **Candidate interpretations:**
  - (a) The board message was optimistic relative to the stated condition.
  - (b) The plan changed between 2 Jun and 8 Jul.
  - (c) On 8 Jul the hires still looked achievable (candidates were in the pipeline).
- **What would settle it:** Any record between 2 Jun and 8 Jul about the plan.

| Owner conclusion | Owner reasoning (source believed, and why) | Date |
|---|---|---|
| The June plan was conditional on two backend hires. | The July board update still presented the September date as on track and expected the hires that quarter. The later August hiring freeze meant the condition was not fulfilled. The evidence does not establish whether the July expectation was unreasonable at the time. | 19 Nov 2026 |

### C-07 · "Feature-complete"
- **Visible from:** D3
- **Evidence:**
  - The Jul–Sep board update says the product is in final hardening and its core engine is feature-complete [D1].
  - [EV-071 · Jira · D3], Issues sheet, at the 31 Oct export: the core-engine epic still has open stories, including one in progress, and the re-routing epic has more open points than the core engine [D3].
- **Candidate interpretations:**
  - (a) Different definitions (features vs hardening tasks).
  - (b) Jira was not maintained (see A-019).
  - (c) Framing in a board communication.
- **What would settle it:** Jira history or the v3 burndown ([EV-091 · T5 · D3], not supplied).

| Owner conclusion | Owner reasoning (source believed, and why) | Date |
|---|---|---|
| The board update describes the core engine as feature-complete and in hardening. | The October Jira export still contains open stories. These may reflect different dates or definitions of feature work and hardening. Do not claim that all delivery scope was complete. | 19 Nov 2026 |

### C-08 · Who was pulled off by the August incidents
- **Visible from:** D3
- **Evidence:**
  - [EV-095 · T5 · D3] names a person similar to, but not the same as, the SRE's first name as the one pulled off by the August incidents. [EV-096 · T5 · D3] gives that name the SRE and on-call role, and notes that the SRE's own name appears once later, possibly for the same person [D3].
  - [EV-070 · Incidents · D3] rows 4, 6 and 8: the SRE responded to all three August SEV1s. The similar name first appears as a responder in row 9 (27 Aug) [D3].
  - [EV-106 · Email · D3] (24 Aug): the SRE reports the month's third SEV1 and pauses performance tickets to stabilise the routing API [D3].
- **Candidate interpretations:**
  - (a) The SRE's name was misheard.
  - (b) The summariser conflated two people.
  - (c) The speaker was referring to both people.
- **What would settle it:** The call audio; nothing further is expected in the exports.

| Owner conclusion | Owner reasoning (source believed, and why) | Date |
|---|---|---|
| The incident export records a responder under the employed SRE's first name on all three August SEV1 incidents; the export carries first names only, so this is a name match, not a record-level link. | A similar name appears later as a responder, but the transcript and records do not establish that the names refer to the same person. Keep the ambiguous attribution unresolved. (Owner wording adjusted on 19 Nov 2026: the export does not itself identify "the employee SRE".) | 19 Nov 2026 |

### C-09 · The SRE and the similar rota name: one person or two
- **Visible from:** D3
- **Evidence:**
  - [EV-072 · On-call rota · D3] from row 10 (week of 17 Aug): the similar name alternates with the SRE as primary or secondary in the same weeks [D3].
  - [EV-070 · Incidents · D3]: both names appear as responders (the similar name in rows 9, 10, 12 and 14) [D3].
  - The contractor invoice PDF in `documents/`: an invoice for on-call SRE support, naming an individual by initial and surname, which refers to an earlier part-month invoice for late August [D3]. [EV-061 · Ledger · D1]: that earlier invoice [D1].
  - [EV-106 · Email · D3]: the contractor starts on the rota that week [D3].
  - The HR export has one SRE ([EV-065 · HR export · D1]) and excludes contractors [D1].
  - [EV-096 · T5 · D3]: unclear whether the two names are the same person [D3].
  - No record writes the similar name together with a surname.
- **Candidate interpretations:**
  - (a) Two people: the employed SRE and the contractor.
  - (b) One person, with a spelling variant.
  - (c) The contractor's actual name differs, and the rota name is a label or typo.
- **What would settle it:** A record naming the rota person with a surname; the vendor contract.

| Owner conclusion | Owner reasoning (source believed, and why) | Date |
|---|---|---|
| The evidence favours two people: the employed SRE and an incoming on-call contractor. | However, the supplied records do not establish the full identity behind the similar rota name. Do not merge the identities without a vendor or account mapping. | 19 Nov 2026 |

### C-10 · When the contractor started on the rota
- **Visible from:** D3
- **Evidence:**
  - [EV-106 · Email · D3] (Mon 24 Aug): the contractor starts on the rota "this week" [D3].
  - [EV-072 · On-call rota · D3] row 10: the contractor's rota name is primary in the week of 17 Aug [D3].
  - The contractor's first invoice period starts on 18 Aug (the contractor invoice PDF in `documents/` [D3]; [EV-061 · Ledger · D1] [D1]).
  - First incident response under that name: 27 Aug (`Incidents` row 9) [D3].
  - The rota's title row describes it as a copied wiki table [D3].
- **Candidate interpretations:**
  - (a) The rota was edited or back-filled after the fact.
  - (b) Billing and onboarding started before the email's "this week".
  - (c) The rota's week labels are off by one.
- **What would settle it:** The rota's edit history.

| Owner conclusion | Owner reasoning (source believed, and why) | Date |
|---|---|---|
| The records show different dates for the rota entry, invoice period and email announcement. | The rota may have been back-filled. The first actual operational shift cannot be established from the supplied material. | 19 Nov 2026 |

### C-11 · Who froze the backend hiring
- **Visible from:** D3
- **Evidence:**
  - [EV-098 · T5 · D3]: the VP Engineering says finance froze the two backend roles [D3].
  - [EV-104 · Email · D2] (12 Aug): the CFO pauses the two requisitions until the funding round closes, with the CEO and the Talent Partner copied [D2].
  - [EV-105 · Email · D2] (12 Aug): the VP Engineering accepts and says the team will absorb it [D2].
  - The Jul–Sep board update says hiring is paused while the round closes [D1].
- **Candidate interpretations:**
  - (a) Both accounts are accurate (finance paused the roles, and engineering accepted at the time), and they differ only in framing.
  - (b) The positions changed after 12 Aug.
- **What would settle it:** —

| Owner conclusion | Owner reasoning (source believed, and why) | Date |
|---|---|---|
| The CFO's 12 August email initiated the pause on the two requisitions. | The VP Engineering acknowledged it and said the team would absorb the impact. These accounts are consistent; they describe the same decision from different perspectives. | 19 Nov 2026 |

### C-17 · Engineering team size in Oct–Dec 2025
- **Visible from:** D3
- **Evidence:**
  - [EV-073 · Pulse survey · D2] row 4: the Engineering team size in Oct–Dec 2025 is one more than the HR export supports [D2].
  - HR export: four of the engineering employees joined before 2026; the fifth joined in early 2026 [D1].
  - [EV-112 · Git log · D3]: no additional engineer identity appears in 2025 [D3].
- **Candidate interpretations:**
  - (a) The pulse counted someone not in the HR export (a former engineer, a contractor, or someone from another team).
  - (b) The HR export lists current staff only, so leavers are missing.
  - (c) The survey tool defines teams differently.
- **What would settle it:** A leaver list (not supplied).

| Owner conclusion | Owner reasoning (source believed, and why) | Date |
|---|---|---|
| The pulse survey records one more Engineering team member than the supplied HR export supports. | The difference could reflect a former employee, contractor or different team definition. The available evidence does not identify the additional person or establish an extra employee. | 19 Nov 2026 |

### C-19 · Marketing spend for the half-year
- **Visible from:** D3
- **Evidence:**
  - [EV-087 · T4 · D3]: the Head of Marketing gives a half-year spend figure with no unit and says finance counts differently. [EV-089 · T4 · D3]: the summit went slightly over budget [D3].
  - [EV-077 · T2 · D1]: the CFO says the marketing figures never match finance's own [D1].
  - The GL marketing cost-centre lines for Apr–Sep (search-ads lines plus the event-vendor lines of C-18) do not equal the spoken figure under any obvious unit reading. The extract covers selected cost centres only and has no social-ads line (GR-05) [D1].
- **Candidate interpretations:**
  - (a) A unit question.
  - (b) Spend outside the extracted cost centres.
  - (c) A different period or basis (committed vs posted).
- **What would settle it:** The campaign sheet ([EV-086 · T4 · D3]) or the budget-vs-actuals sheet ([EV-076 · T2 · D1]); neither was supplied.

| Owner conclusion | Owner reasoning (source believed, and why) | Date |
|---|---|---|
| The spoken half-year figure cannot be reconciled to the selected general-ledger extract. | That extract does not cover all marketing costs, including the missing social-ads line. Preserve the spoken figure as unverified and do not present the ledger extract as total marketing spend. | 19 Nov 2026 |

### C-20 · Summit outcome
- **Visible from:** D3
- **Evidence:**
  - [EV-087 · T4 · D3]: the Head of Marketing calls the quarter the best ever for pipeline. [EV-090 · T4 · D3]: a sales colleague's view that the summit leads are cold, which the Head of Marketing dismisses [D3].
  - The Jul–Sep board update reports the summit's registrations as a record for the company [D1].
- **Candidate interpretations:**
  - (a) Registrations and pipeline are different measures.
  - (b) The claim about lead quality is second-hand.
  - (c) Both views are partly right.
- **What would settle it:** A CRM export (not supplied, GR-04).

| Owner conclusion | Owner reasoning (source believed, and why) | Date |
|---|---|---|
| The board update supports a record number of registrations. | The pipeline claim and the sales colleague's concern about lead quality refer to different funnel stages. Without a CRM export, the summit's commercial success cannot be established. | 19 Nov 2026 |

### C-26 · Marketing reporting line
- **Visible from:** D3
- **Evidence:**
  - [EV-068 · HR export · D1]: the Head of Marketing reports to the CEO [D1].
  - [EV-085 · T4 · D3]: the Head of Marketing has heard that marketing will report to a new CRO, with no date [D3].
  - [EV-100 · T6b · D3]: the CEO says marketing will report to the new CRO in one of the next two months, still being finalised [D3].
  - No CRO appears in the HR export [D1], and the hiring tracker has no CRO requisition [D2].
- **Candidate interpretations:**
  - (a) A planned change with an uncertain effective date and a role not yet filled or named.
  - (b) A rumour at the time of T4 that the CEO later confirmed in T6b.
- **What would settle it:** An appointment record and an effective date.

| Owner conclusion | Owner reasoning (source believed, and why) | Date |
|---|---|---|
| The HR export shows the current reporting line as the CEO. | The later CEO statement confirms an intended move to a CRO, but says the arrangement is still being finalised. Treat this as a planned future change, not an effective reporting-line change. | 19 Nov 2026 |

### C-27 · An engineer's remote arrangement
- **Visible from:** D3
- **Evidence:**
  - [EV-063 · HR export · D1]: the Senior Frontend Engineer's location is recorded as remote [D1].
  - [EV-107 · Email · D2] (18 Sep): that engineer asks to make the arrangement permanent. No reply is in the extract (GR-05) [D2].
  - [EV-096 · T5 · D3]: the VP Engineering describes the engineer as remote now [D3].
- **Candidate interpretations:**
  - (a) A current but temporary arrangement.
  - (b) The HR export records the location, not the contractual status.
  - (c) A decision exists outside the extract.
- **What would settle it:** An HR decision record.

| Owner conclusion | Owner reasoning (source believed, and why) | Date |
|---|---|---|
| The HR export and VP statement support that the engineer is currently remote. | The employee's request to make it permanent is not evidence of approval. The contractual permanence of the arrangement remains unresolved. | 19 Nov 2026 |

### C-31 · Why the GA date moved
- **Visible from:** D3
- **Evidence:**
  - The Jul–Sep board update attributes the move to unplanned reliability work after August [D1].
  - [EV-095 · T5 · D3]: August incidents were the main blocker. [EV-098 · T5 · D3]: finance froze the backend hiring [D3].
  - [EV-102 · Email · D3] (2 Jun): the original date depended on two backend hires; the requisitions were paused in August (C-11) [D3].
  - [EV-106 · Email · D3] (24 Aug): performance tickets were paused to stabilise the routing API [D3].
  - [EV-071 · Jira · D3]: the GA date changed on 2 Sep 2026, and roughly half the story points were open at the export [D3].
- **Candidate interpretations:**
  - (a) Reliability work, as the board says.
  - (b) The hiring condition failed.
  - (c) The remaining scope.
  - (d) A combination. The sources emphasise different causes.
- **What would settle it:** The v3 burndown and the August postmortems ([EV-092 · T5 · D3]), neither of which was supplied.

| Owner conclusion | Owner reasoning (source believed, and why) | Date |
|---|---|---|
| The evidence supports several contributing factors: August reliability incidents, the backend hiring freeze and remaining delivery scope. | The board emphasises reliability work, while other records show staffing and scope issues too. Treat the cause as multifactorial; the evidence does not establish the relative contribution of each factor. | 19 Nov 2026 |

### I-01 · A personal-account git identity
- **Visible from:** D3
- **Evidence:**
  - [EV-112 · Git log · D3], `author_email` column: one identity is a personal code-hosting account, not a work address. Its name part matches the Staff Engineer's initial and surname. It commits only to the engine repository, over the whole period of the log [D3].
  - The Staff Engineer's work address also commits to that repository [D3].
  - The HR export has one person with that surname ([EV-062 · HR export · D1]) [D1].
  - Whatever is concluded, commit counts and commit timing are not used as productivity or wellbeing signals (`ARCHITECTURE.md` P7, §16.6).
- **Candidate interpretations:**
  - (a) The Staff Engineer's personal account.
  - (b) A different person with the same initial and surname (not in the HR export).
- **What would settle it:** A code-hosting account mapping.

| Owner conclusion | Owner reasoning (source believed, and why) | Date |
|---|---|---|
| The personal account may belong to the Staff Engineer, given the matching name and repository activity, but the supplied evidence does not prove account ownership. | Keep the identity unlinked until an account mapping is available. Commit counts and timing must not be used as productivity or wellbeing signals. | 19 Nov 2026 |

## Part D. Open ambiguities with no conflicting source

| ID | Ambiguity | Evidence | Note | Visible from | Owner note |
|---|---|---|---|---|---|
| U-01 | Which figure the board wants by a stated month | [EV-043 · T1 · D1] (an unidentified second speaker [EV-037 · T1 · D1]) | No record identifies it | D1 | |
| U-02 | When the books moved accounting systems (the call is unsure of the month) | [EV-047 · T2 · D1] | The ledger extract starts in Apr 2026 [EV-015 · Ledger · D1], and the call warns that older numbers may not match | D1 | |
| U-03 | An engineer the Head of People is worried about, not named on the call | [EV-078 · T3 · D2] [D2] | No record identifies them. Candidate identities are deliberately not proposed (`ARCHITECTURE.md` P7: no individual attrition or wellbeing inference) | D2 | The individual discussed by the Head of People is not identified in the supplied records. Keep the person unnamed. Do not infer an identity or individual attrition or wellbeing condition. (19 Nov 2026) |
| U-04 | Content of the audio gap in the VP Engineering call | [EV-094 · T5 · D3], [EV-099 · T5 · D3] [D3] | Any claim that T5 "did not mention X" is unsafe | D3 | The audio gap is not evidence that a topic was not discussed. Do not make absence claims about the missing segment. Restrict conclusions to the available transcript portions and other records. (19 Nov 2026) |

---

## Held for later release

These items exist in the owner's private working register. They are withheld for now because their evidence includes later-pack material. Topics are not listed, because a topic could reveal later content.

| Release pack | Items added (full) | Items whose Day 1 version is replaced by the full version |
|---|---|---|
| D2 (Tue 17 Nov) | Published 19 Nov 2026 (M-11; C-12, C-13, C-14, C-16, C-28, C-32; U-03) | M-03, M-04, M-09, M-10; C-01, C-30 |
| D3 (Wed 18 Nov) | Published 19 Nov 2026 (M-05, M-06, M-14; C-05 to C-11, C-17, C-19, C-20, C-26, C-27, C-31; I-01; U-04) | M-07, M-12 |
| D4 (Thu 19 Nov) | C-15 (its D2 evidence alone can be released on D2), C-24, C-25, C-29 | M-01, M-13 |

---

## Revision log

| Date | Change | By |
|---|---|---|
| 16 Nov 2026 | Day 1 public edition prepared from the owner's private working register:<br>• D1 items only, plus Day 1 versions of ten mixed-pack items;<br>• opaque evidence references;<br>• no quotations, names, identifiers, figures or individual pay details;<br>• C-21 generalised to a payroll-process finding.<br>Every owner-conclusion field is blank | AI (Claude Code), for owner review |
| 19 Nov 2026 | D2/D3 items (26) | Published after release with the owner's conclusions; evidence cited by new opaque references; C-08 wording adjusted to the record | Owner conclusions; publication by AI (Claude Code) |
