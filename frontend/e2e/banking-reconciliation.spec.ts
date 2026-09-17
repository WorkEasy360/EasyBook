import { expect, test, type Page } from "@playwright/test";
import { expectHeading, signIn, uniqueSuffix } from "./helpers";

/**
 * Critical E2E — reconciliation sign-off.
 *
 * Start a reconciliation in the UI, see the backend's verdict block
 * completion while a line is open, explain the line, then complete — and
 * assert the server's frozen cleared balance. The Complete button must follow
 * `can_complete` from GET …/summary/ and nothing else.
 *
 * Setup (ledger account, bank account, one statement line, and later its
 * categorization) goes through the API: the account and categorize flows have
 * their own spec (banking.spec.ts).
 */

async function post<T>(page: Page, path: string, data: unknown): Promise<T> {
  const response = await page.request.post(`/api/bff/${path}`, { data });
  expect(response.ok(), `POST ${path} → ${response.status()} ${await response.text()}`).toBeTruthy();
  return (await response.json()) as T;
}

async function get<T>(page: Page, path: string): Promise<T> {
  const response = await page.request.get(`/api/bff/${path}`);
  expect(response.ok(), `GET ${path} → ${response.status()}`).toBeTruthy();
  return (await response.json()) as T;
}

test.setTimeout(240_000);

test("starts a reconciliation, is blocked by the server's summary, then completes when balanced", async ({ page }) => {
  const suffix = uniqueSuffix();
  await signIn(page);

  const ledger = await post<{ id: string }>(page, "accounting/accounts", {
    code: `BR${suffix}`,
    name: `Banking recon ledger ${suffix}`,
    account_type: "asset",
  });
  const account = await post<{ id: string; name: string }>(page, "bank-accounts", {
    name: `Banking Recon ${suffix}`,
    account_id: ledger.id,
    kind: "bank",
  });
  const line = await post<{ id: string }>(page, `bank-accounts/${account.id}/transactions`, {
    transaction_date: "2026-09-10",
    amount: "1000.00",
    description: `Recon interest ${suffix}`,
  });

  // Start from the account's own shortcut.
  await page.goto(`/banking/reconciliation/new?bank_account=${account.id}`);
  await expectHeading(page, "Start reconciliation");
  // The preselected account shows once the form has hydrated and its lookup
  // loaded; filling before that can be lost to a re-render.
  await expect(page.getByRole("combobox", { name: /Bank account/ })).toHaveValue(account.name);
  await page.getByLabel("Statement start date").fill("2026-09-01");
  await page.getByLabel("Statement end date").fill("2026-09-30");
  await page.getByRole("textbox", { name: /Closing balance on the statement/ }).fill("1000.00");
  await expect(page.getByLabel("Statement start date")).toHaveValue("2026-09-01");
  await page.getByRole("button", { name: "Start reconciliation" }).click();
  await page.waitForURL(/\/banking\/reconciliation\/[0-9a-f-]{36}$/);
  await expectHeading(page, new RegExp(`Banking Recon ${suffix}`));
  const reconciliationId = page.url().split("/").pop() ?? "";

  // The line is open, so the server says it cannot complete — and the UI obeys.
  await expect(page.getByText("Not ready to complete:")).toBeVisible();
  await expect(page.getByText(/1 line is still unmatched or suggested/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Complete reconciliation" })).toBeDisabled();
  const blocked = await get<{ can_complete: boolean; open_transaction_count: number; difference: string }>(
    page,
    `bank-reconciliations/${reconciliationId}/summary`,
  );
  expect(blocked).toMatchObject({ can_complete: false, open_transaction_count: 1, difference: "1000.00" });

  // Explain the line (interest with no document behind it).
  const accounts = await get<{ results: Array<{ id: string; code: string }> }>(page, "accounting/accounts?page_size=200");
  const income = accounts.results.find((row) => row.code === "4000");
  expect(income, "4000 Sales exists in the dev tenant").toBeTruthy();
  await post(page, `bank-transactions/${line.id}/categorize`, { account_id: income?.id, description: "Interest" });

  await page.reload();
  await expect(page.getByText("Ready to complete: the period balances and every line in it is explained.")).toBeVisible();
  const complete = page.getByRole("button", { name: "Complete reconciliation" });
  await expect(complete).toBeEnabled();
  await complete.click();
  const dialog = page.getByRole("dialog", { name: "Complete this reconciliation?" });
  await expect(dialog.getByText(/The server reports a difference of/)).toBeVisible();
  await dialog.getByRole("button", { name: "Complete reconciliation" }).click();
  await expect(dialog).toBeHidden();

  await expect(page.getByRole("button", { name: "Reopen" })).toBeVisible();
  const completed = await get<{ status: string; cleared_balance: string | null }>(
    page,
    `bank-reconciliations/${reconciliationId}`,
  );
  expect(completed).toMatchObject({ status: "completed", cleared_balance: "1000.00" });

  // The line is now locked by the completed period.
  const locked = await get<{ reconciliation: string | null }>(page, `bank-transactions/${line.id}`);
  expect(locked.reconciliation).toBe(reconciliationId);
});
