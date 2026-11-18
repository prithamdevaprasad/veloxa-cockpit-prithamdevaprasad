// Not part of the test suite (outside tests/): captures the redesigned routes against the synthetic Demo Lab.
// Run: SHOT_DIR=<dir> npx playwright test screenshots.spec.ts --config screenshots.config.ts
import { expect, test, type Page } from "@playwright/test";

const TOKEN = "synthetic-demo-lab-fixture-token-0123456789";   // documented local Demo Lab fixture
const OUT = process.env.SHOT_DIR ?? "screenshots";

async function signIn(page: Page) {
  await page.goto("/#/admin");
  await page.getByLabel("Bearer token").fill(TOKEN);
  await page.getByRole("button", { name: "Use token" }).click();
}
async function shot(page: Page, route: string, file: string, full = true) {
  await page.goto(`/#/${route}`);
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(900);
  await page.screenshot({ path: `${OUT}/${file}.png`, fullPage: full });
}

test("capture the redesigned routes", async ({ page }) => {
  test.setTimeout(180_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page);
  for (const r of ["overview", "agents", "departments", "evidence", "intelligence", "whatif", "timeline", "admin"]) {
    await shot(page, r, r === "overview" ? "01-overview-desktop" : r);
  }
  await page.setViewportSize({ width: 1280, height: 800 });
  await shot(page, "overview", "01b-overview-1280");

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/#/overview");
  await page.waitForLoadState("networkidle");
  await page.getByRole("button", { name: "Ask Veloxa (Ctrl+K)" }).click();
  const ask = page.getByRole("dialog", { name: "Ask Veloxa" });
  await ask.getByRole("button", { name: "New conversation" }).click();
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}/04-ask-overlay.png` });
  await ask.getByLabel("Question").fill("What is our headcount by department?");
  await ask.getByRole("button", { name: "Send" }).click();
  await expect(ask.getByRole("article", { name: "Answer" }).first()).toBeVisible({ timeout: 20_000 });
  await page.waitForTimeout(700);
  await page.screenshot({ path: `${OUT}/06-ask-answer.png` });
  await ask.getByRole("button", { name: "Full screen" }).click();
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${OUT}/05-ask-fullscreen.png` });
  await ask.getByLabel("Question").fill("What happened to Quartz 2.0?");
  await ask.getByRole("button", { name: "Send" }).click();
  await expect(ask.getByRole("article", { name: "Answer" }).nth(1)).toBeVisible({ timeout: 20_000 });
  await page.waitForTimeout(700);
  await page.screenshot({ path: `${OUT}/06b-ask-passages.png` });
  await ask.getByRole("button", { name: "Show source 1" }).last().click();
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${OUT}/07-evidence-drawer.png` });
  await page.keyboard.press("Escape");
  await ask.getByRole("button", { name: "Close Ask" }).click();

  await page.setViewportSize({ width: 820, height: 1180 });
  await shot(page, "overview", "02-overview-tablet");
  await page.setViewportSize({ width: 393, height: 851 });
  await shot(page, "overview", "03-overview-mobile");
  await page.goto("/#/overview");
  await page.getByRole("button", { name: "Ask Veloxa (Ctrl+K)" }).click();
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${OUT}/03b-ask-mobile.png` });
});
