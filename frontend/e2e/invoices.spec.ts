import { expect, test } from "@playwright/test";
import { expectHeading, pickOption, signIn } from "./helpers";

/**
 * Critical E2E — invoice lifecycle: draft → post.
 *
 * Asserts what the SERVER returns after each step (number assigned on
 * posting, status, engine-computed total), never what the form estimated.
 * Relies on the dev tenant having an "E2E Customer", the "Steel Widget" item,
 * "Main Warehouse" and the 1100/2200 accounts. Everything the post depends on
 * is chosen explicitly — never inherited from whichever invoice was last saved.
 */

test("creates a draft invoice, posts it, and shows the engine's figures", async ({ page }) => {
  await signIn(page);

  await page.goto("/sales/invoices/new");
  await expectHeading(page, "New invoice");

  await pickOption(page, "Customer", /E2E Customer/);
  // Explicit, not remembered: other runs create invoices too, and a tracked
  // product line cannot post without a warehouse to issue stock from.
  await pickOption(page, "Warehouse", "Main Warehouse");
  await pickOption(page, "Receivable account", "Accounts Receivable");
  // Taxed lines cannot post without an output-tax account.
  await pickOption(page, "Tax payable account", "GST Payable");
  await pickOption(page, "Item", "Steel Widget");
  const quantity = page.getByRole("group", { name: "Line 1" }).getByLabel("Quantity");
  await quantity.fill("2");
  const price = page.getByRole("group", { name: "Line 1" }).getByLabel("Unit price");
  await price.fill("1000");
  await page.getByRole("group", { name: "Line 1" }).getByLabel("Tax rate").fill("18");

  // The estimate follows core/money.py: 2 × 1000 + 18% = 2,360.00.
  await expect(page.getByText("₹2,360.00").first()).toBeVisible();

  await page.getByRole("button", { name: "Save as draft" }).click();
  await page.waitForURL(/\/sales\/invoices\/[0-9a-f-]{36}$/);
  await expectHeading(page, "Draft invoice");

  await page.getByRole("button", { name: "Post invoice" }).click();
  const dialog = page.getByRole("dialog", { name: "Post this invoice?" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Post invoice" }).click();

  // Posting assigns the number and flips the status — both from the server.
  await expectHeading(page, /^INV-\d+$/);
  await expect(page.getByRole("main").getByText("Sent", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Balance due").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Post invoice" })).toHaveCount(0);
});
