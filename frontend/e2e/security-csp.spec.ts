import { expect, test, type ConsoleMessage, type Page } from "@playwright/test";
import { signIn } from "./helpers";

/**
 * The Content-Security-Policy must block what it is for and nothing else.
 *
 * A too-strict policy fails silently in the worst way: the HTML renders, the
 * scripts are refused, and the page looks fine but never hydrates — forms do
 * native submits, dialogs never open. So this asserts both halves: the header
 * is present and nonce-based, and real pages report ZERO CSP violations while
 * their interactive parts actually work.
 */

function collectViolations(page: Page): string[] {
  const violations: string[] = [];
  page.on("console", (message: ConsoleMessage) => {
    const text = message.text();
    if (/Content Security Policy|Refused to (load|execute|apply)/i.test(text)) violations.push(text);
  });
  return violations;
}

test("serves a nonce-based policy on pages", async ({ request }) => {
  const response = await request.get("/login");
  const policy = response.headers()["content-security-policy"] ?? "";
  expect(policy).toMatch(/script-src 'self' 'nonce-[A-Za-z0-9+/=]+' 'strict-dynamic'/);
  expect(policy).toContain("frame-ancestors 'none'");
  expect(policy).toContain("object-src 'none'");
  expect(policy).not.toMatch(/script-src[^;]*'unsafe-inline'/);
});

test("hydrates the app under the policy with no violations", async ({ page }) => {
  const violations = collectViolations(page);

  await signIn(page);
  await expect(page.getByRole("heading", { level: 1, name: "Dashboard" })).toBeVisible();

  // An interactive client form: the combobox only opens if hydration ran, and
  // it only has options if the browser's same-origin API call succeeded under
  // the policy. (An open-but-empty list once hid a production-only failure:
  // a 308 on the API path upgraded to https by the CSP.)
  await page.goto("/sales/invoices/new");
  await page.getByRole("combobox", { name: "Customer" }).click();
  await expect(page.getByRole("listbox")).toBeVisible();
  await expect(page.getByRole("option").first()).toBeVisible();

  // A dialog-driven action and a page with inline style attributes (table widths).
  await page.goto("/inventory/adjustments");
  await expect(page.getByRole("heading", { level: 1, name: "Stock adjustments" })).toBeVisible();

  expect(violations).toEqual([]);
});
