import { expect, test } from "@playwright/test";

/**
 * Critical E2E — authentication (spec §91).
 *
 * Runs against a local stack, never production (spec §90). It needs a seeded
 * user; supply credentials via E2E_EMAIL / E2E_PASSWORD, and a backend at
 * API_BASE_URL. Selectors are roles and accessible names, not CSS (spec §101)
 * — which also means a failure here is usually a real accessibility defect.
 */

const EMAIL = process.env.E2E_EMAIL ?? "fe-test@easybook.local";
const PASSWORD = process.env.E2E_PASSWORD ?? "FrontendTest123!";

test.describe("authentication", () => {
  test("signs in, lands on the dashboard, and signs out", async ({ page }) => {
    await page.goto("/login");

    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();

    await page.getByLabel("Email address").fill(EMAIL);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();

    await page.waitForURL("**/dashboard");
    await expect(page.getByRole("heading", { level: 1, name: "Dashboard" })).toBeVisible();

    // Main navigation is present and reflects a real role.
    await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();

    await page.getByRole("button", { name: /Account menu/ }).click();
    await page.getByRole("button", { name: "Sign out" }).click();

    await page.waitForURL("**/login**");
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  });

  test("rejects bad credentials without revealing whether the account exists", async ({ page }) => {
    await page.goto("/login");

    await page.getByLabel("Email address").fill("definitely-not-a-user@easybook.local");
    await page.getByLabel("Password").fill("wrong-password-entirely");
    await page.getByRole("button", { name: "Sign in" }).click();

    // Scoped to the form: Next's own route announcer is also role="alert",
    // so an unscoped query is ambiguous under strict mode.
    const alert = page.locator("form").getByRole("alert");
    await expect(alert).toBeVisible();
    // One fixed message for both "no such account" and "wrong password".
    await expect(alert).toContainText("do not match an account");
    await expect(page).toHaveURL(/\/login/);
  });

  test("sends an unauthenticated visitor to sign in, then back where they meant to go", async ({
    page,
  }) => {
    await page.goto("/sales/invoices");
    await page.waitForURL("**/login**");
    expect(new URL(page.url()).searchParams.get("next")).toBe("/sales/invoices");
  });

  test("keeps the session token out of the page entirely", async ({ page, context }) => {
    await page.goto("/login");
    await page.getByLabel("Email address").fill(EMAIL);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.waitForURL("**/dashboard");

    // The session cookies exist and are httpOnly...
    const cookies = await context.cookies();
    const access = cookies.find((cookie) => cookie.name === "eb_at");
    const refresh = cookies.find((cookie) => cookie.name === "eb_rt");
    expect(access?.httpOnly).toBe(true);
    expect(refresh?.httpOnly).toBe(true);

    // ...so document.cookie cannot see them, and no token is in the markup.
    const visibleCookies = await page.evaluate(() => document.cookie);
    expect(visibleCookies).not.toContain("eb_at");
    expect(visibleCookies).not.toContain("eb_rt");

    const html = await page.content();
    expect(html).not.toContain("eyJhbGciOiJIUzI1NiI");
  });

  test("a dead session sends the user back to sign in", async ({ page, context }) => {
    await context.addCookies([
      { name: "eb_at", value: "dead.token.x", domain: "127.0.0.1", path: "/" },
      { name: "eb_rt", value: "dead.refresh.y", domain: "127.0.0.1", path: "/" },
    ]);

    await page.goto("/dashboard");
    await page.waitForURL("**/login**");
    await expect(page.getByRole("status")).toContainText("session expired");
  });
});

test.describe("navigation permissions", () => {
  test("never offers a route whose backend API does not exist", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("Email address").fill(EMAIL);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.waitForURL("**/dashboard");

    const nav = page.getByRole("navigation", { name: "Main" });
    // tax/ and compliance/ have no api package; audit/ has no urls.py.
    for (const label of ["e-Invoices", "e-Way Bills", "Tax Rates", "Audit Trail"]) {
      await expect(nav.getByRole("link", { name: label })).toHaveCount(0);
    }
  });
});
