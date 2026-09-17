import { randomUUID } from "node:crypto";
import { expect, test, type APIRequestContext } from "@playwright/test";
import { expectHeading, pickOption, signIn } from "./helpers";

/**
 * Critical E2E — record a customer payment against a posted invoice.
 *
 * The invoice is created and posted for THIS run through the same BFF the UI
 * uses (a signed-in session's cookies), so the test never depends on some
 * shared invoice staying unpaid. The payment itself goes through the UI, from
 * the invoice page's "Record payment", and the assertion is the invoice's
 * balance due and status as the SERVER now reports them.
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

interface Page<T> {
  results: T[];
}

test("records a payment on a posted invoice and the invoice's balance due drops", async ({ page }) => {
  // The dev server is shared by several builds; server re-renders can be slow.
  test.setTimeout(300_000);
  await signIn(page);

  const [customers, items, accounts] = await Promise.all([
    bff<Page<{ id: string; display_name: string }>>(page.request, "GET", "sales/customers/?is_active=true&page_size=200"),
    bff<Page<{ id: string; sku: string }>>(page.request, "GET", "items/?is_active=true&page_size=200"),
    bff<Page<{ id: string; code: string }>>(page.request, "GET", "accounting/accounts/?account_type=asset&page_size=200"),
  ]);
  const customer = customers.results.find((row) => row.display_name.includes("Customer"));
  const service = items.results.find((row) => row.sku === "SVC-CONS");
  const receivable = accounts.results.find((row) => row.code === "1100");
  expect(customer && service && receivable, "dev tenant fixtures (customer, SVC-CONS, 1100)").toBeTruthy();

  // A service line with no tax: posts without stock or a tax account.
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());
  const draft = await bff<{ id: string }>(page.request, "POST", "sales/invoices/", {
    customer_id: customer!.id,
    invoice_date: today,
    due_date: today,
    receivable_account_id: receivable!.id,
    reference: "sales-payments e2e",
    lines: [{ item_id: service!.id, quantity: "1", unit_price: "1000", tax_rate: "0" }],
  });
  const posted = await bff<{ invoice_number: string; amount_due: string; status: string }>(
    page.request,
    "POST",
    `sales/invoices/${draft.id}/post/`,
    {},
  );
  expect(posted.status).toBe("sent");
  expect(posted.amount_due).toBe("1000.00");

  await page.goto(`/sales/invoices/${draft.id}`);
  await expectHeading(page, posted.invoice_number);
  await page.getByRole("link", { name: "Record payment" }).click();

  await page.waitForURL(/\/sales\/payments\/new\?customer=.+&invoice=.+/);
  await expectHeading(page, "Record payment");
  // Prefilled from the invoice's server-reported balance due.
  const apply = page.getByLabel(`Apply to ${posted.invoice_number}`);
  await expect(apply).toHaveValue(/^1,?000(\.00)?$/, { timeout: 60_000 });

  // Pay part of it.
  await page.getByLabel("Amount received").fill("400");
  await apply.fill("400");
  await pickOption(page, "Received into", /^1000/);
  await expect(page.getByText("Unapplied credit (estimate)")).toBeVisible();

  await page.getByRole("button", { name: "Record payment", exact: true }).click();
  await page.waitForURL(/\/sales\/payments\/[0-9a-f-]{36}$/, { timeout: 60_000 });
  await expectHeading(page, /^PAY-\d+$/);
  await expect(page.getByRole("link", { name: posted.invoice_number })).toBeVisible();

  // The invoice now reports what the backend derived from the allocation.
  await page.goto(`/sales/invoices/${draft.id}`);
  await expectHeading(page, posted.invoice_number);
  await expect(page.getByText(/^[^\w\s]?Partially Paid$/).first()).toBeVisible({ timeout: 60_000 });
  const balance = page.getByText("Balance due", { exact: true }).first().locator("..");
  await expect(balance).toContainText("₹600.00");
});
