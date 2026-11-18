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
- **Persona inventions** for the 14 agents are A-101 to A-114 (section 6).

| Pack | Entries currently in this file |
|---|---|
| — | GR-01, A-021, A-025 |
| D1 | GR-02, GR-03, GR-04, GR-05; A-002 to A-006, A-008 to A-012, A-016, A-017, A-024, A-026 |

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

## 4a. Ingestion (Day 1 foundation)

### A-024 · Data class assigned to each D1 source
| | |
|---|---|
| Date | 16 Nov 2026 |
| Assumption | On admission, each source is given the most restrictive §6.3 data class that fits its content (`config/source_catalogue.toml`): <br>• payroll-register rows → `sensitive_personal`; <br>• HR-export rows → `personal`; <br>• ledger rows → `confidential`, except the ledger's payroll line → `payroll_aggregate` (§15.3); <br>• emails → `private_comms`; <br>• board updates and call summaries → `confidential`; <br>• the brief and README → `internal`. <br>The class drives access control: individual payroll rows are unreadable by the application role (§9.4) |
| Source | §6.3, §9.4, §15.3; A-017 ([EV-048 · T2 · D1]); [EV-007 · README · D1] (systems supplied) |
| Pack | D1 |
| Confidence (proposed) | Medium. This is a classification policy, not a fact about the data. Erring restrictive is deliberate |
| Would change my mind | A tenant data-classification policy, or the owner reclassifying a source |
| Status | **Approved by the owner (16 Nov 2026), with strict fail-closed handling:** any file without an explicit catalogue rule is rejected and never read, and a new source needs a reviewed rule before it can be admitted |

### A-025 · File-drop extraction time is the pack's release time
| | |
|---|---|
| Date | 16 Nov 2026 |
| Assumption | For the file-drop connector, each record's extraction time (E, §8.1) is its pack's release time. That keeps E ≤ admission, as §8.1 requires. Dates the files themselves state (export dates, document dates) are source-recorded time (R), handled with the temporal model, and are not used as E |
| Source | §8.1, §8.2; GR-01 |
| Pack | — |
| Confidence (proposed) | Medium–High |
| Would change my mind | A connector design that records the actual receipt time of each file |
| Status | Superseded by A-025 r2 (the r1 text above is kept as history) |

**A-025 r2 (16 Nov 2026, after owner review).** *Extraction time is the actual time the connector read the file.*

| | |
|---|---|
| Assumption | Each record's extraction time E (§8.1) is the wall-clock time at which the connector read that file's bytes: the bytes it hashed and parsed. Four times are kept distinct: <br>• `release_at`: the pack's scheduled release, used as the batch's knowledge time; <br>• `extracted_at`: when each file was actually read; <br>• `admitted_at`: when the admission transaction started; <br>• `sealed_at`: when the batch was sealed. <br>The database enforces `release_at ≤ extracted_at ≤ admitted_at ≤ sealed_at`. Dates the files themselves state (export or document dates) remain source-recorded time R, not E |
| Reason for revision | r1 set E to the release time. That conflated knowledge time with extraction and could not record when the bytes were actually read (owner review, 16 Nov 2026) |
| Source | §8.1, §8.2; GR-01 |
| Pack | — |
| Confidence (proposed) | High |
| Would change my mind | A connector that receives files before their release time (not possible here: admission and reading are refused before release) |
| Status | **Approved by the owner (16 Nov 2026)** (the corrected version using actual extraction timestamps) |

### A-026 · Data-room templates are not evidence
| | |
|---|---|
| Date | 16 Nov 2026 |
| Assumption | The data room's template files are worksheets provided for the candidate's own use. They are hashed and registered in the raw manifest, but no source records are extracted from them, so they can never be cited as evidence |
| Source | [EV-007 · README · D1] (the folder listing describes templates as worksheets for the candidate's own use) |
| Pack | D1 |
| Confidence (proposed) | High |
| Would change my mind | A template that turns out to contain company data |
| Status | **Approved by the owner (16 Nov 2026)** |

## 5. Assumptions considered and deliberately not made

These are open conflict questions for the owner and are **not** assumed:

- That two near-identical vendor names in the ledger are one vendor (C-18).
- The company's true headcount (C-01), ARR (C-02) or burn (C-03).

## 6. Persona assumptions (agents)

The records are silent on the agents' personal lives, so these details are **invented** and logged here (brief:
"invent it and log it as an assumption"). They come from a deterministic generator (`veloxa.agents.persona`, version
1), seeded only by the agent ID: never by a person's name, gender, age or origin, so nothing is
inferred from identity, and every run produces the same values. Each value is also stored in the Assumption Ledger
(`runtime.assumption`, migration 0021) with `origin = synthetic_persona` and `scope = persona`, which the database
enforces. Persona assumptions are **never evidence**: they are not used for workload, attendance, performance, pay,
promotion, employment decisions, risk ranking or access, and the cockpit does not state them as facts about the
represented person. Work facts (role, department, manager, assigned work) come only from admitted records.

Agents are listed by slot ID; the mapping of a slot to an HR record is an owner-approved decision (18 Nov 2026,
exact employee ID or e-mail only) kept in the database, not in this public file.

Common fields for every entry below:

| | |
|---|---|
| Date | 18 Nov 2026 |
| Source | None: the records are silent (`email.md` section 4, "a personal life"; brief, "invent it and log it") |
| Confidence | Not applicable: fictional by design |
| Would change my mind | Any admitted record about the person's life; the invented detail is then withdrawn (status `withdrawn`), never kept alongside a fact |
| Status | Generated by AI (code); owner to review |

### A-101 · Persona of `agent-eng-01`

| Category | Invented detail | Ledger ID |
|---|---|---|
| interests | learning a third language | `AS-2649213f972e` |
| hobbies | a book club that meets monthly | `AS-12d85f547b46` |
| background | came from a larger company looking for more ownership | `AS-3a48d6b07eef` |
| goals | wants a calmer release cadence | `AS-d0c908487028` |
| communication style | direct and brief; prefers written updates | `AS-b1f92b2107cb` |
| routine | reviews the week every Sunday evening | `AS-06f45d46efb3` |
| living context | recently moved closer to the office | `AS-b0e96c12cb18` |
| preferences | headphones on for focus | `AS-ff6e82182482` |
| personality | steady under pressure | `AS-487ce6aeb41a` |

### A-102 · Persona of `agent-eng-02`

| Category | Invented detail | Ledger ID |
|---|---|---|
| interests | trail running | `AS-aaa6038dcffc` |
| hobbies | a book club that meets monthly | `AS-67d900f96678` |
| background | studied at a (fictional) state university before joining a startup | `AS-24d784f8c337` |
| goals | wants a calmer release cadence | `AS-543890b1c370` |
| communication style | informal; thinks out loud | `AS-faadc1803a8f` |
| routine | walks during long calls | `AS-d679656c963c` |
| living context | lives alone with a cat | `AS-39e3a6ff8c84` |
| preferences | handwritten notes for planning | `AS-640a8aa18769` |
| personality | steady under pressure | `AS-de689e0a8ca9` |

### A-103 · Persona of `agent-eng-03`

| Category | Invented detail | Ledger ID |
|---|---|---|
| interests | trail running | `AS-d3886eb09055` |
| hobbies | volunteering at an animal shelter | `AS-2093c5a265bb` |
| background | started in a services firm and moved to product | `AS-b98a9c78f4b8` |
| goals | plans to take a long holiday after the next launch | `AS-96ec24c20ad4` |
| communication style | structured; likes agendas and follow-up notes | `AS-f8b04d11e6fc` |
| routine | keeps Friday afternoons for tidying up loose ends | `AS-5c63e1aab91c` |
| living context | recently moved closer to the office | `AS-fa673ca1bf1c` |
| preferences | plain-text email | `AS-f330d4dc5f96` |
| personality | dry sense of humour | `AS-b211006a2270` |

### A-104 · Persona of `agent-eng-04`

| Category | Invented detail | Ledger ID |
|---|---|---|
| interests | pottery | `AS-59fd670280bb` |
| hobbies | restoring an old bicycle | `AS-a20c46a5a8e4` |
| background | came from a larger company looking for more ownership | `AS-64e15fc88779` |
| goals | is working towards a professional certification | `AS-98ddadcc454c` |
| communication style | structured; likes agendas and follow-up notes | `AS-97740108c2a2` |
| routine | walks during long calls | `AS-b854c65c3378` |
| living context | commutes by metro | `AS-0786826650e3` |
| preferences | headphones on for focus | `AS-3dadc3f401b5` |
| personality | dry sense of humour | `AS-a31a50a21b0e` |

### A-105 · Persona of `agent-eng-05`

| Category | Invented detail | Ledger ID |
|---|---|---|
| interests | pottery | `AS-01d0a2d27a61` |
| hobbies | cooking for friends | `AS-253517e8df90` |
| background | first job after a master's programme | `AS-3a885d959281` |
| goals | hopes to lead a project end to end | `AS-97efc9333ad9` |
| communication style | informal; thinks out loud | `AS-bbc7b638d216` |
| routine | starts early and protects mornings for focused work | `AS-abc766ae5b53` |
| living context | shares a flat with a friend | `AS-64c0f14b4b1b` |
| preferences | dark mode everywhere | `AS-b888bb49537c` |
| personality | dry sense of humour | `AS-584698599611` |

### A-106 · Persona of `agent-fin-01`

| Category | Invented detail | Ledger ID |
|---|---|---|
| interests | home baking | `AS-1c4484991e08` |
| hobbies | writing short fiction | `AS-bd109788040a` |
| background | moved cities twice for work before Veloxa | `AS-4032c150d557` |
| goals | is working towards a professional certification | `AS-646dd10fe212` |
| communication style | warm, asks questions before answering | `AS-aa05057a8d28` |
| routine | starts early and protects mornings for focused work | `AS-d043de2c46fa` |
| living context | lives alone with a cat | `AS-2d18e9a4fc81` |
| preferences | plain-text email | `AS-4b1208abbd05` |
| personality | steady under pressure | `AS-db698cf3f817` |

### A-107 · Persona of `agent-fin-02`

| Category | Invented detail | Ledger ID |
|---|---|---|
| interests | board games | `AS-9db1dbf7ea35` |
| hobbies | writing short fiction | `AS-e43c1cc929de` |
| background | took a year out to travel before this role | `AS-2413b93823ee` |
| goals | wants to mentor a junior colleague this year | `AS-77795cdf2a7a` |
| communication style | quiet in meetings, detailed in writing | `AS-23174e388253` |
| routine | walks during long calls | `AS-6df25ebb7db5` |
| living context | lives with family | `AS-e71a15316330` |
| preferences | dark mode everywhere | `AS-697b444be0b8` |
| personality | dry sense of humour | `AS-d67a54ec50dd` |

### A-108 · Persona of `agent-fin-03`

| Category | Invented detail | Ledger ID |
|---|---|---|
| interests | pottery | `AS-7838359f5ccc` |
| hobbies | growing herbs on a balcony | `AS-658cfff486ae` |
| background | came from a larger company looking for more ownership | `AS-b6b2d6fb9b4d` |
| goals | wants a calmer release cadence | `AS-2dd8e9d66ca1` |
| communication style | informal; thinks out loud | `AS-317f94f8b4f3` |
| routine | reviews the week every Sunday evening | `AS-287075643685` |
| living context | lives with family | `AS-3f6d636bda83` |
| preferences | dark mode everywhere | `AS-e5559f2a4107` |
| personality | sceptical until shown evidence | `AS-65e59a32431d` |

### A-109 · Persona of `agent-ppl-01`

| Category | Invented detail | Ledger ID |
|---|---|---|
| interests | amateur astronomy | `AS-16ff6dcbf833` |
| hobbies | writing short fiction | `AS-9ff84aec151e` |
| background | came from a larger company looking for more ownership | `AS-199551f6b7ca` |
| goals | hopes to lead a project end to end | `AS-a790b0d956a9` |
| communication style | careful and precise; checks numbers twice | `AS-10471f0d7635` |
| routine | takes a proper lunch break away from the desk | `AS-849813c0c56a` |
| living context | shares a flat with a friend | `AS-30c0eb10d474` |
| preferences | short meetings with clear owners | `AS-9d26c16b15a2` |
| personality | sceptical until shown evidence | `AS-5f3c899e9552` |

### A-110 · Persona of `agent-ppl-02`

| Category | Invented detail | Ledger ID |
|---|---|---|
| interests | board games | `AS-fba85de6145a` |
| hobbies | cooking for friends | `AS-6f0c95527de3` |
| background | first job after a master's programme | `AS-b8e898f4bbe5` |
| goals | is working towards a professional certification | `AS-57de419fa6d5` |
| communication style | warm, asks questions before answering | `AS-92bdbc8994e9` |
| routine | takes a proper lunch break away from the desk | `AS-f2f9809b7d55` |
| living context | shares a flat with a friend | `AS-441bbdfd745b` |
| preferences | handwritten notes for planning | `AS-52ccdf443150` |
| personality | curious and a little impatient | `AS-374d21905abc` |

### A-111 · Persona of `agent-ppl-03`

| Category | Invented detail | Ledger ID |
|---|---|---|
| interests | community gardening | `AS-ea9f08d975c8` |
| hobbies | weekend treks | `AS-718464d64d0e` |
| background | studied at a (fictional) state university before joining a startup | `AS-e2cc01bbec47` |
| goals | wants a calmer release cadence | `AS-3e87e8b3867a` |
| communication style | structured; likes agendas and follow-up notes | `AS-7fdbb0434717` |
| routine | starts early and protects mornings for focused work | `AS-49f881c4bd77` |
| living context | commutes by metro | `AS-02cda016a87d` |
| preferences | plain-text email | `AS-c9ed2a5f8530` |
| personality | steady under pressure | `AS-4179d24bd286` |

### A-112 · Persona of `agent-mkt-01`

| Category | Invented detail | Ledger ID |
|---|---|---|
| interests | chess problems | `AS-395d3b0b5409` |
| hobbies | photographing street markets | `AS-ce025d6db1a5` |
| background | first job after a master's programme | `AS-0a8e5ed2e973` |
| goals | plans to take a long holiday after the next launch | `AS-fd37fea70448` |
| communication style | quiet in meetings, detailed in writing | `AS-102e3da36c8e` |
| routine | walks during long calls | `AS-d8a32b3ead92` |
| living context | recently moved closer to the office | `AS-602d18e0134c` |
| preferences | short meetings with clear owners | `AS-5ccbb4a596a1` |
| personality | energetic and optimistic | `AS-451e69f17fa9` |

### A-113 · Persona of `agent-mkt-02`

| Category | Invented detail | Ledger ID |
|---|---|---|
| interests | jazz records | `AS-9ac0950404d4` |
| hobbies | weekend treks | `AS-b1d2ded53e99` |
| background | first job after a master's programme | `AS-89c96cdf4508` |
| goals | is working towards a professional certification | `AS-d16f995004e2` |
| communication style | informal; thinks out loud | `AS-40cb7524022d` |
| routine | starts early and protects mornings for focused work | `AS-98fe98737a6f` |
| living context | commutes by metro | `AS-05a19130f511` |
| preferences | headphones on for focus | `AS-079798dfa647` |
| personality | thoughtful and conflict-averse | `AS-c7112671e826` |

### A-114 · Persona of `agent-mkt-03`

| Category | Invented detail | Ledger ID |
|---|---|---|
| interests | pottery | `AS-4abaf7e86d78` |
| hobbies | restoring an old bicycle | `AS-4889ed0c084b` |
| background | started in a services firm and moved to product | `AS-aea998543124` |
| goals | would like to understand the business side better | `AS-0ebabd0c9bba` |
| communication style | careful and precise; checks numbers twice | `AS-4aff9308dc80` |
| routine | starts early and protects mornings for focused work | `AS-8115c61b39ee` |
| living context | commutes by metro | `AS-6b53b2d1a6e8` |
| preferences | handwritten notes for planning | `AS-8d946030b62a` |
| personality | sceptical until shown evidence | `AS-1540e83308a2` |

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
| 16 Nov 2026 | A-024 to A-026 | Added with the Day 1 ingestion foundation: data class per source, file-drop extraction time, templates not evidence. Drafts; none approved | AI (Claude Code), for owner review |
| 16 Nov 2026 | A-025 r2 | Revised after owner review: extraction time is the actual read time of each file, distinct from release, admission and sealing times. Still a draft | AI (Claude Code), for owner review |
| 16 Nov 2026 | A-024, A-025 r2, A-026 | Approved by the owner: A-024 with strict fail-closed handling; A-025 in its corrected r2 form (actual extraction timestamps); A-026 as written | Owner decision, recorded by AI (Claude Code) |
| 19 Nov 2026 | A-101 to A-114 | Persona inventions for the 14 agent slots logged from the deterministic generator (9 categories each, 126 details); never evidence | AI (Claude Code), for owner review |
