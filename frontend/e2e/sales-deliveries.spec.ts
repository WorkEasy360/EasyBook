import { randomUUID } from "node:crypto";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { expectHeading, signIn } from "./helpers";

/**
 * Critical E2E — deliver a confirmed sales order: challan prefilled from the
 * order → draft → dispatch (stock issued) → the order's fulfilment status,
 * which only the backend derives, turns "Fulfilled".
 *
 * The order is created and confirmed for this run through the BFF. It orders
 * ONE "Steel Widget" (WID-001), so each run issues one unit from the default
 * warehouse.
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

test("delivers a confirmed order: prefilled challan, dispatch, order fulfilled", async ({ page }) => {
  // The dev server is shared by several builds; server re-renders can be slow.
  test.setTimeout(300_000);
  await signIn(page);

  const [customers, items] = await Promise.all([
    bff<{ results: Array<{ id: string; display_name: string }> }>(page.request, "GET", "sales/customers/?is_active=true&page_size=200"),
    bff<{ results: Array<{ id: string; sku: string }> }>(page.request, "GET", "items/?is_active=true&page_size=200"),
  ]);
  const customer = customers.results.find((row) => row.display_name.includes("Customer"));
  const widget = items.results.find((row) => row.sku === "WID-001");
  expect(customer && widget, "dev tenant fixtures (customer, WID-001)").toBeTruthy();

  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());
  const order = await bff<{ id: string; order_number: string }>(page.request, "POST", "sales/orders/", {
    customer_id: customer!.id,
    order_date: today,
    notes: "sales-deliveries e2e",
    lines: [{ item_id: widget!.id, quantity: "1", unit_price: "500", tax_rate: "0" }],
  });
  await bff(page.request, "POST", `sales/orders/${order.id}/confirm/`, {});

  await page.goto(`/sales/orders/${order.id}`);
  await expectHeading(page, order.order_number);
  await expect(statusBadge(page, "Confirmed")).toBeVisible({ timeout: 60_000 });
  await page.getByRole("link", { name: "Create delivery challan" }).first().click();

  await page.waitForURL(/\/sales\/deliveries\/new\?order=/);
  await expectHeading(page, "New delivery challan");
  const line = page.getByRole("group", { name: "Line 1" });
  // Nothing dispatched yet, so the whole ordered quantity is left.
  await expect(line.getByLabel("Quantity")).toHaveValue(/^1(\.0+)?$/);

  await page.getByRole("button", { name: "Save as draft" }).click();
  await page.waitForURL(/\/sales\/deliveries\/[0-9a-f-]{36}$/, { timeout: 60_000 });
  await expectHeading(page, /^DC-\d+$/);
  await expect(statusBadge(page, "Draft")).toBeVisible({ timeout: 60_000 });

  await page.getByRole("button", { name: "Dispatch", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: /^Dispatch DC-\d+\?$/ });
  await expect(dialog).toContainText("stock ledger");
  await dialog.getByRole("button", { name: "Dispatch", exact: true }).click();
  await expect(dialog).toBeHidden({ timeout: 60_000 });

  await expect(statusBadge(page, "Dispatched")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("link", { name: "Create invoice" }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Dispatch", exact: true })).toHaveCount(0);

  // The order's status is derived by the backend from dispatched quantities.
  await page.goto(`/sales/orders/${order.id}`);
  await expectHeading(page, order.order_number);
  await expect(statusBadge(page, "Fulfilled")).toBeVisible({ timeout: 60_000 });
});
