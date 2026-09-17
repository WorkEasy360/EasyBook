import { expect, test, type Locator, type Page } from "@playwright/test";
import { expectHeading, signIn, uniqueSuffix } from "./helpers";

/**
 * Critical E2E — manual journal: draft → post → the ledger still balances.
 *
 * Asserts what the SERVER returns after each step: the journal number and
 * status assigned on posting, the line sums of the saved journal, and the
 * trial balance's own `is_balanced` verdict. The form's balance panel is only
 * checked for blocking an unbalanced save — never used as proof of anything.
 *
 * Uses the dev tenant's 5200 Office Expenses and 1000 Bank - HDFC accounts.
 */

/** Picks a Combobox option inside one line's fieldset (every line has an "Account" field). */
async function pickIn(scope: Locator, label: string, search: string, option: RegExp): Promise<void> {
  const input = scope.getByRole("combobox", { name: label });
  await input.click();
  await input.fill(search);
  const choice = scope.getByRole("option", { name: option }).first();
  await expect(choice).toBeVisible();
  await choice.dispatchEvent("pointerdown");
}

/**
 * Confirms a dialog, pressing again if the shared dev backend throttled the
 * request (one login is shared by every agent and test). The dialog keeps the
 * same Idempotency-Key, so a retry replays rather than posting twice.
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

test("records a balanced manual journal, posts it, and the trial balance stays balanced", async ({ page }) => {
  // Several route compilations on a shared dev server; the default 30s is too tight.
  test.setTimeout(120_000);
  const memo = `Accounting E2E ${uniqueSuffix()}`;
  await signIn(page);

  await page.goto("/accounting/journals/new");
  await expectHeading(page, "New journal");
  const form = page.getByRole("main");

  const line1 = form.getByRole("group", { name: "Line 1" });
  const line2 = form.getByRole("group", { name: "Line 2" });

  await form.getByLabel("Memo").fill(memo);
  await pickIn(line1, "Account", "Office", /5200 · Office Expenses/);
  await line1.getByLabel("Description").fill("Printer paper");
  await line1.getByLabel("Debit").fill("125.50");
  await pickIn(line2, "Account", "HDFC", /1000 · Bank - HDFC/);
  await line2.getByLabel("Credit").fill("100");

  // Unbalanced: the estimate says so, and saving is refused before any request.
  await expect(form.getByText("Not balanced yet.")).toBeVisible();
  await form.getByRole("button", { name: "Save as draft" }).click();
  await expect(form.getByRole("alert").filter({ hasText: "Debits and credits must be equal" })).toBeVisible();
  await expect(page).toHaveURL(/\/accounting\/journals\/new$/);

  await line2.getByLabel("Credit").fill("125.50");
  await expect(form.getByText("Balanced — ready to save.")).toBeVisible();
  await form.getByRole("button", { name: "Save as draft" }).click();

  // A draft: no number yet, nothing in the ledger.
  await page.waitForURL(/\/accounting\/journals\/[0-9a-f-]{36}$/);
  await expectHeading(page, "Draft journal");
  const main = page.getByRole("main");
  await expect(main.getByText(memo).first()).toBeVisible();
  await expect(main.getByText("Sum of lines (estimate)")).toBeVisible();

  await main.getByRole("button", { name: "Post journal" }).click();
  await confirm(page, "Post this journal?", "Post journal");

  // Posting assigns the number and status — both read back from the server.
  await expectHeading(page, /^JE-\d+$/);
  await expect(main.getByText("Posted", { exact: true }).first()).toBeVisible();
  await expect(main.getByRole("button", { name: "Post journal" })).toHaveCount(0);
  await expect(main.getByRole("button", { name: "Reverse", exact: true })).toBeVisible();
  await expect(main.getByText("Sum of lines", { exact: true })).toBeVisible();
  const journalNumber = (await page.getByRole("heading", { level: 1 }).textContent())?.trim() ?? "";
  const journalUrl = page.url();

  // It is on the posted list (one large page: other runs post journals on the same date)…
  await page.goto("/accounting/journals?status=posted&page_size=200");
  await expect(page.getByRole("main").getByRole("link", { name: journalNumber, exact: true })).toBeVisible();

  // …and the engine still reports a balanced ledger.
  await page.goto("/accounting/trial-balance");
  await expectHeading(page, "Trial balance");
  await expect(page.getByRole("main").getByText("The engine reports closing debits equal closing credits.")).toBeVisible();
  await expect(page.getByText("Out of balance")).toHaveCount(0);

  // Reverse it: a NEW posted journal with the sides swapped; the original is
  // kept and marked reversed, never edited.
  await page.goto(journalUrl);
  await expectHeading(page, journalNumber);
  await main.getByRole("button", { name: "Reverse", exact: true }).click();
  const reverse = page.getByRole("dialog", { name: `Reverse ${journalNumber}?` });
  await reverse.getByLabel("Memo").fill(`${memo} reversal`);
  await confirm(page, `Reverse ${journalNumber}?`, "Reverse journal");

  await page.waitForURL((url) => url.href !== journalUrl && /\/accounting\/journals\/[0-9a-f-]{36}$/.test(url.pathname));
  await expectHeading(page, /^JE-\d+$/);
  await expect(main.getByText(`${memo} reversal`).first()).toBeVisible();
  await expect(main.getByRole("link", { name: "Original journal" })).toBeVisible();
  // Swapped: the 125.50 now sits on the credit side of 5200.
  const expenseLine = main.getByRole("row").filter({ hasText: "5200 · Office Expenses" });
  await expect(expenseLine.getByRole("cell").nth(3)).toHaveText("0.00");
  await expect(expenseLine.getByRole("cell").nth(4)).toHaveText("125.50");

  await main.getByRole("link", { name: "Original journal" }).click();
  await expectHeading(page, journalNumber);
  await expect(main.getByText(/^\W*Reversed$/).first()).toBeVisible();
  await expect(main.getByRole("button", { name: "Reverse", exact: true })).toHaveCount(0);
});

test("adds an account to the chart and deactivates it", async ({ page }) => {
  test.setTimeout(120_000);
  const suffix = uniqueSuffix();
  const code = `9${suffix.slice(-6)}`;
  const name = `Accounting E2E ${suffix}`;
  await signIn(page);

  await page.goto("/accounting/accounts");
  await expectHeading(page, "Chart of accounts");
  await page.getByRole("main").getByRole("button", { name: "New account" }).click();
  const create = page.getByRole("dialog", { name: "New account" });
  await create.getByLabel(/^Code/).fill(code);
  await create.getByLabel(/^Name/).fill(name);
  await create.getByLabel(/^Type/).selectOption("expense");
  await create.getByLabel("Subtype").fill("e2e");
  await confirm(page, "New account", "Create account");

  // The list re-renders from the server; open the new account from it.
  await page.goto("/accounting/accounts?account_type=expense&page_size=200");
  await page.getByRole("main").getByRole("link", { name: code, exact: true }).click();
  await expectHeading(page, `${code} · ${name}`);
  await expect(page.getByRole("main").getByText("Expense account — increases on the debit side.")).toBeVisible();

  // Deactivate it so test accounts never crowd the pickers (they list active accounts only).
  await page.getByRole("main").getByRole("button", { name: /^Edit/ }).click();
  const edit = page.getByRole("dialog", { name: `Edit ${code} · ${name}` });
  await edit.getByLabel("Active").uncheck();
  await confirm(page, `Edit ${code} · ${name}`, "Save changes");
  await expect(page.getByRole("main").getByText(/^\W*Inactive$/).first()).toBeVisible();
});
