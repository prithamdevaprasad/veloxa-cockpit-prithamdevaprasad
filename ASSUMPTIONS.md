# ASSUMPTIONS

**Release status: material released up to D1 (Mon 16 Nov 2026).** This file currently contains only entries resting on the Day 1 release pack, the hackathon email (`email.md`) or `ARCHITECTURE.md`. Entries that depend on later packs are added on those packs' release days (see "Held for later release").

This file lists the assumptions that the architecture and the data analysis rely on. Each entry gives:

- the date;
- the assumption;
- the source it rests on;
- a confidence level;
- what would change the owner's mind.

Section 0 separately records **ground rules and observed facts**: instructions, release rules and directly observable properties of the material. They are not hypotheses.

**Rules for this file**

- **Revise, never delete.** A changed entry gets a new revision line (`A-0xx r2`, …) with the date and the reason, and the earlier text stays. A withdrawn entry is marked `Withdrawn`, and a reclassified entry keeps a stub at its old ID.
- **Owner-owned.** Entries were prepared by AI on 16 Nov 2026.
  - Assumptions are marked `Draft: owner to review`, and their confidence values are proposals.
  - Ground rules and observed facts are marked `Recorded: owner to review`.
  - Nothing here is approved until the owner confirms it.
- **Not a place for conflicts.** Conflicts and the owner's conclusions belong in `TRANSCRIPT_NOTES.md`.
- **Public-repository safety.**
  - Challenge sources are cited by **opaque references** (`EV-nnn`), with source type and release pack. The mapping to exact source locations is kept privately by the owner.
  - The hackathon email and `ARCHITECTURE.md` (§n) are cited directly.
  - No quotations, personal names, identifiers or figures appear.
- **Persona inventions** for the 14 agents get IDs from A-101 onwards when persona specs are written. None have been written yet.

| Pack | Entries currently in this file |
|---|---|
| — | GR-01, A-021 |
| D1 | GR-02, GR-03, GR-04, GR-05; A-002 to A-006, A-008 to A-012, A-016, A-017 |

---

## 0. Ground rules and observed facts

### GR-01 · Release packs are sealed knowledge batches (was A-001)
| | |
|---|---|
| Kind | Release rule (instruction) |
| Date | 16 Nov 2026 |
| Statement | The cockpit treats each pack's content as unknown before 10:00 IST on that pack's release day, whatever dates the material itself carries. The four packs are four sealed admission batches (§8.2, §25.1) |
| Source | `email.md` line 14 (all material supplied up front), lines 40–44 (one pack per day, released at 10:00), line 54 (later material must not leak into earlier answers) |
| Pack | — |
| Would change my mind | An organiser instruction giving a different release time or basis |
| Status | Recorded: owner to review |

### GR-02 · Timestamps that carry an explicit offset are converted, not assumed (from A-003)
| | |
|---|---|
| Kind | Observed fact |
| Date | 16 Nov 2026 |
| Statement | Where a source timestamp carries an explicit UTC offset, it is converted to an instant using that offset. No time-zone assumption is needed for these values. Each later export is checked for this when its pack is released |
| Source | [EV-012 · Email · D1] (a date header with an explicit IST offset) |
| Pack | D1 |
| Status | Recorded: owner to review |

### GR-03 · Machine summaries are not verbatim speech (was A-022)
| | |
|---|---|
| Kind | Instruction (ground rule of the challenge) |
| Date | 16 Nov 2026 |
| Statement | Fragments in the call summaries are what the summariser wrote, not verbatim speech. Speaker attribution may be wrong, and content lost in any audio gap is unknown. Transcript-only claims are graded no higher than C (§13.5) unless a record corroborates them |
| Source | `email.md` line 74 (names, numbers and tools may be misheard; speakers may be wrong or biased); [EV-037 · T1 · D1] (one speaker is unidentified) |
| Pack | D1 |
| Status | Recorded: owner to review |

### GR-04 · Sources that were not supplied are absent, not empty (was A-013, D1 part)
| | |
|---|---|
| Kind | Observed fact, with the behaviour it implies |
| Date | 16 Nov 2026 |
| Statement | The Day 1 material refers to a CRM export, a chat channel and a budget-vs-actuals sheet, none of which is supplied. Nor are sources outside the README's folder listing. A question that needs one of them gets "I don't know", naming the missing source. It never gets an answer implying coverage |
| Source | [EV-007 · README · D1] (the folders supplied); [EV-044, EV-045 · T1 · D1]; [EV-051 · T2 · D1] |
| Pack | D1 |
| Would change my mind | Any of these exports being supplied |
| Status | Recorded: owner to review |

### GR-05 · Email and ledger extracts are partial (was A-014)
| | |
|---|---|
| Kind | Observed fact |
| Date | 16 Nov 2026 |
| Statement | The email set is labelled as selected threads and the ledger as selected cost centres. So the absence of a reply or of a ledger line is not evidence that none exists (e.g. an email thread with no recorded follow-up) |
| Source | [EV-008 · README · D1]; [EV-015 · Ledger · D1]; [EV-014 · Email · D1] |
| Pack | D1 |
| Would change my mind | A statement that either extract is complete |
| Status | Recorded: owner to review |

GR-06 and GR-07 are held for later release.

---

## 1. Schedule and time basis

### A-001 · Reclassified
Reclassified as **GR-01** on 16 Nov 2026: it is an instruction, not a hypothesis. Its content is unchanged in substance.

### A-002 · The email supersedes the Candidate Brief where they differ
| | |
|---|---|
| Date | 16 Nov 2026 |
| Assumption | Where the brief and the email disagree, the email governs: the length of the challenge, the number of packs, live sessions and the code-freeze time. **Kept as an assumption**: the email calls itself a compressed version of the challenge, but it does not say in terms that it overrides the brief |
| Source | `email.md` lines 3, 6, 16–24 and 38; against [EV-003, EV-004, EV-005 · Brief · D1] |
| Pack | D1 |
| Confidence (proposed) | High |
| Would change my mind | An organiser statement that the brief governs on any of these points |
| Status | Draft: owner to review |

### A-003 · Date-only values are IST calendar dates
| | |
|---|---|
| Date | 16 Nov 2026 |
| Assumption | Date-only values in the exports are IST calendar dates. A question about "date X" means the end of day X in IST. Timestamps with explicit offsets are covered by GR-02 |
| Source | `email.md` line 37 (the timeline is given in IST); [EV-012 · Email · D1] |
| Pack | D1 |
| Confidence (proposed) | High |
| Would change my mind | An export documented in a different time zone |
| Status | Draft: owner to review |

### A-004 · Exports are snapshots as at the data-room export date
| | |
|---|---|
| Date | 16 Nov 2026 |
| Assumption | Status-like fields (HR status, title, manager and location; issue status; requisition status) are known only "as at extraction", 31 Oct 2026. Earlier states are unknown unless a dated field records them (rule T3, §8.4) |
| Source | [EV-006 · README · D1]; [EV-026 · HR export · D1] |
| Pack | D1 |
| Confidence (proposed) | High |
| Would change my mind | A history or audit-trail export for any of these systems |
| Status | Draft: owner to review |

### A-005 · HR join dates were recorded no later than the join date
| | |
|---|---|
| Date | 16 Nov 2026 |
| Assumption | For RECORDED_BY questions, an HR-export join date is treated as recorded on or before the join date itself (implied recording, rule T4) |
| Source | [EV-028 · HR export · D1]; §8.4 T4; §26.3 (listed as unverified) |
| Pack | D1 |
| Confidence (proposed) | Medium |
| Would change my mind | An HR audit trail showing join dates entered after the event |
| Status | Draft: owner to review |

### A-006 · Call dates are unknown; the file creation time is only an upper bound
| | |
|---|---|
| Date | 16 Nov 2026 |
| Assumption | No call summary states its call date. The only time evidence is each file's creation time, which is used as an upper bound on when the content was recorded. There is no lower bound |
| Source | [EV-053 · T1, T2 file properties · D1]. Each later summary's properties are to be checked when its pack is released |
| Pack | D1 |
| Confidence (proposed) | Medium. The creation time may reflect when the files were generated |
| Would change my mind | A dated reference to a call in any record, or file metadata showing a different date |
| Status | Draft: owner to review |

A-007 is held for later release.

### A-008 · Cloud-hosting postings cover the previous month's usage
| | |
|---|---|
| Date | 16 Nov 2026 |
| Assumption | Each monthly cloud-hosting ledger line is posted early in the month for the previous month's usage, so its posting date is not its service period (valid time) |
| Source | [EV-017 · Ledger · D1] (the narration states the usage month) |
| Pack | D1 |
| Confidence (proposed) | High |
| Would change my mind | Provider invoices showing a different billing period |
| Status | Draft: owner to review |

## 2. Units, conventions and scope

### A-009 · Currency and unit conventions in formal documents
| | |
|---|---|
| Date | 16 Nov 2026 |
| Assumption | In formal documents, "L" means lakh (10⁵ rupees) and "Cr" means crore (10⁷ rupees). Ledger and payroll amounts are in rupees. **Spoken numbers in call summaries are given no assumed unit** unless the summary states one (see C-02, C-03) |
| Source | [EV-009, EV-010 · Board update · D1]; [EV-016 · Ledger · D1]; [EV-041 · T1 · D1] |
| Pack | D1 |
| Confidence (proposed) | High |
| Would change my mind | A finance document using other units |
| Status | Draft: owner to review |

### A-010 · The financial year runs April to March
| | |
|---|---|
| Date | 16 Nov 2026 |
| Assumption | The financial year runs April to March. Which label applies to Apr 2026 – Mar 2027 is **not** assumed (C-04) |
| Source | [EV-046 · T2 · D1]; [EV-022 · Payroll register · D1] |
| Pack | D1 |
| Confidence (proposed) | High for the period; label unresolved |
| Would change my mind | A finance document with a different year boundary |
| Status | Draft: owner to review |

### A-011 · The 14 agent subjects
| | |
|---|---|
| Date | 16 Nov 2026 |
| Assumption | The 14 people are the 14 HR-export employees in the Engineering, Finance, People & Ops and Marketing departments. <br>• Excluded: the CEO and the Sales and Support staff. <br>• Contractors are not agents (the HR export excludes them). <br>• "HR" in the brief is read as the export's People & Ops department |
| Source | [EV-002 · Brief · D1]; `email.md` line 48; [EV-027, EV-026 · HR export · D1] |
| Pack | D1 |
| Confidence (proposed) | Medium–High. The count and the four departments match exactly, but no source lists the people |
| Would change my mind | Any instruction naming a different set, or including the CEO |
| Status | Draft: owner to review. **Not approved** |

### A-012 · A recorded exit date is treated as that agent's knowledge cutoff
| | |
|---|---|
| Date | 16 Nov 2026 |
| Assumption | Where the HR export records an exit date for an employee who is on notice, that employee's agent gets a knowledge cutoff at the end of that date (an alumnus agent, §14.6). No record confirms that the exit took place |
| Source | [EV-030, EV-035 · HR export · D1] |
| Pack | D1 |
| Confidence (proposed) | Medium–High |
| Would change my mind | Any record of an extension or withdrawal of notice |
| Status | Draft: owner to review |

### A-013 · Reclassified
Reclassified on 16 Nov 2026 as **GR-04** (the Day 1 part) and **GR-07** (a later-pack part, held for later release). These are observed facts.

### A-014 · Reclassified
Reclassified as **GR-05** on 16 Nov 2026: it is an observed fact.

### A-015 · Reclassified
Reclassified as **GR-06** on 16 Nov 2026: it is an observed fact. GR-06 is held for later release.

## 3. Access model (hackathon stand-in for source ACLs)

### A-016 · Who could see what, derived from the records
| | |
|---|---|
| Date | 16 Nov 2026 |
| Assumption | No ACL, sharing or group export exists. For the employee agents' knowledge boundaries (KB, §14.2), access is therefore derived from the records: <br>• **Party relations** (participation or access, §10.7): an email to its sender and recipients; a ticket to its assignee; an incident to its responder. <br>• **Role entitlements** (labelled `role_plausible`, §14.2): <br>  – issue-tracker, version-control, incident and rota records → the engineering team; <br>  – ledger lines → finance and the CEO; <br>  – a payroll line → its subject and finance, at line level; <br>  – a leave row → its subject, its approver and People & Ops; <br>  – survey results → People & Ops, the CEO and team leads, aggregate only; <br>  – board updates → the leadership team, read as the CEO's direct reports; <br>  – call summaries → their named speakers; <br>  – voice notes → the CEO. <br>These are conventions, not evidence. They are used only to *narrow* what an agent could know, never to grant anyone current access |
| Source | [EV-007 · README · D1] (systems supplied; no ACL or sharing data); [EV-029 · HR export · D1]; [EV-012 · Email · D1] (sender and recipient headers); [EV-009, EV-010 · Board update · D1] (no distribution list); `email.md` line 56; §25.1; §10.7 |
| Pack | D1 |
| Confidence (proposed) | Medium for the party relations; Low–Medium for the role entitlements, especially "leadership team" |
| Would change my mind | Any sharing or ACL export; an owner rule for a data class |
| Status | Draft: owner to review. **Not approved** |

## 4. Policy parameters and interpretations

### A-017 · Individual pay is protected from the CEO
| | |
|---|---|
| Date | 16 Nov 2026 |
| Assumption | Individual pay values are never shown to the CEO or other non-privileged principals. Only aggregates that pass the disclosure auditor (§15) are released. The CFO's statement to this effect is treated as tenant policy |
| Source | [EV-048 · T2 · D1] |
| Pack | D1 |
| Confidence (proposed) | Medium. It is a machine-summarised call statement; no written policy exists |
| Would change my mind | A written compensation-visibility policy, or a CEO/CFO instruction that says otherwise |
| Status | Draft: owner to review. **Not approved** |

A-018, A-019 and A-020 are held for later release.

### A-021 · CEO voice notes are requests, not statements of fact
| | |
|---|---|
| Date | 16 Nov 2026 |
| Assumption | Each CEO voice note is treated as a product request to respond to, not as a statement of fact about the company, unless it reports a fact. Such a report is admitted as a *reported*, unverified status. Second-hand claims inside a voice note are not treated as records of the named person's view. This is applied to each voice note when its pack is released |
| Source | `email.md` line 56 (each voice note gets a written response: make the change or push back) |
| Pack | — |
| Confidence (proposed) | Medium–High |
| Would change my mind | A record from the named person confirming the statement |
| Status | Draft: owner to review |

### A-022 · Reclassified
Reclassified as **GR-03** on 16 Nov 2026: it is a ground rule stated in the email.

A-023 is held for later release.

## 5. Assumptions considered and deliberately not made

These are open conflict questions for the owner and are **not** assumed:

- That two near-identical vendor names in the ledger are one vendor (C-18).
- The company's true headcount (C-01), ARR (C-02) or burn (C-03).

## 6. Persona assumptions (agents)

None have been recorded yet. Fictional personal details for the 14 agents are written by the owner, or written by AI and approved by the owner, when the persona specs are created. Each item gets an ID from A-101 onwards and is `assumption`-class data that can never be cited as evidence (§14.4).

## Held for later release

| Release pack | Entries |
|---|---|
| D2 (Tue 17 Nov) | A-018, A-023 |
| D3 (Wed 18 Nov) | GR-06, GR-07; A-007, A-019 |
| D4 (Thu 19 Nov) | A-020 |

## Revision log

| Date | Entry | Change | By |
|---|---|---|---|
| 16 Nov 2026 | All | Day 1 public edition prepared from the owner's private working file: <br>• D1, email and architecture entries only; <br>• opaque evidence references; <br>• no quotations, names, identifiers or figures. <br>No assumption approved | AI (Claude Code), for owner review |
