import { expect, test, type Page } from "@playwright/test";
import { expectHeading, pickOption, signIn, uniqueSuffix } from "./helpers";

/**
 * Critical E2E — banking: create a bank account → import a CSV statement
 * through the mapping wizard → categorize one line (posts a journal) →
 * exclude another (posts nothing).
 *
 * Every step is asserted against what the SERVER returns afterwards, read back
 * through the same BFF the browser uses. Nothing is mocked.
 *
 * Each bank account needs its own unlinked ledger account (the pairing is
 * 1:1 — account_already_linked), so the test creates one through the API
 * first; that is setup, not the flow under test.
 */

async function apiJson<T>(page: Page, path: string): Promise<T> {
  const response = await page.request.get(`/api/bff/${path}`);
  expect(response.ok(), `GET ${path} → ${response.status()}`).toBeTruthy();
  return (await response.json()) as T;
}

// Several pages compile on first visit in the shared dev server; the flow
// spans eight navigations.
test.setTimeout(240_000);

test("creates a bank account, imports a statement, categorizes one line and excludes another", async ({ page }) => {
  const suffix = uniqueSuffix();
  const accountName = `Banking E2E ${suffix}`;
  await signIn(page);

  const ledger = await page.request.post("/api/bff/accounting/accounts", {
    data: { code: `BE${suffix}`, name: `Banking E2E ledger ${suffix}`, account_type: "asset" },
  });
  expect(ledger.ok(), `ledger account setup → ${ledger.status()}`).toBeTruthy();

  // 1. Create the bank account.
  await page.goto("/banking/accounts/new");
  await expectHeading(page, "New bank account");
  await page.getByRole("textbox", { name: "Name (required)" }).fill(accountName);
  await pickOption(page, "Ledger account", `BE${suffix}`);
  await page.getByLabel("Account number").fill("50100987654321");
  await page.getByRole("button", { name: "Create bank account" }).click();
  await page.waitForURL(/\/banking\/accounts\/[0-9a-f-]{36}$/);
  await expectHeading(page, accountName);
  const bankAccountId = page.url().split("/").pop() ?? "";
  // Only the last four digits ever come back.
  await expect(page.getByText("••••4321").first()).toBeVisible();

  // 2. Import a CSV statement through the wizard.
  await page.getByRole("link", { name: "Import statement" }).first().click();
  await expectHeading(page, "Import statement");
  const csv = [
    "Date,Narration,Reference,Withdrawal,Deposit",
    `05/09/2026,E2E bank charges ${suffix},CHG${suffix},118.00,`,
    `08/09/2026,E2E customer receipt ${suffix},UTR${suffix},,2500.00`,
    `14/09/2026,E2E duplicate artefact ${suffix},ART${suffix},500.00,`,
  ].join("\r\n");
  await page.getByLabel("Statement file (CSV)").setInputFiles({
    name: `banking-e2e-${suffix}.csv`,
    mimeType: "text/csv",
    buffer: Buffer.from(csv, "utf-8"),
  });

  // Pre-filled from the header names — confirmed here, as a person would.
  await expect(page.getByLabel("How amounts are shown")).toHaveValue("debit_credit");
  await expect(page.getByLabel("Date column")).toHaveValue("Date");
  await expect(page.getByLabel("Withdrawal column (money out)")).toHaveValue("Withdrawal");
  await expect(page.getByLabel("Deposit column (money in)")).toHaveValue("Deposit");
  await page.getByLabel("Date format").selectOption("%d/%m/%Y");
  await page.getByLabel("Description column").selectOption("Narration");
  await expect(page.getByRole("table", { name: /First rows of/ }).getByText(`E2E bank charges ${suffix}`)).toBeVisible();

  await page.getByRole("button", { name: "Import statement" }).click();
  await expect(page.getByRole("heading", { name: "Import finished" })).toBeVisible();

  const imported = await apiJson<{ count: number; results: Array<{ id: string; description: string; amount: string; status: string }> }>(
    page,
    `bank-transactions?bank_account=${bankAccountId}`,
  );
  expect(imported.count).toBe(3);
  const charges = imported.results.find((row) => row.description === `E2E bank charges ${suffix}`);
  const artefact = imported.results.find((row) => row.description === `E2E duplicate artefact ${suffix}`);
  // The server read the withdrawal column as money out.
  expect(charges?.amount).toBe("-118.00");
  expect(artefact?.status).toBe("unmatched");

  // 3. Categorize the bank charge — posts one journal.
  await page.getByRole("link", { name: "Review unmatched lines" }).click();
  await expectHeading(page, "Bank transactions");
  await page.getByRole("row", { name: new RegExp(`E2E bank charges ${suffix}`) }).getByRole("link").first().click();
  await expectHeading(page, `E2E bank charges ${suffix}`);
  await page.getByRole("button", { name: "Categorize", exact: true }).click();
  const categorize = page.getByRole("dialog", { name: "Categorize and post a journal" });
  await expect(categorize).toBeVisible();
  await pickOption(page, "Account", "5200");
  await categorize.getByRole("button", { name: "Categorize and post" }).click();
  await expect(categorize).toBeHidden();

  await expect(page.getByRole("table", { name: /Confirmed matches/ }).getByText(/Journal/)).toBeVisible();
  const categorized = await apiJson<{ status: string }>(page, `bank-transactions/${charges?.id}`);
  expect(categorized.status).toBe("matched");
  const matches = await apiJson<Array<{ match_type: string; journal_entry: string | null; is_confirmed: boolean }>>(
    page,
    `bank-transactions/${charges?.id}/suggestions`,
  );
  expect(matches).toHaveLength(1);
  expect(matches[0]).toMatchObject({ match_type: "categorization", is_confirmed: true });
  expect(matches[0]?.journal_entry).toBeTruthy();

  // 4. Exclude the artefact — a reason is required, nothing is posted.
  await page.goto(`/banking/transactions/${artefact?.id}`);
  await expectHeading(page, `E2E duplicate artefact ${suffix}`);
  await page.getByRole("button", { name: "Exclude", exact: true }).click();
  const exclude = page.getByRole("dialog", { name: "Exclude this transaction?" });
  await expect(exclude.getByRole("button", { name: "Exclude", exact: true })).toBeDisabled();
  await exclude.getByLabel("Reason").fill("Bank emitted this line twice");
  await exclude.getByRole("button", { name: "Exclude", exact: true }).click();
  await expect(exclude).toBeHidden();

  await expect(page.getByText("Bank emitted this line twice")).toBeVisible();
  await expect(page.getByRole("button", { name: "Restore" })).toBeVisible();
  const excluded = await apiJson<{ status: string; excluded_reason: string }>(page, `bank-transactions/${artefact?.id}`);
  expect(excluded).toMatchObject({ status: "excluded", excluded_reason: "Bank emitted this line twice" });
});

test("edits a bank account, adds a statement line by hand and records a transfer", async ({ page }) => {
  const suffix = uniqueSuffix();
  await signIn(page);

  const created: Array<{ id: string; name: string }> = [];
  for (const label of ["X", "Y"]) {
    const ledger = await page.request.post("/api/bff/accounting/accounts", {
      data: { code: `B${label}${suffix}`, name: `Banking E2E ledger ${label} ${suffix}`, account_type: "asset" },
    });
    expect(ledger.ok()).toBeTruthy();
    const account = await page.request.post("/api/bff/bank-accounts", {
      data: {
        name: `Banking E2E ${label} ${suffix}`,
        account_id: ((await ledger.json()) as { id: string }).id,
        opening_balance: "5000.00",
        opening_balance_date: "2026-08-31",
      },
    });
    expect(account.ok()).toBeTruthy();
    created.push((await account.json()) as { id: string; name: string });
  }
  const [source, destination] = created as [{ id: string; name: string }, { id: string; name: string }];

  // A line added by hand: a positive amount plus a direction, sent signed.
  await page.goto(`/banking/accounts/${source.id}`);
  await expectHeading(page, source.name);
  await page.getByRole("button", { name: "Add transaction" }).click();
  const addLine = page.getByRole("dialog", { name: "Add a statement line" });
  await addLine.getByLabel("Direction").selectOption("out");
  await addLine.getByRole("textbox", { name: /Amount/ }).fill("42.50");
  await addLine.getByLabel("Description").fill(`Hand-entered fee ${suffix}`);
  await addLine.getByRole("button", { name: "Add line" }).click();
  await expect(addLine).toBeHidden();
  const lines = await apiJson<{ results: Array<{ amount: string; description: string }> }>(
    page,
    `bank-transactions?bank_account=${source.id}`,
  );
  expect(lines.results).toEqual([expect.objectContaining({ amount: "-42.50", description: `Hand-entered fee ${suffix}` })]);

  // Editing: statement lines now exist, so the opening balance is locked and
  // must not be sent at all (the service refuses the key even unchanged).
  await page.goto(`/banking/accounts/${source.id}/edit`);
  await expectHeading(page, `Edit ${source.name}`);
  await expect(page.getByRole("textbox", { name: "Opening balance" })).toBeDisabled();
  await page.getByLabel("Bank name").fill(`Edited bank ${suffix}`);
  await page.getByRole("button", { name: "Save changes" }).click();
  await page.waitForURL(new RegExp(`/banking/accounts/${source.id}$`));
  const edited = await apiJson<{ bank_name: string; opening_balance: string }>(page, `bank-accounts/${source.id}`);
  expect(edited).toMatchObject({ bank_name: `Edited bank ${suffix}`, opening_balance: "5000.00" });

  // A transfer from the account page: confirmed, then posted by the server.
  await page.getByRole("link", { name: "Transfer", exact: true }).click();
  await expectHeading(page, "New transfer");
  await pickOption(page, "To account", destination.name);
  await page.getByRole("textbox", { name: /Amount/ }).fill("1250.00");
  await page.getByLabel("Reference").fill(`TRF-E2E-${suffix}`);
  await page.getByRole("button", { name: "Record transfer" }).click();
  const confirm = page.getByRole("dialog", { name: "Record and post this transfer?" });
  await expect(confirm.getByText(`from ${source.name} to ${destination.name}`, { exact: false })).toBeVisible();
  await confirm.getByRole("button", { name: "Record transfer" }).click();
  await page.waitForURL(/\/banking\/transfers$/);

  const transfers = await apiJson<{ results: Array<{ reference: string; amount: string; status: string; accounting_journal: string | null }> }>(
    page,
    "bank-transfers?page_size=200",
  );
  const recorded = transfers.results.find((row) => row.reference === `TRF-E2E-${suffix}`);
  expect(recorded).toMatchObject({ amount: "1250.00", status: "posted" });
  expect(recorded?.accounting_journal).toBeTruthy();
});
