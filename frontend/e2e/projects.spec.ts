import { expect, test, type Locator, type Page } from "@playwright/test";
import { expectHeading, pickOption, signIn, uniqueSuffix } from "./helpers";

/**
 * Critical E2E — project time lifecycle.
 *
 * Every assertion reads back a server state (status badges re-rendered after
 * each transition, the unbilled-time preview endpoint, the invoice the
 * backend created). Nobody may approve their own time
 * (`self_approval_forbidden`), so the owner logs hours for the dev tenant's
 * second member, "Priya Staff" (projects-agent-staff@easybook.local), and
 * approves them as a manager.
 *
 * Status badges carry a marker glyph for some states ("● Active",
 * "● Approved"), so those are matched as a word rather than exact text.
 */

async function pickIn(scope: Locator, label: string, search: string, option: RegExp): Promise<void> {
  const input = scope.getByRole("combobox", { name: label });
  await input.click();
  await input.fill(search);
  const choice = scope.getByRole("option", { name: option }).first();
  await expect(choice).toBeVisible();
  await choice.dispatchEvent("pointerdown");
}

/** Creates an hourly project, activates it and adds a "Build" task. Returns the project URL. */
async function createActiveProject(page: Page, name: string, code: string): Promise<string> {
  await page.goto("/projects/new");
  await expectHeading(page, "New project");
  await pickOption(page, "Customer", /E2E Customer/);
  await page.getByLabel("Project code").fill(code);
  await page.getByLabel(/^Name/).fill(name);
  await page.getByLabel("Default hourly rate").fill("1200");
  await pickOption(page, "Service item", "Consulting");
  await page.getByRole("button", { name: "Create project" }).click();

  await page.waitForURL(/\/projects\/[0-9a-f-]{36}$/);
  await expectHeading(page, name);
  const main = page.getByRole("main");
  await expect(main.getByText("Draft", { exact: true }).first()).toBeVisible();

  await main.getByRole("button", { name: "Activate", exact: true }).click();
  await confirm(page, "Activate this project?", "Activate");
  await expect(main.getByRole("button", { name: "Put on hold" })).toBeVisible();
  await expect(main.getByText(/^\W*Active$/).first()).toBeVisible();

  await main.getByRole("button", { name: "Add task", exact: true }).click();
  const taskDialog = page.getByRole("dialog", { name: "Add task" });
  await taskDialog.getByLabel("Task name").fill("Build");
  await taskDialog.getByRole("button", { name: "Add task", exact: true }).click();
  await expect(taskDialog).toBeHidden();
  await expect(main.getByRole("table", { name: /Tasks on/ }).getByText("Build", { exact: true })).toBeVisible();

  return page.url();
}

/** Logs time for Priya through the Log time dialog on a project-filtered timesheet. */
async function logTimeForPriya(page: Page, projectName: string, hours: string, description: string): Promise<void> {
  await page.getByRole("main").getByRole("button", { name: "Log time", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Log time" });
  // The project comes preselected from the list filter.
  await expect(dialog.getByRole("combobox", { name: "Project" })).toHaveValue(projectName);
  await pickIn(dialog, "Task", "Build", /Build/);
  await dialog.getByLabel("Hours").fill(hours);
  await pickIn(dialog, "Person", "Priya", /Priya Staff/);
  await dialog.getByLabel("Description").fill(description);
  await dialog.getByRole("button", { name: "Log time", exact: true }).click();
  await expect(dialog).toBeHidden();
}

/**
 * Confirms a dialog. The dev backend throttles per user (300/min) and every
 * agent and test shares one login, so a 429 can land here; the dialog keeps
 * the error and the same Idempotency-Key, and pressing the button again is
 * exactly what a person would do — the retry replays, it cannot double-apply.
 */
async function confirm(page: Page, title: string, button: string): Promise<void> {
  const dialog = page.getByRole("dialog", { name: title });
  for (let attempt = 0; attempt < 4; attempt += 1) {
    await dialog.getByRole("button", { name: button, exact: true }).click();
    const outcome = await Promise.race([
      dialog.waitFor({ state: "hidden", timeout: 20_000 }).then(() => "closed" as const),
      dialog.getByText(/throttled/i).waitFor({ timeout: 20_000 }).then(() => "throttled" as const),
    ]);
    if (outcome === "closed") return;
    await page.waitForTimeout(3_000);
  }
  await expect(dialog).toBeHidden();
}

/**
 * Waits for a server-rendered state to appear. If the shared dev backend
 * throttled the re-render (the page then shows its error state saying so),
 * waits and reloads — the write already happened; only the read was refused.
 */
async function expectShown(page: Page, locator: Locator): Promise<void> {
  for (let attempt = 0; attempt < 4; attempt += 1) {
    try {
      await expect(locator).toBeVisible({ timeout: 10_000 });
      return;
    } catch (error) {
      if ((await page.getByText(/throttled/i).count()) === 0) throw error;
      await page.waitForTimeout(5_000);
      await page.reload();
    }
  }
  await expect(locator).toBeVisible();
}

test("creates a project, logs time for a colleague, submits, approves and invoices it", async ({ page }) => {
  // Several route compilations on a shared dev server; the default 30s is too tight.
  test.setTimeout(150_000);
  const suffix = uniqueSuffix();
  const name = `Projects E2E ${suffix}`;
  const work = `E2E work ${suffix}`;
  await signIn(page);

  const projectUrl = await createActiveProject(page, name, `PRJ-E2E-${suffix}`);
  const projectId = projectUrl.split("/").pop() ?? "";

  await page.goto(`/projects/timesheets?project=${projectId}`);
  await expectHeading(page, "Timesheets");
  await logTimeForPriya(page, name, "2.5", work);

  const row = page.getByRole("row").filter({ hasText: work });
  await expectShown(page, row.getByText("Draft", { exact: true }));
  await expectShown(page, row.getByText("Priya Staff"));

  await row.getByRole("button", { name: "Submit", exact: true }).click();
  await confirm(page, "Submit this entry for approval?", "Submit");
  await expectShown(page, row.getByText("Submitted", { exact: true }));

  await row.getByRole("button", { name: "Approve", exact: true }).click();
  await confirm(page, "Approve this entry?", "Approve");
  await expectShown(page, row.getByText(/^\W*Approved$/));

  // The unbilled-time preview now offers exactly this entry for billing.
  await page.goto(projectUrl);
  const unbilled = page.getByRole("table", { name: /Unbilled time on/ });
  await expect(unbilled.getByRole("row").filter({ hasText: "Priya Staff" })).toContainText("2.5");

  // Invoice it: a DRAFT sales invoice, opened straight from the response.
  await page.getByRole("main").getByRole("button", { name: "Invoice time" }).click();
  const invoiceDialog = page.getByRole("dialog", { name: "Invoice approved time" });
  await pickIn(invoiceDialog, "Receivable account", "Receivable", /Accounts Receivable/);
  await invoiceDialog.getByRole("button", { name: "Create draft invoice" }).click();
  await page.waitForURL(/\/sales\/invoices\/[0-9a-f-]{36}$/);
  await expectHeading(page, "Draft invoice");
  // 2.5 h at the project's 1,200.00 rate, priced by the engine. Closed
  // confirm dialogs repeat the total, so only a visible match counts.
  await expect(page.getByRole("main").getByText("₹3,000.00").filter({ visible: true }).first()).toBeVisible();
});

test("bulk-submits and bulk-approves entries, then rejects and deletes one", async ({ page }) => {
  test.setTimeout(150_000);
  const suffix = uniqueSuffix();
  const name = `Projects E2E bulk ${suffix}`;
  await signIn(page);

  const projectUrl = await createActiveProject(page, name, `PRJ-E2B-${suffix}`);
  const projectId = projectUrl.split("/").pop() ?? "";

  await page.goto(`/projects/timesheets?project=${projectId}`);
  await logTimeForPriya(page, name, "1", `alpha ${suffix}`);
  await logTimeForPriya(page, name, "1.5", `beta ${suffix}`);
  const alpha = page.getByRole("row").filter({ hasText: `alpha ${suffix}` });
  const beta = page.getByRole("row").filter({ hasText: `beta ${suffix}` });
  await expectShown(page, alpha.getByText("Draft", { exact: true }));
  await expectShown(page, beta.getByText("Draft", { exact: true }));

  // Bulk submit: the filtered page holds just these two entries.
  const main = page.getByRole("main");
  await main.getByRole("checkbox", { name: "Select all entries on this page" }).check();
  await main.getByRole("button", { name: "Submit selected (2)" }).click();
  await confirm(page, "Submit these entries for approval?", "Submit selected (2)");
  await expectShown(page, alpha.getByText("Submitted", { exact: true }));
  await expectShown(page, beta.getByText("Submitted", { exact: true }));

  // Bulk approve (the refresh cleared the selection).
  await main.getByRole("checkbox", { name: "Select all entries on this page" }).check();
  await main.getByRole("button", { name: "Approve selected (2)" }).click();
  await confirm(page, "Approve these entries?", "Approve selected (2)");
  await expectShown(page, alpha.getByText(/^\W*Approved$/));
  await expectShown(page, beta.getByText(/^\W*Approved$/));

  // Reject beta with a reason — an approver may change their mind before invoicing.
  await beta.getByRole("button", { name: "Reject", exact: true }).click();
  const reject = page.getByRole("dialog", { name: "Reject this entry?" });
  await reject.getByLabel("Reason").fill("Wrong task");
  await reject.getByRole("button", { name: "Reject", exact: true }).click();
  await expect(reject).toBeHidden();
  await expectShown(page, beta.getByText(/^\W*Rejected$/));
  await expectShown(page, beta.getByText("Wrong task", { exact: true }));

  // Delete beta.
  await beta.getByRole("button", { name: /^Delete/ }).click();
  await confirm(page, "Delete this time entry?", "Delete entry");
  await expect(beta).toHaveCount(0);
  await expectShown(page, alpha.getByText(/^\W*Approved$/));

  // Only alpha is left to bill.
  await page.goto(projectUrl);
  const unbilled = page.getByRole("table", { name: /Unbilled time on/ });
  await expect(unbilled.getByRole("row")).toHaveCount(2); // header + alpha
  await expect(unbilled).toContainText("₹1,200.00");
});
