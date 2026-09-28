import { expect, test } from "@playwright/test";
import { signIn } from "./helpers";

/**
 * Critical E2E — the sales master-data vertical (spec §92 first leg).
 * Asserts server-authoritative state after each write, never local state.
 */

test("creates a customer, reads it back, and edits it", async ({ page }) => {
  await signIn(page);

  const unique = Date.now().toString().slice(-8);
  const code = `E2E-${unique}`;
  const name = `E2E Customer ${unique}`;

  await page.goto("/sales/customers");
  await expect(page.getByRole("heading", { level: 1, name: "Customers" })).toBeVisible();

  await page.getByRole("link", { name: "New customer" }).first().click();
  await page.waitForURL("**/sales/customers/new");

  await page.getByLabel("Display name").fill(name);
  await page.getByLabel("Customer code").fill(code);
  await page.getByLabel("Email").fill(`e2e-${unique}@example.com`);
  await page.getByLabel("Payment terms (days)").fill("45");
  await page.getByRole("button", { name: "Create customer" }).click();

  // Landed on the detail page, showing what the SERVER stored.
  await page.waitForURL(/\/sales\/customers\/[0-9a-f-]{36}$/);
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
  await expect(page.getByText("Net 45 days")).toBeVisible();

  // And it is in the list.
  await page.goto("/sales/customers");
  await expect(page.getByRole("link", { name })).toBeVisible();

  // Edit round-trip. exact:true matters — getByRole's `name` is a
  // case-insensitive SUBSTRING match by default, so a bare "Edit" also
  // matches the "Cr-edit- Notes" link in the sidebar.
  await page.getByRole("link", { name }).click();
  await page.getByRole("main").getByRole("link", { name: "Edit", exact: true }).click();
  await page.waitForURL(/\/edit$/);
  await page.getByLabel("Phone").fill("+91 98765 43210");
  await page.getByRole("button", { name: "Save changes" }).click();

  await page.waitForURL(/\/sales\/customers\/[0-9a-f-]{36}$/);
  await expect(page.getByText("+91 98765 43210")).toBeVisible();
});

test("reports the server's validation error rather than inventing one", async ({ page }) => {
  await signIn(page);
  await page.goto("/sales/customers/new");

  // Submitting empty must be caught before a request is made.
  await page.getByRole("button", { name: "Create customer" }).click();
  await expect(page.getByText("A display name is required.")).toBeVisible();
  await expect(page.getByText("A customer code is required.")).toBeVisible();
});

test("filter chips are links that change the URL", async ({ page }) => {
  await signIn(page);
  await page.goto("/sales/customers");

  await page.getByRole("link", { name: "Inactive", exact: true }).click();
  await expect(page).toHaveURL(/is_active=false/);

  await page.getByRole("link", { name: "Clear filters" }).click();
  await expect(page).toHaveURL(/\/sales\/customers$/);
});
