# Veloxa

**An executive cockpit that answers a CEO's questions from the company's own records, with a source for every claim.**

**Live demo:** [veloxa-demo.veloxa-cockpit-frontend.workers.dev](https://veloxa-demo.veloxa-cockpit-frontend.workers.dev) (invite-only sign-in)

Built for the Hiyaku Systems Hackathon (16–20 Nov 2026) around **Veloxa Technologies**, a fictional company. All data in
the demo is synthetic.

---

## Product demo

[![Veloxa product demo: click to play the 30-second video](docs/demo/video/veloxa-demo-preview.gif)](docs/demo/video/veloxa-demo.mp4)

**[▶ Watch the 30-second demo (MP4)](docs/demo/video/veloxa-demo.mp4)**

---

## What Veloxa does

| | |
|---|---|
| **Executive cockpit** | One page with what needs attention, early warnings, key facts and what changed recently. |
| **CEO Chat** | Ask in plain English ("What is the biggest risk?", "Are we on track?"). Every answer cites the records it comes from, and follow-up questions keep the subject. When the records don't say, Veloxa says so instead of guessing. |
| **14 AI assistants** | One per team member, each grounded only in the records that person's role can see. They say clearly what they don't know. |
| **Risks and decision support** | A ranked list of concerns with the evidence behind each, a risk map, and *What if* scenarios ("what happens if this date moves?"). |
| **Integrations** | Read-only GitHub (pull requests, commits, CI runs) and Jira (issues, status, due dates). Imported data goes through review before anyone can use it. |
| **Evidence you can check** | Open the source of any claim: which file, which row or page, when it was added and who approved it. |
| **Privacy by default** | People only see records their role allows. Private messages are visible only to the people on them. |

## How it works

```mermaid
flowchart LR
    A["Company data<br/>uploads, GitHub, Jira"] --> B["Review and approval<br/>a person checks it first"]
    B --> C[("Knowledge base<br/>approved records only")]
    C --> D["Veloxa engine<br/>finds facts, ranks risks,<br/>checks every claim"]
    D --> E["Website<br/>cockpit, CEO Chat,<br/>AI assistants, What-if"]
```

1. **Data comes in** from uploaded files or the read-only GitHub and Jira connectors.
2. **A person reviews and approves it.** Nothing enters the knowledge base on its own.
3. **The knowledge base keeps every approved record**, with where it came from and when. Records are never edited or
   deleted, so every answer can be traced back.
4. **The engine answers questions** by finding the relevant facts, ranking risks and checking that every sentence in an
   answer is backed by a record. If the records don't support an answer, it says so.
5. **The website shows the results**: the cockpit, CEO Chat, the AI assistants and What-if scenarios. Each person only
   sees what their role allows.

## Architecture

| Layer | What it does | Built with |
|---|---|---|
| Website | The cockpit, chat, assistants and scenario screens | React 18, TypeScript, Vite; hosted on Cloudflare Workers |
| API | Sign-in, permissions, answering questions, connectors | Python 3.12, FastAPI; hosted on Google Cloud Run |
| Background worker | Validates and admits approved data, builds the search index | Python; Google Cloud Run |
| Database | Stores approved records in an append-only ledger; row-level security keeps each company and person to their own data | PostgreSQL 18 (Google Cloud SQL) |
| Connectors | Read-only imports from GitHub (App) and Jira (OAuth) | Python |

More detail is in [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Run it locally

You need Python 3.12, Node.js 24 and Docker.

```powershell
# 1. Install
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --constraint requirements.lock -e ".[dev]"
cd frontend; npm ci; cd ..

# 2. Start the database
docker compose up --detach --wait db

# 3. Start the API with a synthetic demo company (terminal 1)
$env:VELOXA_TEST_ADMIN_DSN = "postgresql://veloxa_admin:local-dev-admin@127.0.0.1:55432/postgres"
python scripts\demo_env.py --port 8765

# 4. Start the website (terminal 2)
cd frontend
$env:VELOXA_API_URL = "http://127.0.0.1:8765"
npm run dev
```

Open `http://127.0.0.1:5173` and sign in with the demo token printed in terminal 1.

On Linux or macOS, use `python3.12 -m venv .venv && . .venv/bin/activate` and the same commands.

The full setup guide (configuration, real data, troubleshooting) is in [`LOCAL_DEVELOPMENT.md`](LOCAL_DEVELOPMENT.md).

## Tests

```powershell
python -m pytest -q                 # Python tests
ruff check .                        # lint and security checks
cd frontend; npm run build          # type-check and production build
npx playwright test                 # browser journeys
```

## Security

- The demo uses synthetic data only. No real personal data is stored in this repository.
- Secrets live in `.env` (never committed) or in the cloud secret manager.
- Every query is limited by row-level security in the database, so one company or person can't read another's data.
- Connectors are read-only, and everything they import waits for human approval.

## Documentation

| Document | What's in it |
|---|---|
| [`LOCAL_DEVELOPMENT.md`](LOCAL_DEVELOPMENT.md) | Full setup, configuration and troubleshooting |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | How the system is designed |
| [`docs/HOSTING.md`](docs/HOSTING.md) | How the demo is hosted |
| [`docs/agents/`](docs/agents/) | How the AI assistants work |
| [`DECISIONS.md`](DECISIONS.md), [`ASSUMPTIONS.md`](ASSUMPTIONS.md) | Design decisions and assumptions |
| [`AI_LOG.md`](AI_LOG.md) | How AI tools were used to build this |
