# Hosting Veloxa on Google Cloud Run (asia-south1)

Status: **prepared, not deployed.** No cloud resource has been created, no API enabled and billing is not linked.
Everything below needs the owner's approval of the paid resources first.

## 1. Architecture

| Part | Where | Notes |
|---|---|---|
| Frontend | Cloudflare Pages | Static build of `frontend/` (`npm run build`, output `dist`). Free plan. |
| API | Cloud Run service `veloxa-api` | `deploy/cloudrun/api.service.yaml`. Request-based, scales 0–4 in staging. |
| Background worker | Cloud Run service `veloxa-worker` | `deploy/cloudrun/worker.service.yaml`. Always on, exactly one instance, no public ingress. |
| Database | Cloud SQL for PostgreSQL, asia-south1 | Two databases on one instance: `veloxa` (application) and `veloxa_workflows` (DBOS journal). |
| Evidence originals and connector tokens | Cloud Storage bucket, mounted into both services | Content-addressed and hash-checked on every read; tokens are AES-GCM encrypted before they are written. |
| Secrets | Secret Manager | Referenced by name from the service definitions; never in the image or the repository. |
| Sign-in | Supabase Auth (existing project) | Veloxa verifies the Supabase token (ES256, issuer, audience, expiry) and maps it to an invited person in its own database. |

One container image serves both services (`deploy/Dockerfile`): built in two stages, no tests or developer tools,
runs as a non-root user. Verified locally: the image builds (479 MB) and runs as `veloxa`.

## 2. Background work and DBOS: why a separate worker

Cloud Run's request-based instances get almost no CPU between requests and can scale to zero, so any work started
"in the background" inside the API could stall or be lost. Veloxa therefore splits the work:

- **API (`veloxa serve`)**: only answers requests. Scheduled Jira sync (`VELOXA_JIRA_SYNC`) is never set here.
- **Worker (`veloxa intake-work`)**: always on (`cpu-throttling: false`, `minScale = maxScale = 1`). It validates
  uploads, admits authorised submissions, and runs the durable DBOS workflows.

**Recovery guarantees.** DBOS records each workflow's progress in its own database (`VELOXA_WORKFLOW_DATABASE_URL`,
kept apart from the application tables). If the worker restarts or is replaced, the new instance picks up every
unfinished workflow and resumes it from its last completed step. Steps are idempotent or guarded, so a step that is
repeated after a crash does not duplicate evidence (the data room and the source-object registry also refuse
duplicates). Exactly one worker instance is used because the executor id is fixed in code
(`src/veloxa/workflows/runner.py`); running more than one needs that id made configurable first.

DBOS Conductor (a paid DBOS service) is not needed.

**One API instance for now.** Jira and Gmail sign-in keep their one-time OAuth state in the API's memory, so a
callback must reach the instance that started it. The hosted staging API runs with `--max-instances=1`. Before
scaling out, move that state into the database.

**Pause and resume.** `bash deploy/veloxa-hosting.sh pause|resume|status <project>` stops and restarts the worker,
the API and Cloud SQL without deleting data (paused cost: the database disk and backups, about US$0.17 a day).

## 3. Cloud SQL

| Setting | Staging | Production |
|---|---|---|
| Edition / version | Enterprise, **PostgreSQL 18** (required: Veloxa uses `uuidv7()`) | same |
| Machine | shared-core `db-g1-small` | `db-custom-2-8192` or larger |
| High availability | off | on (regional) |
| Storage | 20 GB SSD, automatic increase | 100 GB+ SSD |
| Backups | daily, 7 days, point-in-time recovery on | daily, 30 days, PITR on |
| Connections | Cloud SQL connector socket (`/cloudsql/...`), private only | same |
| Pool | `VELOXA_DB_POOL_MAX=8` per API instance (4 instances = 32, under the default limit) | tune with load test |

**Check before approval:** confirm PostgreSQL 18 is offered in asia-south1
(`gcloud sql tiers list` / the console version list once the SQL Admin API is enabled). If it is not, Veloxa
cannot run on Cloud SQL until it is; do not downgrade.

**Migrations.** Run `veloxa migrate` from a one-off Cloud Run job using the admin role, before the new revision
receives traffic. Migrations are forward-only and append-only tables refuse updates and deletes; take an on-demand
backup immediately before each migration.

**Backup and restore.** Automated daily backups and point-in-time recovery. Restore drill: restore into a new
instance, point a staging revision at it, run `scripts/run_acceptance.py`, then switch.

## 4. Secrets (Secret Manager)

| Secret | Used by | Contents |
|---|---|---|
| `veloxa-db-app-password` | API | password of the `veloxa_app` role |
| `veloxa-db-ingest-password`, `veloxa-db-projection-password` | worker | worker role passwords |
| `veloxa-db-admin-password` | migration job only | admin role password |
| `veloxa-workflow-db-url` | API, worker | DSN of the DBOS database |
| `veloxa-token-key` | API, worker | 32-byte key that encrypts connector tokens at rest |
| `veloxa-atlassian-client-id`, `veloxa-atlassian-client-secret` | API | Jira OAuth app |
| `veloxa-gmail-client-json` | API | Google OAuth client for Gmail (mounted as a file; `VELOXA_GMAIL_CLIENT_FILE`) |
| `veloxa-github-app-key` | API | GitHub App private key (mounted as a file; `VELOXA_GITHUB_APP_PRIVATE_KEY_PATH`) |

Each service has its own service account with `secretAccessor` on only the secrets it uses. The Supabase
publishable key is public by design and lives in the frontend build settings; no Supabase secret key is used.

## 5. Security

- Never set in hosted environments: `VELOXA_DEV_AUTH`, `VELOXA_DEMO_LOGIN`, `VELOXA_TEST_AGENTS_FILE`.
- `VELOXA_CORS_ORIGINS` lists the exact https origin of the Pages site; wildcards are refused at start-up.
- OAuth callbacks must be registered for the hosted API URL:
  `https://<api-host>/v1/integrations/jira/callback`, `https://<api-host>/v1/integrations/gmail/callback`;
  Supabase redirect allow-list: the Pages origin only.
- The worker has no public ingress. The API is public but every route needs a verified sign-in.
- Rate limits: `VELOXA_RATE_LIMIT_PER_MIN=120`, `VELOXA_RATE_LIMIT_EXPENSIVE_PER_MIN=20` per caller.
- Logs carry request IDs and route names only (no message content, tokens or evidence).

## 6. Google APIs to enable (once billing is linked)

`run.googleapis.com`, `sqladmin.googleapis.com`, `secretmanager.googleapis.com`, `artifactregistry.googleapis.com`,
`cloudbuild.googleapis.com` (optional, for remote builds), `storage.googleapis.com`, `iam.googleapis.com`.
Already enabled in the project: logging, monitoring, Cloud Storage API, service usage.

## 7. Monitoring and alerts

Cloud Monitoring alerts (free): API 5xx rate > 2% for 5 minutes; API p95 latency > 5 s; worker restarts;
Cloud SQL CPU > 80% and storage > 80%; any failed `veloxa migrate` job. Uptime check on `/health`.

## 8. Deployment runbook (staging)

1. Link billing (owner). Enable the APIs in section 6.
2. Create the Artifact Registry repository, Cloud SQL instance (section 3), the evidence bucket (uniform access,
   no public access) and the two service accounts.
3. Create the secrets (section 4). Generate `veloxa-token-key` with `python -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"`.
4. Build and push: `docker build -f deploy/Dockerfile -t asia-south1-docker.pkg.dev/<project>/veloxa/veloxa:<sha> .`
5. Run the migration job, then `gcloud run services replace` for the worker and the API (fill the placeholders).
6. Register the OAuth callbacks and the Supabase redirect (section 5). Deploy the frontend to Pages with
   `VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY` and the API URL.
7. Smoke test: sign-in, Ask, worry page, Data Room upload, Jira status, GitHub status, Gmail status.

**Rollback:** `gcloud run services update-traffic veloxa-api --to-revisions <previous>=100`. Database changes are
forward-only, so every migration must stay compatible with the previous revision for one release.

## 9. Monthly cost estimate (asia-south1)

Estimates from list prices, **±25%**; confirm in the Google Cloud pricing calculator before approving. INR at
INR 85 per USD. Excludes AI model usage (billed by the model provider) and Supabase (free tier covers these numbers).

| Monthly active users | API | Worker | Cloud SQL | Storage, secrets, registry, logs | **Total USD** | **Total INR** |
|---|---|---|---|---|---|---|
| 100 (staging / pilot) | ~$3 (scale to zero) | ~$53 | ~$36 (`db-g1-small`, 20 GB) | ~$5 | **~$97** | **~INR 8,200** |
| 1,000 | ~$25 (1 warm instance) | ~$53 | ~$68 (`db-custom-1-3840`, 50 GB) | ~$8 | **~$154** | **~INR 13,100** |
| 5,000 | ~$80 | ~$53 | ~$265 (`db-custom-2-8192`, HA, 100 GB) | ~$33 | **~$431** | **~INR 36,600** |
| 10,000 | ~$160 | ~$53 | ~$530 (`db-custom-4-16384`, HA, 200 GB) | ~$60 | **~$803** | **~INR 68,300** |

The always-on worker is the largest fixed cost in staging. A cheaper staging alternative is a Cloud Run **job**
started every 5 minutes by Cloud Scheduler (DBOS resumes unfinished work at each start): about $5/month, with up
to 5 minutes' delay before an upload is processed.
