import { randomUUID } from "node:crypto";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { expectHeading, signIn } from "./helpers";

/**
 * Critical E2E — credit a posted invoice for a return: credit note prefilled
 * from the invoice → restock the line at a unit cost → issue → the invoice's
 * balance due, as the backend derives it, is cleared.
 *
 * The invoice is created and posted for this run through the BFF: one
 * "Steel Widget" (WID-001) at 500 with no tax, issued from the default
 * warehouse. The restock puts that unit back.
 */

const BASE = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3000";

async function bff<T>(request: APIRequestContext, method: "GET" | "POST", path: string, data?: unknown): Promise<T> {
  const response = await request.fetch(`/api/bff/${path}`, {
    method,
    headers: { Origin: BASE, "Idempotency-Key": randomUUID() },
    ...(data === undefined ? {} : { data }),
  });
  expect(response.ok(), `${method} ${path} → ${response.status()} ${await response.text()}`).toBe(true);
  return (await response.json()) as T;
}

function statusBadge(page: Page, label: string) {
  // Marked statuses carry an aria-hidden glyph before the label.
  return page.getByRole("main").getByText(new RegExp(`^[^\\w\\s]?${label}$`)).first();
}

test("credits a posted invoice with a restocked return and clears its balance", async ({ page }) => {
  // The dev server is shared by several builds; server re-renders can be slow.
  test.setTimeout(300_000);
  await signIn(page);

  const [customers, items, accounts, warehouses] = await Promise.all([
    bff<{ results: Array<{ id: string; display_name: string }> }>(page.request, "GET", "sales/customers/?is_active=true&page_size=200"),
    bff<{ results: Array<{ id: string; sku: string }> }>(page.request, "GET", "items/?is_active=true&page_size=200"),
    bff<{ results: Array<{ id: string; code: string }> }>(page.request, "GET", "accounting/accounts/?account_type=asset&page_size=200"),
    bff<{ results: Array<{ id: string; is_default: boolean }> }>(page.request, "GET", "inventory/warehouses/?page_size=200"),
  ]);
  const customer = customers.results.find((row) => row.display_name.includes("Customer"));
  const widget = items.results.find((row) => row.sku === "WID-001");
  const receivable = accounts.results.find((row) => row.code === "1100");
  const warehouse = warehouses.results.find((row) => row.is_default);
  expect(customer && widget && receivable && warehouse, "dev tenant fixtures").toBeTruthy();

  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());
  const draft = await bff<{ id: string }>(page.request, "POST", "sales/invoices/", {
    customer_id: customer!.id,
    invoice_date: today,
    due_date: today,
    receivable_account_id: receivable!.id,
    warehouse_id: warehouse!.id,
    reference: "sales-credit-notes e2e",
    lines: [{ item_id: widget!.id, quantity: "1", unit_price: "500", tax_rate: "0" }],
  });
  const posted = await bff<{ invoice_number: string; amount_due: string }>(
    page.request,
    "POST",
    `sales/invoices/${draft.id}/post/`,
    {},
  );
  expect(posted.amount_due).toBe("500.00");

  await page.goto(`/sales/invoices/${draft.id}`);
  await expectHeading(page, posted.invoice_number);
  await page.getByRole("link", { name: "Credit note", exact: true }).click();

  await page.waitForURL(/\/sales\/credit-notes\/new\?invoice=/);
  await expectHeading(page, "New credit note");
  const line = page.getByRole("group", { name: "Line 1" });
  await expect(line.getByLabel("Quantity")).toHaveValue(/^1(\.0+)?$/);

  // Restock is offered once the item is known to track inventory.
  const restock = line.getByLabel("Return to stock");
  await expect(restock).toBeEnabled({ timeout: 60_000 });
  await restock.check();
  await line.getByLabel("Unit cost").fill("100");

  await page.getByRole("button", { name: "Save as draft" }).click();
  await page.waitForURL(/\/sales\/credit-notes\/[0-9a-f-]{36}$/, { timeout: 60_000 });
  await expectHeading(page, "Draft credit note");
  await expect(page.getByRole("main").getByRole("cell", { name: /^Yes/ })).toBeVisible();

  await page.getByRole("button", { name: "Issue credit note" }).click();
  const dialog = page.getByRole("dialog", { name: "Issue this credit note?" });
  await expect(dialog).toContainText("restocked line returns");
  await dialog.getByRole("button", { name: "Issue credit note" }).click();
  await expect(dialog).toBeHidden({ timeout: 60_000 });

  // Numbered on issue.
  await expectHeading(page, /^CN-\d+$/);
  await expect(statusBadge(page, "Issued")).toBeVisible({ timeout: 60_000 });

  await page.goto(`/sales/invoices/${draft.id}`);
  await expectHeading(page, posted.invoice_number);
  await expect(statusBadge(page, "Paid")).toBeVisible({ timeout: 60_000 });
  const balance = page.getByText("Balance due", { exact: true }).first().locator("..");
  await expect(balance).toContainText("₹0.00");
});
