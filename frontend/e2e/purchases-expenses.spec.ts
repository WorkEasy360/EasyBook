import { expect, test } from "@playwright/test";
import { expectHeading, pickOption, signIn, uniqueSuffix } from "./helpers";

/**
 * Critical E2E — direct expense: draft → post.
 *
 * The draft page must show the tax and total the SERVER computed from amount
 * and rate (services/expenses.py :: _compute_totals), and posting must assign
 * the number and flip the status. Accounts are chosen explicitly: 5200 Office
 * Expenses, paid through 1000 Bank - HDFC, input tax to 1300 GST Input Credit
 * (a taxed expense cannot post without it).
 */

test("creates a draft expense, posts it, and shows the server's figures", async ({ page }) => {
  test.setTimeout(90_000);
  await signIn(page);

  const description = `PUR E2E expense ${uniqueSuffix()}`;

  await page.goto("/purchases/expenses/new");
  await expectHeading(page, "New expense");
  await page.getByLabel("Description").fill(description);
  await page.getByLabel("Amount").fill("250");
  await page.getByLabel("Tax rate").fill("18");
  await pickOption(page, "Expense account", "Office Expenses");
  await pickOption(page, "Paid through", "Bank - HDFC");
  await pickOption(page, "Tax recoverable account", "GST Input Credit");

  // Labelled estimate while typing: 250 + 18% = 295.00.
  await expect(page.getByText("Total (estimate)")).toBeVisible();
  await expect(page.getByText("₹295.00").first()).toBeVisible();

  await page.getByRole("button", { name: "Save as draft" }).click();
  await page.waitForURL(/\/purchases\/expenses\/[0-9a-f-]{36}$/);
  await expectHeading(page, "Draft expense");
  const main = page.getByRole("main");
  // The status badge beside the h1.
  await expect(page.getByRole("heading", { level: 1 }).locator("..")).toContainText("Draft");
  // Server-computed on save.
  await expect(main.getByText("₹45.00").filter({ visible: true }).first()).toBeVisible();
  await expect(main.getByText("₹295.00").filter({ visible: true }).first()).toBeVisible();

  await page.getByRole("button", { name: "Post expense" }).click();
  const dialog = page.getByRole("dialog", { name: "Post this expense?" });
  await expect(dialog).toContainText("Bank - HDFC");
  await dialog.getByRole("button", { name: "Post expense" }).click();

  await expectHeading(page, /^EXP-\d+$/);
  await expect(page.getByRole("heading", { level: 1 }).locator("..")).toContainText("Posted");
  await expect(page.getByRole("link", { name: "View journal" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Post expense" })).toHaveCount(0);

  // It is in the posted list.
  await page.goto("/purchases/expenses?status=posted");
  await expect(page.getByRole("link", { name: /^EXP-\d+$/ }).first()).toBeVisible();
  await expect(page.getByText(description)).toBeVisible();
});
