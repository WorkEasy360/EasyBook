import { expect, test, type Page } from "@playwright/test";
import { expectHeading, signIn, uniqueSuffix } from "./helpers";

/**
 * Critical E2E — the matching workspace's no-posting paths and transfers:
 *  - two opposite lines on two of our accounts → confirm them as ONE transfer;
 *  - a recorded transfer → find suggestions → confirm the suggestion → unmatch;
 *  - void that transfer from the transfers list;
 *  - create and edit a bank rule.
 * Server state is read back after every step.
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

async function bankAccount(page: Page, label: string, suffix: string): Promise<{ id: string; name: string }> {
  const ledger = await post<{ id: string }>(page, "accounting/accounts", {
    code: `B${label}${suffix}`,
    name: `Banking match ledger ${label} ${suffix}`,
    account_type: "asset",
  });
  return post(page, "bank-accounts", { name: `Banking Match ${label} ${suffix}`, account_id: ledger.id, kind: "bank" });
}

test.setTimeout(300_000);

test("confirms a transfer pair, confirms and removes a suggestion, voids a transfer", async ({ page }) => {
  const suffix = uniqueSuffix();
  await signIn(page);

  const from = await bankAccount(page, "A", suffix);
  const to = await bankAccount(page, "B", suffix);

  // 1. Two opposite lines → one transfer.
  const outLine = await post<{ id: string }>(page, `bank-accounts/${from.id}/transactions`, {
    transaction_date: "2026-09-12",
    amount: "-750.00",
    description: `Own transfer out ${suffix}`,
  });
  const inLine = await post<{ id: string }>(page, `bank-accounts/${to.id}/transactions`, {
    transaction_date: "2026-09-13",
    amount: "750.00",
    description: `Own transfer in ${suffix}`,
  });

  await page.goto(`/banking/transactions/${outLine.id}`);
  await expectHeading(page, `Own transfer out ${suffix}`);
  const pairTable = page.getByRole("table", { name: /Possible transfer legs/ });
  // Candidates span every account in the organization, so other open lines of
  // the same amount can appear too; pick the one on this test's account.
  await expect(pairTable.getByRole("link", { name: to.name })).toBeVisible();
  await pairTable.getByRole("row", { name: new RegExp(to.name) }).getByRole("button", { name: "Confirm transfer" }).click();
  const pairDialog = page.getByRole("dialog", { name: "Record these two lines as one transfer?" });
  await expect(pairDialog.getByText(new RegExp(`from ${from.name} to ${to.name}`))).toBeVisible();
  await pairDialog.getByRole("button", { name: "Record transfer" }).click();
  await expect(pairDialog).toBeHidden();

  expect((await get<{ status: string }>(page, `bank-transactions/${outLine.id}`)).status).toBe("matched");
  expect((await get<{ status: string }>(page, `bank-transactions/${inLine.id}`)).status).toBe("matched");

  // 2. A transfer recorded in the books → suggestion → confirm → unmatch.
  const transfer = await post<{ id: string; transfer_number: string }>(page, "bank-transfers", {
    from_bank_account_id: from.id,
    to_bank_account_id: to.id,
    transfer_date: "2026-09-15",
    amount: "300.00",
    reference: `REF${suffix}`,
  });
  const line = await post<{ id: string }>(page, `bank-accounts/${from.id}/transactions`, {
    transaction_date: "2026-09-15",
    amount: "-300.00",
    description: `Sweep ${suffix} REF${suffix}`,
  });

  await page.goto(`/banking/transactions/${line.id}`);
  await expectHeading(page, `Sweep ${suffix} REF${suffix}`);
  await page.getByRole("button", { name: "Find suggestions" }).click();
  const findDialog = page.getByRole("dialog", { name: "Look for matching documents?" });
  await findDialog.getByRole("button", { name: "Find suggestions" }).click();
  await expect(findDialog).toBeHidden();

  const suggestions = page.getByRole("table", { name: /Suggested matches/ });
  await expect(suggestions.getByRole("link", { name: `Bank transfer ${transfer.transfer_number}` })).toBeVisible();
  await suggestions.getByRole("button", { name: "Confirm", exact: true }).click();
  const confirmDialog = page.getByRole("dialog", { name: "Confirm this match?" });
  await expect(confirmDialog.getByText(/Nothing is posted/)).toBeVisible();
  await confirmDialog.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(confirmDialog).toBeHidden();

  expect((await get<{ status: string }>(page, `bank-transactions/${line.id}`)).status).toBe("matched");
  await expect(page.getByRole("table", { name: /Confirmed matches/ }).getByRole("link", { name: `Bank transfer ${transfer.transfer_number}` })).toBeVisible();

  await page.getByRole("table", { name: /Confirmed matches/ }).getByRole("button", { name: "Unmatch" }).click();
  const unmatchDialog = page.getByRole("dialog", { name: "Remove this match?" });
  await unmatchDialog.getByRole("button", { name: "Unmatch" }).click();
  await expect(unmatchDialog).toBeHidden();
  expect((await get<{ status: string }>(page, `bank-transactions/${line.id}`)).status).toBe("unmatched");

  // 3. Void the (now unmatched) transfer from the list.
  await page.goto("/banking/transfers");
  await expectHeading(page, "Transfers");
  const row = page.getByRole("row", { name: new RegExp(transfer.transfer_number) });
  await row.getByRole("button", { name: "Void" }).click();
  const voidDialog = page.getByRole("dialog", { name: `Void ${transfer.transfer_number}?` });
  await voidDialog.getByLabel("Reason").fill("Recorded against the wrong accounts");
  await voidDialog.getByRole("button", { name: "Void" }).click();
  await expect(voidDialog).toBeHidden();

  const transfers = await get<{ results: Array<{ id: string; status: string; void_reason: string }> }>(page, "bank-transfers?page_size=200");
  expect(transfers.results.find((item) => item.id === transfer.id)).toMatchObject({
    status: "void",
    void_reason: "Recorded against the wrong accounts",
  });
});

test("creates and edits a bank rule", async ({ page }) => {
  const suffix = uniqueSuffix();
  const name = `Banking rule ${suffix}`;
  await signIn(page);

  await page.goto("/banking/rules/new");
  await expectHeading(page, "New bank rule");
  await page.getByRole("textbox", { name: "Name (required)" }).fill(name);

  // No condition yet: refused before it reaches the server.
  await page.getByRole("button", { name: "Create rule" }).click();
  await expect(page.getByText("Add at least one condition — a rule without one would match every line.")).toBeVisible();

  await page.getByLabel("Description or reference contains").fill(`artefact-${suffix}`);
  await page.getByLabel("Action").selectOption("exclude");
  await page.getByRole("button", { name: "Create rule" }).click();
  await page.waitForURL(/\/banking\/rules$/);
  await expect(page.getByRole("row", { name: new RegExp(name) })).toBeVisible();

  const rules = await get<{ results: Array<{ id: string; name: string; action: string; priority: number }> }>(page, "bank-rules?page_size=200");
  const created = rules.results.find((rule) => rule.name === name);
  expect(created).toMatchObject({ action: "exclude", priority: 100 });

  await page.getByRole("row", { name: new RegExp(name) }).getByRole("link").first().click();
  await expectHeading(page, `Edit ${name}`);
  await page.getByRole("textbox", { name: "Priority (required)" }).fill("7");
  await page.getByRole("button", { name: "Save rule" }).click();
  await page.waitForURL(/\/banking\/rules$/);

  const edited = await get<{ priority: number; is_active: boolean }>(page, `bank-rules/${created?.id}`);
  expect(edited).toMatchObject({ priority: 7, is_active: true });
});
