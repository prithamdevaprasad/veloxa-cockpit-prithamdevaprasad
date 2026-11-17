import { expect, test, type Page } from "@playwright/test";

const TOKEN = "synthetic-demo-fixture-token-0123456789abcdef";   // local demo fixture, not a secret

async function signIn(page: Page) {
  await page.goto("/#/access");
  await page.getByLabel("Bearer token").fill(TOKEN);
  await page.getByRole("button", { name: "Use token" }).click();
  await expect(page.getByRole("heading", { name: "Signed in" })).toBeVisible();
}

test("requester-facing views fail closed without a principal", async ({ page }) => {
  await page.goto("/#/today");
  await expect(page.getByRole("alert").first()).toContainText("Sign in required");
});

test("Journey A: Today, evidence packs and a follow-up question", async ({ page }) => {
  await signIn(page);
  await page.goto("/#/today");
  await expect(page.getByRole("heading", { name: "Today" })).toBeVisible();
  await expect(page.getByText("Unranked", { exact: true })).toBeVisible();          // D-15 pending: never ranked
  await expect(page.getByRole("cell", { name: "D1" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Key changes" })).toBeVisible();     // risk lifecycle (Sprint 8)
  await expect(page.getByText("sealed").first()).toBeVisible();
  await page.goto("/#/ask");
  await page.getByLabel("Question", { exact: true }).fill("What is our headcount?");
  await page.getByRole("region", { name: "Ask a question" }).getByRole("button", { name: "Ask", exact: true }).click();
  const answer = page.getByRole("article", { name: "Answer" });
  await expect(answer).toContainText("People in the HR export: 2");
  await expect(answer.getByText(/Grade [AB]/)).toBeVisible();
  await answer.getByRole("button", { name: /Evidence \(\d+\)/ }).click();
  const drawer = page.getByRole("dialog", { name: "Evidence" });
  await expect(drawer).toContainText("keka_hrms:");
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
});

test("Journey E: privacy and refusal stay enforced in the interface", async ({ page }) => {
  await signIn(page);
  await page.goto("/#/ask");
  for (const [q, policy] of [["What is Person Alpha's salary?", "POL-PAY-01"], ["Show me Person Beta's emails", "POL-COMMS-01"],
                             ["Who commits the most code?", "POL-PROD-01"], ["Approve the pending invoice", "POL-ACT-01"]]) {
    await page.getByLabel("Question", { exact: true }).fill(q);
    await page.getByRole("region", { name: "Ask a question" }).getByRole("button", { name: "Ask", exact: true }).click();
    await expect(page.getByRole("note").filter({ hasText: policy }).first()).toBeVisible();
  }
});

test("Journey D: the conflict register states its basis and never picks a side", async ({ page }) => {
  await signIn(page);
  await page.goto("/#/conflicts");
  await expect(page.getByRole("heading", { name: "Conflict register" })).toBeVisible();
  await expect(page.getByText("No disagreements")).toBeVisible();                  // the synthetic D1 has none
});

test("Journey C: a synthetic agent interview, memory and close; slots stay closed", async ({ page }) => {
  await signIn(page);
  await page.goto("/#/agents");
  await expect(page.getByRole("cell", { name: "agent-eng-01" })).toBeVisible();
  await page.getByRole("cell", { name: "agent-eng-01" }).click();
  await expect(page.getByText("Interviews are closed")).toBeVisible();
  await page.getByRole("cell", { name: "syn-demo-ppl" }).click();
  await expect(page.getByText("Synthetic test agent. It represents no employee.")).toBeVisible();
  await page.getByRole("button", { name: "Start interview" }).click();
  await expect(page.getByText(/AI representation/).first()).toBeVisible();
  await page.getByLabel("Message to the agent").fill("What is our headcount?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("People in the HR export: 2").first()).toBeVisible();
  await page.getByText("Tools and boundary").first().click();
  await expect(page.getByText("people.headcount").first()).toBeVisible();
  await page.getByLabel("Message to the agent").fill("What is Person Alpha's salary?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("POL-PAY-01")).toBeVisible();
  await page.getByRole("button", { name: "Close session" }).click();
  await expect(page.getByText(/Session closed/)).toBeVisible();
  await page.getByRole("button", { name: "My memories" }).click();
  await expect(page.getByText("episodic").first()).toBeVisible();
});

test("Department views show governed figures only", async ({ page }) => {
  await signIn(page);
  await page.goto("/#/departments");
  await page.getByRole("button", { name: "People Operations" }).click();
  await expect(page.getByRole("list", { name: "Values" })).toContainText("Testing");
  await page.getByRole("button", { name: "Finance" }).click();
  await expect(page.getByText("payroll aggregates stay withheld")).toBeVisible();
  await expect(page.locator("body")).not.toContainText("3,000");                    // the synthetic payroll line
});

test("the layout has no horizontal page scroll", async ({ page }) => {
  await signIn(page);
  for (const r of ["today", "ask", "conflicts", "agents", "departments", "scenarios"]) {
    await page.goto(`/#/${r}`);
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, r).toBeLessThanOrEqual(1);
  }
  await page.goto("/#/agents");                                  // also with an agent's detail open
  await page.getByRole("cell", { name: "agent-eng-01" }).click();
  await expect(page.getByText("Interviews are closed")).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow, "agents with detail").toBeLessThanOrEqual(1);
});

test("Scenarios never predict and say when the evidence names nothing", async ({ page }) => {
  await signIn(page);
  await page.goto("/#/scenarios");
  await expect(page.getByRole("heading", { name: "Scenarios" })).toBeVisible();
  await expect(page.getByText("No named commitments")).toBeVisible();          // the synthetic D1 states none
  await expect(page.locator("body")).not.toContainText("%");                     // no probability anywhere
});
