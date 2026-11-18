// Not part of the test suite (outside tests/): captures the redesigned routes against the synthetic Demo Lab.
// Run: npx playwright test screenshots.spec.ts --config screenshots.config.ts
import { expect, test } from "@playwright/test";

const TOKEN = "synthetic-demo-lab-fixture-token-0123456789";   // documented local Demo Lab fixture
const OUT = process.env.SHOT_DIR ?? "screenshots";

test("capture the main routes", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/#/admin");
  await page.getByLabel("Bearer token").fill(TOKEN);
  await page.getByRole("button", { name: "Use token" }).click();
  for (const r of ["overview", "agents", "departments", "evidence", "intelligence", "whatif", "timeline", "admin"]) {
    await page.goto(`/#/${r}`);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(600);
    await page.screenshot({ path: `${OUT}/${r}.png`, fullPage: true });
  }
  await page.goto("/#/overview");
  await page.waitForLoadState("networkidle");
  await page.getByRole("region", { name: "Recent Agent Updates" }).getByRole("button").first().click();
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}/agent-update.png` });
  await page.getByRole("button", { name: "Ask about this" }).click();
  const ask = page.getByRole("dialog", { name: "Ask Veloxa" });
  await ask.getByLabel("Question").fill("What happened to Quartz 2.0?");
  await ask.getByRole("button", { name: "Send" }).click();
  await expect(ask.getByRole("list", { name: "Claims" }).first()).toBeVisible({ timeout: 20_000 });
  await page.screenshot({ path: `${OUT}/ask-contextual.png` });
  await ask.getByRole("button", { name: "Show source 1" }).first().click();
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${OUT}/evidence-drawer.png` });
  await page.setViewportSize({ width: 393, height: 851 });
  await page.keyboard.press("Escape");
  for (const r of ["overview", "evidence"]) {
    await page.goto(`/#/${r}`);
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: `${OUT}/mobile-${r}.png`, fullPage: true });
  }
});
