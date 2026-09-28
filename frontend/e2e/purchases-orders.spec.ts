import { expect, test, type Page } from "@playwright/test";
import { expectHeading, pickOption, signIn } from "./helpers";

/** The status badge beside the page's h1 — not a status elsewhere on the page. */
function headerStatus(page: Page) {
  return page.getByRole("heading", { level: 1 }).locator("..");
}

/**
 * Critical E2E — procure to stock: purchase order → approve → goods receipt
 * → receive → the order's status moves.
 *
 * The final assertion is the point: the order becomes "Received" only
 * because the backend's receive_goods recomputed it from the receipt lines
 * linked through source_order_line_id (refresh_order_receipt_status). The UI
 * never sets it.
 *
 * Uses the dev tenant's "Steel Widget" (a stock-tracked product — only those
 * can be received) and "Main Warehouse"; the vendor is the purchases test
 * vendor seeded for this module.
 */

test("approves a purchase order, receives it in full, and the order shows received", async ({ page }) => {
  test.setTimeout(120_000);
  await signIn(page);

  // --- draft order ------------------------------------------------------------
  await page.goto("/purchases/orders/new");
  await expectHeading(page, "New purchase order");
  await pickOption(page, "Vendor", "Purchases Test Vendor");
  await pickOption(page, "Deliver to warehouse", "Main Warehouse");
  const line = page.getByRole("group", { name: "Line 1" });
  await pickOption(page, "Item", "Steel Widget");
  await line.getByLabel("Quantity").fill("3");
  await line.getByLabel("Unit price").fill("100");

  await page.getByRole("button", { name: "Save as draft" }).click();
  await page.waitForURL(/\/purchases\/orders\/[0-9a-f-]{36}$/);
  await expectHeading(page, /^PO-\d+$/);
  const orderNumber = (await page.getByRole("heading", { level: 1 }).textContent())?.trim() ?? "";
  const orderUrl = page.url();
  await expect(headerStatus(page)).toContainText("Draft");

  // --- approve ------------------------------------------------------------------
  await page.getByRole("button", { name: "Approve order" }).click();
  const approve = page.getByRole("dialog", { name: `Approve ${orderNumber}?` });
  await approve.getByRole("button", { name: "Approve order" }).click();
  await expect(headerStatus(page)).toContainText("Approved");

  // --- goods receipt prefilled from the order ---------------------------------
  await page.getByRole("link", { name: "Receive goods" }).click();
  await page.waitForURL(/\/purchases\/goods-receipts\/new\?order=/);
  await expectHeading(page, "New goods receipt");
  // Outstanding quantity comes from the match endpoint's received/billed figures.
  await expect(page.getByRole("group", { name: "Line 1" }).getByLabel("Quantity")).toHaveValue("3");
  await page.getByLabel("Vendor delivery note").fill(`DN-${orderNumber}`);
  await page.getByRole("button", { name: "Save as draft" }).click();

  await page.waitForURL(/\/purchases\/goods-receipts\/[0-9a-f-]{36}$/);
  await expectHeading(page, /^GR-\d+$/);
  const receiptNumber = (await page.getByRole("heading", { level: 1 }).textContent())?.trim() ?? "";

  // --- receive: moves stock and recomputes the order's status -----------------
  await page.getByRole("button", { name: "Receive goods" }).click();
  const receive = page.getByRole("dialog", { name: `Receive ${receiptNumber}?` });
  await expect(receive).toContainText("Main Warehouse");
  await receive.getByRole("button", { name: "Receive goods" }).click();
  await expect(headerStatus(page)).toContainText("Received");
  await expect(page.getByRole("button", { name: "Receive goods" })).toHaveCount(0);

  // --- the order, as the server now reports it -------------------------------
  await page.goto(orderUrl);
  await expectHeading(page, orderNumber);
  await expect(headerStatus(page)).toContainText("Received");
  // Fully received, not "Partially Received": all 3 of 3 arrived.
  await expect(headerStatus(page)).not.toContainText("Partially");
  await expect(page.getByRole("table", { name: `Goods receipts for ${orderNumber}` })).toContainText(receiptNumber);
  // Nothing is left to receive, so the shortcut is gone.
  await expect(page.getByRole("link", { name: "Receive goods" })).toHaveCount(0);
});
