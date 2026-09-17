import { expect, test, type Page } from "@playwright/test";
import { expectHeading, pickOption, signIn, uniqueSuffix } from "./helpers";

/** The status badge beside the page's h1 — not a status elsewhere on the page. */
function headerStatus(page: Page) {
  return page.getByRole("heading", { level: 1 }).locator("..");
}

/**
 * Critical E2E — payables: bill draft → post → vendor payment → balance due.
 *
 * Every assertion after a write reads what the SERVER returned (the bill
 * number assigned on posting, its status, and `amount_due` derived from the
 * payment allocations), never the form's estimate.
 *
 * A fresh vendor is created through the UI so the payment screen lists only
 * this run's bill. The line uses the "Consulting" service item — no stock and
 * no warehouse — which has no purchase account, so the line names the 5200
 * Office Expenses account itself (item_missing_purchase_account otherwise).
 * Tax at 18% needs the 1300 GST Input Credit account to post.
 */

test("posts a bill, records a part payment, and shows the server's balance due", async ({ page }) => {
  test.setTimeout(120_000);
  await signIn(page);

  const suffix = uniqueSuffix();
  const vendorName = `PUR E2E Vendor ${suffix}`;

  // --- vendor, with a default payable account the bill form picks up -------
  await page.goto("/purchases/vendors/new");
  await expectHeading(page, "New vendor");
  await page.getByLabel("Display name").fill(vendorName);
  await page.getByLabel("Vendor code").fill(`PURE2E${suffix}`);
  await pickOption(page, "Default payable account", "Accounts Payable");
  await page.getByRole("button", { name: "Create vendor" }).click();
  await page.waitForURL(/\/purchases\/vendors\/[0-9a-f-]{36}$/);
  await expectHeading(page, vendorName);

  // --- draft bill -----------------------------------------------------------
  await page.getByRole("main").getByRole("link", { name: "New bill", exact: true }).click();
  await page.waitForURL(/\/purchases\/bills\/new\?vendor=/);
  await expectHeading(page, "New bill");

  await pickOption(page, "Tax recoverable account", "GST Input Credit");
  const line = page.getByRole("group", { name: "Line 1" });
  await pickOption(page, "Item", "Consulting");
  await line.getByLabel("Quantity").fill("2");
  await line.getByLabel("Unit price").fill("500");
  await line.getByLabel("Tax rate").fill("18");
  await pickOption(page, "Expense account", "Office Expenses");

  // Estimate per core/money.py: 2 × 500 + 18% = 1,180.00.
  await expect(page.getByText("₹1,180.00").first()).toBeVisible();

  await page.getByRole("button", { name: "Save as draft" }).click();
  await page.waitForURL(/\/purchases\/bills\/[0-9a-f-]{36}$/);
  await expectHeading(page, "Draft bill");
  const billUrl = page.url();

  // --- post -----------------------------------------------------------------
  await page.getByRole("button", { name: "Post bill" }).click();
  const postDialog = page.getByRole("dialog", { name: "Post this bill?" });
  await expect(postDialog).toBeVisible();
  await postDialog.getByRole("button", { name: "Post bill" }).click();

  await expectHeading(page, /^BILL-\d+$/);
  const billNumber = (await page.getByRole("heading", { level: 1 }).textContent())?.trim() ?? "";
  await expect(headerStatus(page)).toContainText("Open");
  await expect(page.getByRole("button", { name: "Post bill" })).toHaveCount(0);

  // --- part payment -----------------------------------------------------------
  await page.getByRole("link", { name: "Record payment" }).click();
  await page.waitForURL(/\/purchases\/payments\/new\?vendor=.+&bill=/);
  await expectHeading(page, "Record vendor payment");

  // The bill arrives pre-applied at its full balance; pay part of it instead.
  const apply = page.getByLabel(`Apply to ${billNumber}`);
  await expect(apply).toHaveValue("1180.00");
  await page.getByLabel("Amount paid").fill("500");
  await apply.fill("500");
  await pickOption(page, "Paid from", "Bank - HDFC");
  await page.getByRole("button", { name: "Record payment" }).click();

  await page.waitForURL(/\/purchases\/payments\/[0-9a-f-]{36}$/);
  await expectHeading(page, /^VPAY-\d+$/);
  await expect(page.getByRole("link", { name: billNumber })).toBeVisible();

  // --- the bill now reflects the payment, as derived by the server ----------
  await page.goto(billUrl);
  await expectHeading(page, billNumber);
  await expect(headerStatus(page)).toContainText("Partially Paid");
  // 1,180.00 − 500.00, as the serializer derives it from the allocation.
  await expect(page.getByRole("main").getByText("₹680.00").filter({ visible: true }).first()).toBeVisible();
  await expect(page.getByRole("table", { name: `Payments applied to ${billNumber}` })).toContainText("₹500.00");
});
