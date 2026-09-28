import { expect, test } from "@playwright/test";
import { EMAIL, uniqueSuffix } from "./helpers";

/**
 * Sign-up against the real backend: account creation, automatic sign-in into
 * organization setup, and the backend's own validation surfacing on the
 * right inputs. Each run creates one throwaway user.
 */

const STRONG_PASSWORD = "Quiet-ledger-Harbor-81";

test.describe("sign-up", () => {
  test("creates an account and lands on organization setup, signed in", async ({ page, context }) => {
    await page.goto("/login");
    await page.getByRole("link", { name: "Create an account" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Create your account" })).toBeVisible();

    await page.getByLabel("First name").fill("E2E");
    await page.getByLabel("Last name").fill("Signup");
    await page.getByLabel("Email address").fill(`e2e-signup-${uniqueSuffix()}@easybook.local`);
    await page.getByLabel(/^Password/).fill(STRONG_PASSWORD);
    await page.getByLabel("Confirm password").fill(STRONG_PASSWORD);
    await page.getByRole("button", { name: "Create account" }).click();

    await page.waitForURL("**/onboarding/organization");
    await expect(page.getByRole("heading", { level: 1, name: "Set up your organization" })).toBeVisible();

    const cookies = await context.cookies();
    expect(cookies.find((cookie) => cookie.name === "eb_at")?.httpOnly).toBe(true);
    expect(cookies.find((cookie) => cookie.name === "eb_rt")?.httpOnly).toBe(true);

    // Signed in, so the sign-up page now sends them into the app.
    await page.goto("/register");
    await expect(page).not.toHaveURL(/\/register/);
  });

  test("shows the backend's password rules on the password field", async ({ page }) => {
    await page.goto("/register");
    // Long enough for the client check, but on Django's common-password list.
    await page.getByLabel("Email address").fill(`e2e-weak-${uniqueSuffix()}@easybook.local`);
    await page.getByLabel(/^Password/).fill("password123");
    await page.getByLabel("Confirm password").fill("password123");
    await page.getByRole("button", { name: "Create account" }).click();

    const form = page.locator("form");
    await expect(form.getByRole("alert").first()).toBeVisible();
    await expect(page.getByLabel(/^Password/)).toHaveAttribute("aria-invalid", "true");
    await expect(form).toContainText("too common");
    await expect(page).toHaveURL(/\/register/);
  });

  test("refuses an email that already has an account, in any letter case", async ({ page }) => {
    await page.goto("/register");
    await page.getByLabel("Email address").fill(EMAIL.toUpperCase());
    await page.getByLabel(/^Password/).fill(STRONG_PASSWORD);
    await page.getByLabel("Confirm password").fill(STRONG_PASSWORD);
    await page.getByRole("button", { name: "Create account" }).click();

    await expect(page.getByLabel("Email address")).toHaveAttribute("aria-invalid", "true");
    await expect(page.locator("form")).toContainText("already exists");
    await expect(page).toHaveURL(/\/register/);
  });

  test("catches a mismatched confirmation before calling the backend", async ({ page }) => {
    let registerCalls = 0;
    page.on("request", (request) => {
      if (request.url().includes("/api/auth/register")) registerCalls += 1;
    });

    await page.goto("/register");
    await page.getByLabel("Email address").fill(`e2e-mismatch-${uniqueSuffix()}@easybook.local`);
    await page.getByLabel(/^Password/).fill(STRONG_PASSWORD);
    await page.getByLabel("Confirm password").fill(`${STRONG_PASSWORD}x`);
    await page.getByRole("button", { name: "Create account" }).click();

    await expect(page.locator("form")).toContainText("The passwords do not match.");
    expect(registerCalls).toBe(0);
  });
});
