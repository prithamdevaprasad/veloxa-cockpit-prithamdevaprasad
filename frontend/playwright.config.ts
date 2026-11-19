import { defineConfig, devices } from "@playwright/test";

// Browser tests run the REAL API on a disposable synthetic database (scripts/demo_env.py) behind `vite preview`.
// Needs VELOXA_TEST_ADMIN_DSN and a built frontend (npm run build).
const python = process.env.VELOXA_PYTHON ?? "python";

export default defineConfig({
  testDir: "tests",
  timeout: 45_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: "http://127.0.0.1:4173", trace: "retain-on-failure" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    { name: "desktop-wide", use: { ...devices["Desktop Chrome"], viewport: { width: 1920, height: 1080 } } },
    { name: "tablet", use: { ...devices["Desktop Chrome"], viewport: { width: 820, height: 1180 }, hasTouch: true } },
    { name: "mobile", use: { ...devices["Pixel 5"] } },
  ],
  webServer: [
    { command: `${python} ../scripts/demo_env.py --port 8765`, url: "http://127.0.0.1:8765/health",
      timeout: 120_000, reuseExistingServer: false, stdout: "pipe" },
    { command: "npx vite preview --host 127.0.0.1 --port 4173 --strictPort", url: "http://127.0.0.1:4173",
      env: { VELOXA_API_URL: "http://127.0.0.1:8765" }, timeout: 60_000, reuseExistingServer: false },
  ],
});
