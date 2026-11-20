# Load and concurrency (local measurement)

**These are local measurements. They are not a capacity statement or an SLA.**
- **Setup:** one Windows laptop; one API process (uvicorn); PostgreSQL 18.7 in Docker; the synthetic demo tenant (`scripts/demo_env.py`).
- **No model:** answers are composed deterministically.

**Measured:** 17 Nov 2026, with `scripts/load_test.py`.

## How to run

```powershell
$env:VELOXA_TEST_ADMIN_DSN = "postgresql://veloxa_admin:<password>@127.0.0.1:55432/postgres"
python scripts/demo_env.py --port 8765          # terminal 1: synthetic demo API
python scripts/load_test.py --base http://127.0.0.1:8765 --clients 8 --requests 40
python scripts/load_test.py --base http://127.0.0.1:8765 --burst 30     # rate-limit behaviour
python scripts/demo_env.py --cleanup            # drop any leftover demo database
```

**Behaviour:**
- The probe only targets loopback addresses.
- It exits 1 on any 5xx response.
- The demo raises the gateway limits (6,000/min; expensive 600/min) so one fixture token can drive the browser suite. To measure under the code defaults, start the demo with `VELOXA_RATE_LIMIT_PER_MIN=120` and `VELOXA_RATE_LIMIT_EXPENSIVE_PER_MIN=10`.

## Results

| Run | Phase | Requests | Status | p50 ms | p95 ms | max ms | req/s |
|---|---|---|---|---|---|---|---|
| Demo limits, 8 clients | read (`/health`, `/v1/today`, `/v1/conflicts`, `/v1/timeline`, `/v1/dependencies`) | 320 | 320 × 200 | 63.4 | 109.9 | 140.1 | 112 |
| Demo limits, 8 clients | ask (`POST /v1/ask`) | 80 | 80 × 200 | 64.7 | 96.5 | 112.0 | 113 |
| Default limits, 4 clients | read | 20 | 20 × 200 | 52.3 | 112.6 | 112.6 | 69 |
| Default limits, 4 clients | ask | 4 | 4 × 200 | 57.5 | 61.1 | 61.1 | 63 |
| Default limits, 1 client | burst of 30 asks | 30 | **6 × 200, 24 × 429** | 10.1 | 70.8 | 74.8 | 386 |

**Concurrency findings:**
- **The expensive bucket holds under concurrency.** The 4 earlier asks plus 6 burst asks equal exactly the 10/min expensive limit.
  - All 24 refusals carried `Retry-After`.
  - There were **0 server errors**.
- **Unit-level concurrency.** `tests/test_gateway.py` sends 5 parallel requests against a limit of 3. The result is exactly `[200, 200, 200, 429, 429]`: the token bucket is lock-protected.
- **Idempotency under retries.** Agent messages and workflow starts are idempotent per key. This is tested in `test_agent_api.py` and `test_workflows_dbos.py`.

## Not measured

- Multi-process or multi-host serving.
- Connection-pool sizing.
- A real model provider's latency.
- Large tenants.
- Soak tests over hours.

These need the hosting decision (owner decisions OD-D, OD-J).
