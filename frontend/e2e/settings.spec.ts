import { expect, test } from "@playwright/test";
import { EMAIL, expectHeading, signIn, uniqueSuffix } from "./helpers";

const API_BASE_URL = process.env.E2E_API_URL ?? "http://127.0.0.1:8001/api/v1";

/**
 * Settings pages render from the live API: the active organization
 * (GET organizations/), its members (GET organizations/members/) and the
 * signed-in user (GET auth/me/). Nothing here is editable yet, and the
 * blocked settings are listed rather than offered as forms.
 */

test("settings pages show the organization, its members and the signed-in user", async ({ page }) => {
  test.setTimeout(90_000);
  await signIn(page);

  await page.goto("/settings");
  await expectHeading(page, "Settings");
  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { name: "Organization profile" })).toBeVisible();
  await expect(main.getByText("Frontend Test Co", { exact: true }).first()).toBeVisible();
  // The fiscal-year blocker is listed, flagged as blocking posting.
  const blocker = main.getByRole("listitem").filter({ hasText: "Blocks posting" });
  await expect(blocker).toHaveCount(1);
  await expect(blocker).toContainText("Fiscal years");

  await page.goto("/settings/members");
  await expectHeading(page, "Members");
  const members = page.getByRole("table", { name: /Members of/ });
  const me = members.getByRole("row").filter({ hasText: EMAIL });
  await expect(me).toHaveCount(1);
  await expect(me.getByText("You", { exact: true })).toBeVisible();
  await expect(me.getByText("Owner", { exact: true })).toBeVisible();

  await page.goto("/settings/profile");
  await expectHeading(page, "Your profile");
  await expect(page.getByRole("main").getByText(EMAIL).first()).toBeVisible();
});

test("a new user with no organization creates one through onboarding and lands on the dashboard", async ({
  page,
  playwright,
}) => {
  test.setTimeout(120_000);
  const suffix = uniqueSuffix();
  const email = `automation-onboarding-${suffix}@easybook.local`;
  const password = `Onboard-${suffix}-pass`;
  const organizationName = `automation-onboarding Co ${suffix}`;

  // A FRESH user, never the shared test user: giving that user a second
  // organization could change which one a new sign-in opens for everyone
  // else. The app has no sign-up page, so the account is registered straight
  // against the API (POST auth/register/ is public).
  const backend = await playwright.request.newContext();
  const registered = await backend.post(`${API_BASE_URL}/auth/register/`, {
    data: { email, password, first_name: "Onboarding", last_name: "Tester" },
  });
  expect(registered.status()).toBe(201);
  await backend.dispose();

  await page.goto("/login");
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();

  // requireSession() sends a user with no organization here.
  await page.waitForURL("**/onboarding/organization", { timeout: 60_000 });
  await expectHeading(page, "Set up your organization");

  await page.getByRole("textbox", { name: "Organization name (required)", exact: true }).fill(organizationName);
  await expect(page.getByRole("textbox", { name: "Base currency (required)", exact: true })).toHaveValue("INR");
  await page.getByRole("textbox", { name: "Time zone (required)", exact: true }).fill("Asia/Kolkata");
  await page.getByRole("combobox", { name: "Fiscal year starts in (required)", exact: true }).selectOption("4");
  await page.getByRole("button", { name: "Create organization" }).click();

  await page.waitForURL("**/dashboard", { timeout: 60_000 });

  // What the server stored, and that the new organization is the open one.
  const organizations = await (await page.request.get("/api/bff/organizations")).json();
  expect(organizations).toHaveLength(1);
  expect(organizations[0]).toMatchObject({
    name: organizationName,
    default_currency: "INR",
    timezone: "Asia/Kolkata",
    fiscal_year_start_month: 4,
    role: "owner",
  });
  await expect(page.getByText(organizationName).first()).toBeVisible({ timeout: 30_000 });
});
