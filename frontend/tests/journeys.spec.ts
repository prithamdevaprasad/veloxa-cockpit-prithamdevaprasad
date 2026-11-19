import { expect, test, type Page } from "@playwright/test";

const TOKEN = "synthetic-demo-fixture-token-0123456789abcdef";   // local demo fixture, not a secret

async function signIn(page: Page) {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in to your workspace" })).toBeVisible();
  await page.getByText("Developer access (local only)").click();
  await page.getByLabel("Bearer token").fill(TOKEN);
  await page.getByRole("button", { name: "Use token" }).click();
  await expect(page.getByRole("button", { name: "Ask Veloxa (Ctrl+K)" })).toBeVisible();      // inside the workspace
}

test("requester-facing views fail closed without a principal", async ({ page }) => {
  await page.goto("/#/today");
  await expect(page.getByRole("heading", { name: "Sign in to your workspace" })).toBeVisible();
  await expect(page.getByRole("link", { name: /Continue with/ })).toHaveCount(0);    // no SSO configured: no buttons
  await expect(page.getByRole("button", { name: "Enter Demo Workspace" })).toHaveCount(0); // not a Demo Lab tenant
  await expect(page.locator("body")).not.toContainText("synthetic-demo-fixture-token");
});

test("Journey A: Today, evidence packs and a follow-up question", async ({ page }) => {
  await signIn(page);
  await page.goto("/#/today");
  await expect(page.getByRole("heading", { name: "Today" })).toBeVisible();
  await expect(page.getByText("Unranked", { exact: true })).toBeVisible();          // D-15 pending: never ranked
  await expect(page.getByRole("cell", { name: "D1" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Key changes" })).toBeVisible();     // risk lifecycle (Sprint 8)
  await expect(page.getByText("sealed").first()).toBeVisible();
  await page.goto("/#/ask");                                                  // the full-screen Ask workspace
  const ws = page.getByRole("dialog", { name: "Ask Veloxa" });
  await ws.getByLabel("Question", { exact: true }).fill("What is our headcount?");
  await ws.getByRole("button", { name: "Send" }).click();
  const answer = ws.getByRole("article", { name: "Answer" });
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
  const ws = page.getByRole("dialog", { name: "Ask Veloxa" });
  for (const [q, policy] of [["What is Person Alpha's salary?", "POL-PAY-01"], ["Show me Person Beta's emails", "POL-COMMS-01"],
                             ["Who commits the most code?", "POL-PROD-01"], ["Approve the pending invoice", "POL-ACT-01"]]) {
    await ws.getByLabel("Question", { exact: true }).fill(q);
    await ws.getByRole("button", { name: "Send" }).click();
    await expect(page.getByRole("note").filter({ hasText: policy }).first()).toBeVisible({ timeout: 15_000 });
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
  await page.goto("/#/admin");
  await expect(page.getByRole("cell", { name: "agent-eng-01" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Directory pages" })).toContainText("Page 1 of 2");
  await page.getByRole("cell", { name: "agent-eng-01" }).click();
  await expect(page.getByText("Interviews are closed")).toBeVisible();
  await page.getByRole("button", { name: "People Ops" }).click();               // 15 agents: page through by category
  await page.getByRole("cell", { name: "syn-demo-ppl" }).click();
  await expect(page.getByText("Synthetic test agent. It represents no employee.")).toBeVisible();
  await page.getByRole("button", { name: "Start interview" }).click();
  await expect(page.getByText(/AI assistant of an employee role/).first()).toBeVisible();
  await page.getByLabel("Message to the agent").fill("What is our headcount?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("People in the HR export: 2").first()).toBeVisible();
  await page.getByText(/Where this comes from/).first().click();                 // sources one click away
  await expect(page.getByText(/people\.headcount/).first()).toBeVisible();
  await page.getByLabel("Message to the agent").fill("What is Person Alpha's salary?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText(/Individual pay is confidential/)).toBeVisible();   // a plain refusal
  await page.getByText("Details").last().click();
  await expect(page.getByText(/POL-PAY-01/)).toBeVisible();
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
  for (const r of ["overview", "worry", "today", "ask", "conflicts", "agents", "departments", "evidence", "intelligence",
                   "whatif", "timeline", "admin"]) {
    await page.goto(`/#/${r}`);
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, r).toBeLessThanOrEqual(1);
  }
  await page.goto("/#/admin");                                  // also with an agent's detail open
  await page.getByRole("cell", { name: "agent-eng-01" }).click();
  await expect(page.getByText("Interviews are closed")).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow, "agents with detail").toBeLessThanOrEqual(1);
});

test("Scenarios never predict and say when the evidence names nothing", async ({ page }) => {
  await signIn(page);
  await page.goto("/#/scenarios");                                               // the old route still works
  await expect(page.getByRole("heading", { name: "What-if Analysis" })).toBeVisible();
  await expect(page.getByText("Nothing to analyse")).toBeVisible();            // the synthetic D1 states none
  await page.goto("/#/intelligence");
  await page.getByRole("tab", { name: "Dependencies & warnings" }).click();
  const deps = page.getByRole("region", { name: "Dependencies" });
  await expect(deps.getByRole("heading", { name: "Dependencies and early warnings" })).toBeVisible();
  await expect(deps.getByText("No dependencies")).toBeVisible();                 // nothing invented
  await expect(page.locator("body")).not.toContainText("%");                     // no probability anywhere
});

test("Timeline shows record events with their sources", async ({ page }) => {
  await signIn(page);
  await page.goto("/#/timeline");
  const events = page.getByRole("list", { name: "Timeline events" });
  await expect(events).toContainText("joined");
  await events.getByRole("button", { name: "Show source 1" }).first().click();
  const drawer = page.getByRole("dialog", { name: "Employee_Master.xlsx" });
  await expect(drawer).toContainText("keka hrms");
  await expect(drawer).toContainText("held by the operator");                   // no dead link for operator packs
});

test("Agent session history survives a page reload", async ({ page }) => {
  await signIn(page);
  await page.goto("/#/admin");
  await page.getByRole("button", { name: "People Ops" }).click();               // 15 agents: page through by category
  await page.getByRole("cell", { name: "syn-demo-ppl" }).click();
  await page.getByRole("button", { name: "Start interview" }).click();
  await page.getByLabel("Message to the agent").fill("What is our headcount?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("People in the HR export: 2").first()).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "People Ops" }).click();               // 15 agents: page through by category
  await page.getByRole("cell", { name: "syn-demo-ppl" }).click();
  await page.getByRole("button", { name: "History" }).click();
  await page.getByRole("list", { name: "Your sessions" }).getByRole("button", { name: "Open" }).first().click();
  await expect(page.locator(".bubble.me").first()).toHaveText("What is our headcount?");
  await expect(page.getByText("People in the HR export: 2").first()).toBeVisible();
});


test("Overview shows governed figures, recent agent updates from a real session, and asks about one", async ({ page }) => {
  await signIn(page);
  await page.goto("/#/overview");
  await expect(page.getByRole("heading", { name: /Welcome/ })).toBeVisible();
  await expect(page.getByText("Headcount", { exact: true })).toBeVisible();
  await expect(page.getByText("governed metric (HR export)")).toBeVisible();
  const updates = page.getByRole("region", { name: "Recent intelligence" });
  await expect(updates).toContainText("demo-ppl");
  await expect(updates.getByText("DEMO").first()).toBeVisible();                // synthetic sessions are labelled
  await updates.getByRole("button", { name: /demo-ppl/ }).first().click();
  await page.getByRole("button", { name: "Ask about this" }).click();
  const ask = page.getByRole("dialog", { name: "Ask Veloxa" });
  await expect(ask).toContainText("About an agent update");
  await expect(ask.getByLabel("Question")).toHaveValue(/What does ".+" mean for us/);  // prefilled, editable, unsent
  await ask.getByLabel("Question").fill("Why did it change?");
  await ask.getByRole("button", { name: "Send" }).click();
  await expect(ask.getByText(/regarding Synthetic org|Why did it change/).first()).toBeVisible();
});

test("the global Ask pill saves and reopens conversations, and citations open the evidence drawer", async ({ page }) => {
  await signIn(page);
  await page.goto("/#/timeline");
  await page.keyboard.press("Control+k");
  const ask = page.getByRole("dialog", { name: "Ask Veloxa" });
  await ask.getByLabel("Question").fill("What is our headcount?");
  await ask.getByRole("button", { name: "Send" }).click();
  await expect(ask.getByRole("article", { name: "Answer" }).first()).toContainText("People in the HR export: 2",
                                                                              { timeout: 15_000 });
  await ask.getByRole("button", { name: "Show source 1" }).first().click();
  await expect(page.getByRole("dialog", { name: "Employee_Master.xlsx" })).toBeVisible();
  await page.keyboard.press("Escape");
  await page.reload();
  await page.getByRole("button", { name: "Ask Veloxa (Ctrl+K)" }).click();
  const panel = page.getByRole("dialog", { name: "Ask Veloxa" });
  const saved = panel.getByRole("button", { name: "Saved" });
  if (await saved.isVisible()) await saved.click();                             // narrow screens: open the list
  await panel.getByRole("button", { name: /What is our headcount\? \d+ answer/ }).first().click();
  await expect(panel.getByRole("article", { name: "Answer" }).first()).toContainText("People in the HR export: 2",
                                                                                { timeout: 15_000 });
});

test("Intelligence keeps conflicts, changes, dependencies and department questions apart", async ({ page }) => {
  await signIn(page);
  await page.goto("/#/intelligence");
  await expect(page.getByRole("heading", { name: "Intelligence" })).toBeVisible();
  await page.getByRole("tab", { name: "Open questions" }).click();
  await page.getByLabel("Department", { exact: true }).selectOption("operations");
  await page.getByLabel("Clarification question").fill("Can Operations clarify the delivery date?");
  await page.getByRole("button", { name: "Submit request" }).click();
  await expect(page.getByText(/does not contact anyone/)).toBeVisible();
  await expect(page.getByText("Can Operations clarify the delivery date?").first()).toBeVisible();
});

test("a contribution is uploaded, validated and waits for an independent reviewer", async ({ page }) => {
  await signIn(page);
  await page.goto("/#/evidence");
  await expect(page.getByText("Uploading is not admission", { exact: false })).toBeVisible();
  await page.getByLabel("Source type").selectOption("ops_note");
  await page.getByLabel("Files").setInputFiles({ name: "Ops_note.txt", mimeType: "text/plain",
    buffer: Buffer.from("Synthetic operations note\nThe Orchid delivery moved to next week.") });
  await page.getByRole("button", { name: "Submit for review" }).click();
  await expect(page.getByText(/nothing is searchable/)).toBeVisible();
  await expect.poll(async () => {
    await page.reload();
    return await page.getByRole("row", { name: /Ops_note|Ops note/ }).first().textContent();
  }, { timeout: 20_000 }).toContain("Awaiting review");
  await page.getByRole("row", { name: /Ops_note|Ops note/ }).first().click();
  await expect(page.getByRole("region", { name: "Validation report" })).toContainText("1 record");
  await page.getByRole("button", { name: "Accept for authorisation" }).click();
  await expect(page.getByText("cannot review their own submission")).toBeVisible();   // separation of duties
});

test("Administration shows the signed-in access and refuses non-admin workspace administration", async ({ page }) => {
  await signIn(page);
  await page.goto("/#/admin");
  await expect(page.getByRole("heading", { name: "Your access" })).toBeVisible();
  await expect(page.getByText("tenant-wide (pre-workspace tenant)")).toBeVisible();
  await expect(page.getByText("Administrators only")).toBeVisible();
});

test("Ask expands from the overlay into the full-screen workspace and back without losing the conversation", async ({ page }) => {
  await signIn(page);
  await page.goto("/#/overview");
  await page.getByRole("button", { name: "Ask Veloxa (Ctrl+K)" }).click();
  const ask = page.getByRole("dialog", { name: "Ask Veloxa" });
  await expect(ask.getByLabel("Question")).toBeFocused();                       // focus moves into the composer
  await ask.getByLabel("Question").fill("What is our headcount?");
  await ask.getByRole("button", { name: "Send" }).click();
  const answer = ask.getByRole("article", { name: "Answer" });
  await expect(answer).toContainText("People in the HR export: 2", { timeout: 15_000 });
  await ask.getByLabel("Question").fill("an unsent draft");
  await ask.getByRole("button", { name: "Full screen" }).click();
  await expect(page).toHaveURL(/#\/ask$/);
  await expect(ask).toHaveClass(/full/);
  await expect(answer).toContainText("People in the HR export: 2");             // same thread, same element
  await expect(ask.getByLabel("Question")).toHaveValue("an unsent draft");      // the draft survives
  await expect(page.locator('nav[aria-label="Main"] button', { hasText: "Ask Veloxa" }))   // also when the nav is collapsed
    .toHaveAttribute("aria-current", "page");
  await page.goBack();                                                          // browser Back: overlay over the overview
  await expect(page).toHaveURL(/#\/overview$/);
  await expect(ask).not.toHaveClass(/full/);
  await expect(answer).toContainText("People in the HR export: 2");
  await page.keyboard.press("Escape");                                          // Escape closes the overlay
  await expect(ask).toBeHidden();
  await expect(page.getByRole("heading", { name: /Welcome/ })).toBeVisible();
  await page.keyboard.press("Control+k");                                       // reopening keeps the conversation
  await expect(page.getByRole("dialog", { name: "Ask Veloxa" }).getByRole("article", { name: "Answer" }))
    .toContainText("People in the HR export: 2");
});

test("the Ask workspace lists, searches and switches saved conversations", async ({ page }) => {
  await signIn(page);
  await page.goto("/#/ask");
  const ws = page.getByRole("dialog", { name: "Ask Veloxa" });
  for (const q of ["What is our headcount?", "What is Person Alpha's salary?"]) {
    await ws.getByRole("button", { name: /new conversation/i }).first().click();     // sidebar, or header on narrow screens
    await ws.getByLabel("Question").fill(q);
    await ws.getByRole("button", { name: "Send" }).click();
    await expect(ws.getByRole("article", { name: "Answer" }).first()).toBeVisible({ timeout: 15_000 });
  }
  const saved = ws.getByRole("button", { name: "Saved" });
  if (await saved.isVisible()) await saved.click();
  await ws.getByLabel("Search conversations").fill("salary");
  await expect(ws.getByRole("button", { name: /What is our headcount\?/ })).toHaveCount(0);
  await ws.getByRole("button", { name: /Person Alpha's salary/ }).first().click();
  await expect(ws.getByRole("note").filter({ hasText: "POL-PAY-01" }).first()).toBeVisible();
  await ws.getByRole("button", { name: "Return to window" }).click();          // collapses to the overlay
  await expect(ws).not.toHaveClass(/full/);
  await expect(ws.getByRole("note").filter({ hasText: "POL-PAY-01" }).first()).toBeVisible();
});

test("an answer separates the direct answer, figures, caveats and evidence", async ({ page }) => {
  await signIn(page);
  await page.goto("/#/ask");
  const ws = page.getByRole("dialog", { name: "Ask Veloxa" });
  await ws.getByLabel("Question").fill("What is the headcount by department?");
  await ws.getByRole("button", { name: "Send" }).click();
  const answer = ws.getByRole("article", { name: "Answer" });
  await expect(answer.locator(".brief-lead")).toBeVisible({ timeout: 15_000 });
  await expect(answer.getByRole("list", { name: "Values" })).toContainText("Testing");   // a breakdown of verified claims
  await expect(answer.getByText(/Grade [AB]/)).toBeVisible();
  await expect(answer.getByText(/contractors are not included/i).first()).toBeVisible();          // the scope caveat is kept
  await answer.getByRole("button", { name: "Show source 1" }).first().click();
  await expect(page.getByRole("dialog", { name: "Employee_Master.xlsx" })).toBeVisible();
});

test("the worry view states it is unranked while D-15 is not recorded, and lists findings", async ({ page }) => {
  await signIn(page);
  await page.goto("/#/worry");
  await expect(page.getByRole("heading", { name: "What should I worry about?" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Ranked worries" })).toContainText(/Not ranked yet|Nothing to rank/);
  await expect(page.getByRole("region", { name: "Findings" })).toBeVisible();
  await expect(page.locator("body")).not.toContainText("probability of");             // severity is never a probability
});

test("AI team: no HR mapping here, so only labelled synthetic agents appear, with the disclosure", async ({ page }) => {
  await signIn(page);
  await page.goto("/#/agents");
  await expect(page.getByRole("heading", { name: "AI team" })).toBeVisible();
  await expect(page.getByText("It is not the actual employee.").first()).toBeVisible();
  await expect(page.getByText("agent-eng-01")).toHaveCount(0);                     // no runtime IDs for the CEO
  const card = page.getByRole("article").filter({ hasText: "Synthetic test agent" }).first();
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "Start interview" }).click();
  const drawer = page.getByRole("dialog");
  await expect(drawer.getByRole("note")).toContainText("It is not the actual employee.");
  await expect(drawer.getByRole("button", { name: "Start interview" })).toHaveCount(0);   // one click: already started
  await expect(drawer.getByText(/AI assistant/)).toBeVisible();            // the agent's opening
  const box = (await drawer.boundingBox())!, vp = page.viewportSize()!;                    // centred modal, not a drawer
  expect(Math.abs(box.x + box.width / 2 - vp.width / 2)).toBeLessThan(4);
  expect(box.width / vp.width).toBeGreaterThan(0.6);
  await drawer.getByLabel("Message to the agent").fill("What do you not know?");
  await drawer.getByRole("button", { name: "Send" }).click();
  await expect(drawer.getByText(/My records cover/).first()).toBeVisible();
});

test("Overview: what needs attention and the AI team come right after the business figures", async ({ page }) => {
  await signIn(page);
  await page.goto("/#/overview");
  const attn = page.getByRole("region", { name: "What needs attention" });
  await expect(attn).toBeVisible();
  await expect(page.getByRole("region", { name: "Meet your AI team" })).toBeVisible();
  const figures = await page.getByRole("region", { name: "Key figures" }).boundingBox();
  const box = await attn.boundingBox();
  const brief = await page.getByRole("region", { name: "Executive brief" }).boundingBox();
  expect(box!.y).toBeGreaterThan(figures!.y);
  expect(box!.y).toBeLessThan(brief!.y);                                         // above the lower dashboard rows
  await expect(attn).not.toContainText("[object Object]");
  await expect(attn).not.toContainText(/\btied\b|Ranked #/);                       // attention groups, no jargon
  const warn = await page.getByRole("region", { name: "Early warnings" }).boundingBox();
  expect(warn!.y).toBeLessThan(brief!.y);                                        // early warnings right after it
  await attn.getByRole("button", { name: "View all concerns" }).click();
  await expect(page.getByRole("heading", { name: "What should I worry about?" })).toBeVisible();
});

test("every answer is explained automatically; abstentions say why; refusals are never dressed up", async ({ page }) => {
  await signIn(page);
  await page.goto("/#/ask");
  const ws = page.getByRole("dialog", { name: "Ask Veloxa" });
  await ws.getByLabel("Question", { exact: true }).fill("What is our headcount?");
  await ws.getByRole("button", { name: "Send" }).click();
  const answer = ws.getByRole("article", { name: "Answer" }).last();
  const ex = answer.getByLabel("Explanation");
  await expect(ex).toBeVisible();                                                   // no click needed
  await expect(ex).toContainText("People in the HR export: 2");
  await expect(answer.getByRole("button", { name: /Explain/ })).toHaveCount(0);     // one experience, no buttons
  await expect(ex.getByRole("note")).toHaveText("Written from verified records.");   // short, never implies a model
  await expect(page.locator("body")).not.toContainText("DENY_ALL");
  await ex.getByText("Evidence and verified details").click();
  await expect(ex.getByRole("button", { name: /View source/ }).first()).toBeVisible();

  await ws.getByLabel("Question", { exact: true }).fill("What will revenue be next year?");
  await ws.getByRole("button", { name: "Send" }).click();
  const abstained = ws.getByRole("article", { name: "Answer" }).last().getByLabel("Explanation");
  await expect(abstained.getByRole("heading", { name: "Why there is no answer" })).toBeVisible();
  await expect(abstained).toContainText("cannot answer this from the admitted records");

  await ws.getByLabel("Question", { exact: true }).fill("What is Person Alpha's salary?");
  await ws.getByRole("button", { name: "Send" }).click();
  await expect(page.getByRole("note").filter({ hasText: "POL-PAY-01" }).first()).toBeVisible({ timeout: 15_000 });
  await expect(ws.getByRole("article", { name: "Answer" }).last().getByLabel("Explanation")).toHaveCount(0);
});

test("an explanation that fails to load is an error, not an explanation", async ({ page }) => {
  await signIn(page);
  await page.route("**/v1/answers/*/explain*", (r) => r.fulfill({ status: 500, contentType: "application/json",
    body: JSON.stringify({ error: "internal_error", request_id: "req-explain-1" }) }));
  await page.goto("/#/ask");
  const ws = page.getByRole("dialog", { name: "Ask Veloxa" });
  await ws.getByLabel("Question", { exact: true }).fill("What is our headcount?");
  await ws.getByRole("button", { name: "Send" }).click();
  const answer = ws.getByRole("article", { name: "Answer" }).last();
  await expect(answer.getByRole("alert")).toContainText("Veloxa hit an internal error");
  await expect(answer.getByLabel("Explanation")).toHaveCount(0);
  await expect(answer).toContainText("People in the HR export: 2");                 // the verified answer itself stays
});

test("the executive brief is an explanation with sources, and API errors are explained, not hidden", async ({ page }) => {
  await signIn(page);
  await page.goto("/#/overview");
  const brief = page.getByRole("region", { name: "Executive brief" });
  await expect(brief.getByLabel("Explanation")).toContainText("Headcount in the HR export: 2");
  await expect(brief.getByRole("button", { name: /Explain/ })).toHaveCount(0);
  await page.route("**/v1/worry", (r) => r.fulfill({ status: 500, contentType: "application/json",
    body: JSON.stringify({ error: "internal_error", request_id: "req-test-123" }) }));
  await page.goto("/#/worry");
  const alert = page.getByRole("alert").first();
  await expect(alert).toContainText("Veloxa hit an internal error");
  await expect(alert).toContainText("req-test-123");                               // the operator can trace it
  await expect(page.locator("body")).not.toContainText("Request failed (500)");
});

test("Admin states the AI-wording model and workspace scope accurately", async ({ page }) => {
  await signIn(page);
  await page.goto("/#/admin");
  const panel = page.getByLabel("AI-assisted wording");
  await expect(panel).toContainText("off for this workspace");                      // this test API has no model
  await expect(panel).toContainText("none configured");
  await expect(panel).toContainText("DENY_ALL");                                     // diagnostics live here only
});

test("sign out ends the session and returns to the sign-in screen", async ({ page }) => {
  await signIn(page);
  await page.goto("/#/admin");
  await expect(page.getByRole("region", { name: "Account" })).toContainText("a developer token");
  await page.getByRole("region", { name: "Account" }).getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in to your workspace" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Sign in to your workspace" })).toBeVisible();
});

test("sign-in errors are explained", async ({ page }) => {
  await page.goto("/#/signin?error=invalid_invitation");
  await expect(page.getByRole("alert")).toContainText("invitation is invalid, expired or already used");
  await page.goto("/#/signin?error=sso_not_configured");
  await page.reload();
  await expect(page.getByRole("alert")).toContainText("Single sign-on isn't configured");
});

test("People is the HR directory; AI assistants are a separate, disclosed section", async ({ page }) => {
  await signIn(page);
  await page.goto("/#/people");
  await expect(page.getByRole("heading", { name: "People", exact: true })).toBeVisible();
  await expect(page.getByText(/people in the HR export/i)).toBeVisible();
  await expect(page.getByText("AI assistants available")).toBeVisible();
  await page.goto("/#/overview");
  await expect(page.getByRole("region", { name: "People overview" })).toHaveCount(0);     // no second roster
  await expect(page.getByRole("region", { name: "Meet your AI team" })).toContainText("not the employees");
});

test("the Jira task card fills from plain words, uses Jira's own people and dates, and writes nothing unconfirmed",
  async ({ page }) => {
  const json = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  const writes: string[] = [];
  await page.route("**/v1/integrations/jira/**", (r) => {
    const u = r.request().url();
    if (r.request().method() === "POST") { writes.push(u); return r.fulfill(json({})); }
    if (u.includes("/projects/VIT/issue-types/10016/date-fields"))
      return r.fulfill(json({ due: true, start_field: "customfield_10015", start_name: "Start date" }));
    if (u.includes("/projects/VIT/issue-types")) return r.fulfill(json({ issue_types: [{ id: "10016", name: "Task" }] }));
    if (u.includes("/projects/VIT/assignable"))
      return r.fulfill(json({ users: [{ account_id: "acc-1", name: "Alex Example" }, { account_id: "acc-2", name: "Sam Sample" }] }));
    if (u.endsWith("/projects")) return r.fulfill(json({ projects: [{ key: "VIT", name: "Veloxa IT" }] }));
    return r.fulfill(json({}));
  });
  await signIn(page);
  await page.getByRole("button", { name: "Ask Veloxa (Ctrl+K)" }).click();
  await page.getByRole("dialog", { name: "Ask Veloxa" }).getByRole("button", { name: "Jira task" }).click();
  const card = page.getByRole("region", { name: "Jira task" });
  await card.getByLabel("Describe it in your own words").fill(
    "Create a task to confirm the GA date with the release owner, assign it to Alex, due 2099-03-01, starting 2099-02-20");
  await card.getByRole("button", { name: "Fill the form" }).click();
  await expect(card.getByLabel("Title")).toHaveValue("Confirm the GA date with the release owner");
  await expect(card.getByLabel("Due date")).toHaveValue("2099-03-01");
  await expect(card.getByLabel("Start date")).toHaveValue("2099-02-20");        // only because Jira reports the field
  await expect(card.getByLabel("Assignee")).toHaveValue("acc-1");               // from Jira's own assignable list
  await expect(card).toContainText("Dates to write: due 2099-03-01 and start 2099-02-20");
  await card.getByLabel("Describe it in your own words").fill("Create a task to check backups, assign it to me");
  await card.getByRole("button", { name: "Fill the form" }).click();
  await expect(card).toContainText("doesn't assume which Jira account is yours");  // "me" is never guessed
  expect(writes).toEqual([]);                                                    // nothing previewed or written
});
