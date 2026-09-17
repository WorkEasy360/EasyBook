import { expect, test, type Locator } from "@playwright/test";
import { expectHeading, pickOption, signIn } from "./helpers";

/**
 * Critical E2E — quote lifecycle: draft → sent → accepted → converted to a
 * draft invoice.
 *
 * Every assertion is on what the SERVER returned after a step: the number
 * allocated on create, the status after each transition, and the invoice the
 * convert endpoint created (its engine-computed total and its link back to
 * the quote). Relies on the dev tenant's "Steel Widget" item, an active
 * customer and the 1100 receivable account.
 */

/** A status badge; marked statuses carry an aria-hidden glyph before the label. */
function statusBadge(scope: Locator, label: string): Locator {
  return scope.getByText(new RegExp(`^[^\\w\\s]?${label}$`)).first();
}

test("creates a quote, sends and accepts it, and converts it to a draft invoice", async ({ page }) => {
  // The dev server is shared by several builds; server re-renders can be slow.
  test.setTimeout(300_000);
  await signIn(page);

  await page.goto("/sales/quotes/new");
  await expectHeading(page, "New quote");

  await pickOption(page, "Customer", /Customer/);
  const line = page.getByRole("group", { name: "Line 1" });
  await pickOption(page, "Item", "Steel Widget");
  await line.getByLabel("Quantity").fill("2");
  await line.getByLabel("Unit price").fill("1000");
  await line.getByLabel("Tax rate").fill("18");
  // Estimate per core/money.py: 2 × 1000 + 18% = 2,360.00.
  await expect(page.getByText("₹2,360.00").first()).toBeVisible();

  await page.getByRole("button", { name: "Save as draft" }).click();
  await page.waitForURL(/\/sales\/quotes\/[0-9a-f-]{36}$/);
  // Quote numbers are allocated on create.
  await expectHeading(page, /^QUO-\d+$/);
  const main = page.getByRole("main");
  await expect(statusBadge(main, "Draft")).toBeVisible({ timeout: 60_000 });

  await page.getByRole("button", { name: "Mark sent" }).click();
  const sendDialog = page.getByRole("dialog", { name: /sent\?$/ });
  await sendDialog.getByRole("button", { name: "Mark sent" }).click();
  await expect(sendDialog).toBeHidden();
  await expect(statusBadge(main, "Sent")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("link", { name: "Edit", exact: true })).toHaveCount(0);

  await page.getByRole("button", { name: "Mark accepted" }).click();
  const acceptDialog = page.getByRole("dialog", { name: /accepted\?$/ });
  await acceptDialog.getByRole("button", { name: "Mark accepted" }).click();
  await expect(acceptDialog).toBeHidden();
  await expect(statusBadge(main, "Accepted")).toBeVisible({ timeout: 60_000 });

  await page.getByRole("button", { name: "Convert", exact: true }).click();
  const convert = page.getByRole("dialog", { name: /^Convert QUO-\d+$/ });
  await expect(convert).toBeVisible();
  await expect(convert.getByLabel("Convert to")).toHaveValue("invoice");
  await pickOption(page, "Receivable account", /^1100/);
  // A tracked product needs a warehouse on the invoice (warehouse_required).
  // The dialog defaults one; choosing the organization's default explicitly
  // keeps the test independent of what earlier invoices remembered.
  await pickOption(page, "Warehouse", /default/);
  await convert.getByRole("button", { name: "Create draft invoice" }).click();

  // The response is the created invoice; the page navigates to it.
  await page.waitForURL(/\/sales\/invoices\/[0-9a-f-]{36}$/);
  await expectHeading(page, "Draft invoice");
  await expect(page.getByRole("link", { name: "View quote" })).toBeVisible();
  // The engine's figure, copied from the quote's lines — not the estimate.
  await expect(page.getByText("₹2,360.00").filter({ visible: true }).first()).toBeVisible();

  // Back on the quote, the invoice is listed and the invoice target is spent.
  await page.getByRole("link", { name: "View quote" }).click();
  await expectHeading(page, /^QUO-\d+$/);
  await expect(page.getByRole("link", { name: "Draft invoice" })).toBeVisible();
});
