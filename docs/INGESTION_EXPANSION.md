# Ingestion expansion: milestone 1 audit and connector architecture

Status: milestone 1 of 9 (audit and design). Nothing in this document replaces the evidence ledger, the data room or
the admission workflow. Every connector added later feeds the **same** path:

```
connector fetch -> data-room submission -> validate -> review -> authorise -> admit (sealed batch) -> project -> C12
```

## 1. What exists today (audited 19 Nov 2026)

| Area | Where | What it does today | Reuse as-is? |
|---|---|---|---|
| Upload workflow | `intake.py`, migration 0018 | Upload, validate (C06 limits, contracts), department review, separate authoriser, admit. Separation of duties is enforced by the database. | Yes: every connector submits here. |
| Submission states | `intake.submission` | `uploaded`, `validating`, `validation_failed`, `awaiting_review`, `rejected`, `awaiting_authorisation`, `declined`, `authorised`, `admitting`, `admission_failed`, `admitted`. | Yes. Add `quarantined` (section 4). |
| File de-duplication | `intake.py` validation | SHA-256 per file. Reports identical originals in other submissions and in already-admitted evidence. | Yes. |
| Evidence ledger | `ledger.py`, `ingest/` | Sealed, versioned batches. Records keep `record_id`, `version`, `batch_seq` and `source_path`; a correction is a new version. | Yes. This is the only evidence store. |
| Object storage | `storage.py` | Content-addressed originals (by SHA-256), tenant-scoped. | Yes. |
| Parsing | `ingest/`, `knowledge.py`, `chunking.py` | XLSX/CSV contracts, DOCX/PDF text, JSONL exports. Claims per system come from `ROW_SPECS`. | Extend: VTT/SRT/TXT transcripts (milestone 4). |
| Connector framework | `connectors.py` | `Connector` protocol, Jira connector, initial and incremental sync, `runtime.connector_run` (cursor before/after, counts, status, error), bounded `Retry-After` back-off, HMAC webhook verification, and the rule that a webhook is a signal and never evidence. | Extend (section 2). |
| Jira OAuth | `jira_oauth.py`, `api_jira.py` | OAuth 3LO, tokens encrypted at rest, scheduled pull, previewed and confirmed writes. | Yes. |
| Identity resolution | `resolution/` (C12), migrations 0011, 0012, 0022 and 0023 | Source identities, HR-anchored person entities, rule candidates with no score, versioned human decisions, audit. Jira assignees can now be linked by a verified e-mail or an administrator mapping. | Yes: every new source registers identities here. |
| Tenancy and authorisation | `tenancy.py`, `authz/`, FORCE RLS | Tenant transactions, ReBAC grants, no self-escalation. | Yes. |

### Gaps against the brief

1. **No per-tenant connection registry.** Jira is configured per server (environment variables plus an in-memory or vault
   token). There is no stored connection identity, scope, health or reauthorisation state per tenant.
2. **The idempotency key is source-specific.** Jira uses issue key plus `updated`. The brief asks for
   `tenant_id + connection_id + source_type + external_object_id`, with a source version or event ID.
3. **No dead-letter store.** A failed run is recorded, but individual failed objects are not queued for retry.
4. **No `quarantined` state.** Data that fails a policy check is rejected, not held.
5. **No auto-admission policy.** Every submission needs two humans. The brief allows low-risk verified events to be
   admitted automatically under an explicit source-specific policy.
6. **Transcripts are parsed as plain documents.** Speakers, timestamps and participants are not preserved as structure.
7. **No GitHub, Gmail, accounting or HRIS connectors.**
8. **The Data Room UI is upload-centric.** There is no connected-sources hub showing health, last sync, errors or
   review counts.

## 2. Connector framework: extensions, not a second engine

**New tables** (one migration, all tenant-scoped under RLS):

- `runtime.source_connection`:
  - fields: `connection_id`, `tenant_id`, `provider` (jira, github, gmail, …), `display_name`, `scope` (JSON:
    repositories, labels, projects…), `access` (`read` or `read_write`, separated), `credential_ref` (vault key; never
    the secret), `status` (connected, expired, revoked, error, disconnected), `last_success_at`, `last_error`,
    `created_by`, `created_at`;
  - disconnecting changes the status only and never removes evidence.
- `runtime.source_object`:
  - the idempotency registry: `tenant_id`, `connection_id`, `source_type`, `external_object_id`, `source_version`,
    `content_sha256`, `first_seen_at`, `last_seen_at`, `deleted_at`, `last_record_id`;
  - unique on (`tenant_id`, `connection_id`, `source_type`, `external_object_id`, `source_version`);
  - a replayed webhook or a re-delivered page is a no-op, and a new version becomes a new evidence version;
  - out-of-order events are resolved by comparing `source_version` (or the event timestamp), never by arrival order.
- `runtime.connector_dead_letter`: `connection_id`, `external_object_id`, `attempts`, `last_error`, `next_attempt_at`.
- `runtime.webhook_delivery`: `delivery_id` (provider's) unique, `received_at`, `verified`, `processed_at`. Replays are
  rejected by the unique key.

**Connector contract** (extending the existing `Connector` protocol):

```python
class SourceConnector(Protocol):
    provider: str
    def fetch(self, cursor: str | None) -> tuple[list[SourceObject], str | None]   # incremental, paginated
    def fetch_one(self, external_id: str) -> SourceObject | None                  # webhook follow-up / reconcile
    def verify_webhook(self, body: bytes, headers: dict) -> WebhookSignal | None  # signal only, never evidence
    def health(self) -> ConnectionHealth                                          # token valid? scope still granted?
```

`SourceObject` carries `external_object_id`, `source_version`, `source_url`, `created_at`, `updated_at`, `actor_ids`
(raw source identities for C12) and the allowlisted fields.

**Sync loop:** this generalises the existing `connectors.sync` and does not replace it.

1. Fetch since the cursor.
2. Drop objects whose (`id`, `version`) is already in `source_object`.
3. Write the changed ones as one JSONL export and submit it to the data room.
4. Record the run.
5. Webhooks enqueue `fetch_one`.
6. A scheduled reconciliation (a full listing compared against `source_object`) catches missed events and deletions.
   Deletions are recorded as `deleted_at` plus a tombstone record version; the earlier evidence is kept.

## 3. Trust and admission

| State | Meaning |
|---|---|
| received | stored original (`uploaded`) |
| parsed / validated | `validating`, then `awaiting_review` or `validation_failed` |
| awaiting review / authorisation | existing human steps |
| admitted / rejected | existing |
| **quarantined** (new) | held by policy: an ambiguous identity, restricted HR fields, a financial anomaly or sensitive correspondence. It needs an explicit release. |

**Automatic admission** applies only where a recorded, source-specific policy allows it, for example "GitHub commit and
pull-request metadata from approved repositories". Such a policy:

- is an owner decision record, versioned like the D-15 policy;
- never covers extracted inferences, meeting decisions, e-mail commitments, HR or finance.

Those always need review. A source's evidence can be admitted while the claims extracted from it stay unconfirmed.

## 4. Provider notes (to be confirmed against current provider documentation at each milestone)

| Provider | Approach | Owner action needed before build |
|---|---|---|
| GitHub | GitHub App, read-only permissions (contents metadata, pull requests, issues, checks, actions, deployments), installed on selected repositories; webhook HMAC (`X-Hub-Signature-256`) and periodic reconciliation. Jira keys are linked only from explicit references in titles, branches or commits. | Create a GitHub App on the owner's account or organisation (free) and choose the repositories. |
| Meeting transcripts | Upload VTT, SRT, TXT, DOCX or PDF through the data room. The parser keeps speakers, timestamps and sections. Decisions, commitments and actions are extracted as separate, unconfirmed claims. Google Meet and Microsoft Teams transcript APIs are to be investigated; nothing is recorded automatically. | None for uploads. Workspace admin consent for the Meet or Teams APIs. |
| Gmail | OAuth with the narrowest read scope. The administrator selects mailboxes, labels, senders and a date range. Incremental sync by history ID. Never send, modify or delete. E-mail statements are evidence of communication, not of truth. | A Google Cloud OAuth client. Restricted Gmail scopes may need Google's verification for external use; internal Workspace use is simpler. |
| Finance | CSV/XLSX first, through the existing contracts, with the reporting period, currency and accounting basis required. Then a provider-neutral `AccountingConnector`, starting with one provider (Zoho Books, Tally, QuickBooks Online or Xero, to be chosen). | Choose the first provider and supply a test company. |
| HR | Existing HR imports, then a provider-neutral `HrisConnector` (BambooHR first, to be investigated). Allowlist only: employee ID, department, title, manager, status, join date and recruitment status. Salary, leave, medical and identity documents are never ingested. | Choose the HRIS and supply a sandbox. |

## 5. Milestones

1. Audit and architecture (this document): done.
2. Connector framework and reliability: the tables above, idempotency, dead letters, webhook replay protection,
   reconciliation and tests (duplicate delivery, out-of-order events, deletion, failed authentication, tenant
   isolation).
3. GitHub App (read-only).
4. Meeting transcript imports.
5. Gmail, read-only.
6. Finance, provider-neutral import.
7. HR, provider-neutral import.
8. Data Room as a data-sources hub (UI) and integration testing.
9. Security and privacy verification.

Development uses synthetic fixtures and test accounts only. No real mailboxes, financial accounts or production
repositories are used without specific authorisation, and nothing is merged, deployed or paid for without approval.
