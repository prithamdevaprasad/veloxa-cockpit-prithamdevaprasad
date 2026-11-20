# Point-in-time recovery and WAL archiving: design (C31). NOT IMPLEMENTED

**Status.**
- **What exists:** C31 provides verified logical backups (`veloxa backup`) and automated restore drills (`veloxa restore-drill`). They restore to the moment of the dump only.
- **What does not exist:** point-in-time recovery (PITR). Nothing below is configured.
- **What is still open:** RPO and RTO are owner decisions (K-17). The tool choice depends on hosting (OD-D).

## Why PITR is needed

A logical dump taken once a day loses up to a day of admissions, decisions, agent turns and audit events. PITR replays the write-ahead log (WAL) up to a chosen instant, so the loss is bounded by the WAL archive delay.

## Recommended design (proposal)

**Prefer managed PITR.** If the hosting platform offers managed PostgreSQL 18 with PITR, use that (OD-D). Self-manage only if it does not.

| Element | Proposal | Notes |
|---|---|---|
| WAL archiving | `wal_level = replica`, `archive_mode = on`, continuous archiving to encrypted object storage with a maintained tool (pgBackRest or WAL-G; choose with hosting) | `archive_timeout = 60s` bounds the loss when write volume is low |
| Base backups | Full weekly; differential daily | Each base backup is verified (checksum plus a restore drill) |
| RPO (proposed placeholder) | 15 minutes | Owner value, K-17 |
| RTO (proposed placeholder) | 4 hours | Owner value, K-17 |
| Encryption | At rest in the archive. Keys are held in the hosting platform's KMS, never alongside the backups | |
| Access | A dedicated backup role. The archive bucket is writable only by the archiver and readable only by the restore role | |
| Retention | Base backups and WAL kept for the backup retention period (OD-G, "Backups"). WAL is kept back to the oldest retained base backup | |
| DBOS system database | Archived the same way. Recovering both databases to the same instant keeps workflow state consistent with the application data | |

## Drills (extending the existing C31 drill)

The PITR drill runs monthly and after any change to the setup:
1. Restore the latest base backup into a **new** disposable instance.
2. Replay WAL to a target time *T*.
3. Compare the fingerprint (batches, ledger md5, audit chain validity) with a fingerprint recorded at *T*.
4. Record the measured restore time against the RTO.
5. Drop the instance.

## Interaction with erasure (D-06a)

Backups contain everything, including any data later erased. The approved direction is crypto-shredding: destroying a key makes the data unreadable everywhere, including in backups. The **keys must therefore not be inside the backups they protect**. Alternatively, backup retention must be shorter than the erasure deadline.

## What must not happen

- PITR restores into the live database. Recovery always goes to a new instance, which is promoted only after verification.
- Silent expiry of append-only evidence. Backup expiry is a separate, owner-approved policy.
