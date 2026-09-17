import { expect, test, type Page } from "@playwright/test";
import { moneyAriaLabel } from "../src/lib/money";
import { expectHeading, signIn } from "./helpers";

/**
 * Reports E2E — read-only, against the real engine.
 *
 * Every figure asserted here is fetched from the API in the same session and
 * compared with what the page renders, so the tests prove the UI shows the
 * engine's numbers rather than numbers it derived. Amounts are matched on the
 * accessible label <Money> gives them ("₹4,502.45" / "negative ₹…"), which is
 * independent of the accounting parentheses used visually.
 */

async function apiGet<T>(page: Page, path: string): Promise<T> {
  // Same-origin BFF call with the signed-in cookies — the browser's own path.
  const response = await page.request.get(`/api/bff/${path}`);
  expect(response.ok(), `GET ${path} → ${response.status()}`).toBe(true);
  return (await response.json()) as T;
}

// Report pages fan out to the engine and the dev server is shared by several
// suites compiling at once; the default 30s is too tight for a cold route.
test.describe.configure({ timeout: 120_000 });

const inr = (value: string) => moneyAriaLabel(value, { currency: "INR", locale: "en-IN" });

test("report centre lists the reports and links to them", async ({ page }) => {
  await signIn(page);
  await page.goto("/reports");
  await expectHeading(page, "Reports");

  for (const name of ["Profit and loss", "Balance sheet", "Customer balances", "GSTR-3B summary", "Journal report"]) {
    await expect(page.getByRole("link", { name, exact: true })).toBeVisible();
  }

  await page.getByRole("link", { name: "Profit and loss", exact: true }).click();
  await page.waitForURL("**/reports/profit-loss");
  await expectHeading(page, "Profit and loss");
});

test("profit and loss for a custom period shows the engine's totals", async ({ page }) => {
  await signIn(page);
  await page.goto("/reports/profit-loss");
  await expectHeading(page, "Profit and loss");

  const filters = page.getByRole("form", { name: "Report filters" });
  await filters.getByLabel("From", { exact: true }).fill("2026-04-01");
  await filters.getByLabel("To", { exact: true }).fill("2026-09-30");
  await filters.getByRole("button", { name: "Apply" }).click();
  await page.waitForURL(/from_date=2026-04-01.*to_date=2026-09-30/);
  await expect(page.getByText("01 Apr 2026 – 30 Sep 2026")).toBeVisible();

  const pnl = await apiGet<{ totals: Record<string, string> }>(
    page,
    "reports/profit-loss?from_date=2026-04-01&to_date=2026-09-30",
  );

  const statement = page.getByRole("table", { name: "Profit and loss" });
  for (const [label, key] of [
    ["Revenue", "revenue"],
    ["Gross profit", "gross_profit"],
    ["Operating profit", "operating_profit"],
    ["Net profit", "net_profit"],
  ] as const) {
    const row = statement.getByRole("row", { name: new RegExp(`^${label}`) });
    await expect(row.getByLabel(inr(pnl.totals[key] ?? ""), { exact: true })).toBeVisible();
  }

  await expect(page.getByText(/Figures from the accounting engine as of/)).toBeVisible();
});

test("GSTR-3B summary opens on the current month with the API's figures", async ({ page }) => {
  await signIn(page);
  await page.goto("/tax/gstr-3b");
  await expectHeading(page, "GSTR-3B summary");

  // The default period is the current month in the organization's timezone —
  // the "This month" preset is the active one.
  const thisMonth = page.getByRole("link", { name: /^This month/ });
  await expect(thisMonth).toHaveAttribute("aria-current", "true");
  const href = (await thisMonth.getAttribute("href")) ?? "";
  const params = new URL(href, "http://localhost").searchParams;
  const from = params.get("from_date") ?? "";
  const to = params.get("to_date") ?? "";
  expect(from).toMatch(/^\d{4}-\d{2}-01$/);

  await expect(page.getByText(/e-Invoice \(IRN\) generation, e-Way Bill generation and GST return filing are not available/)).toBeVisible();

  const summary = await apiGet<{ outward: { taxable: { taxable_value: string } }; itc: { all_other: { taxable_value: string } } }>(
    page,
    `reports/tax/gstr3b-summary?from_date=${from}&to_date=${to}`,
  );

  const outward = page.getByRole("table", { name: /\(outward\)$/ });
  const taxableRow = outward.getByRole("row", { name: /outward\.taxable/ });
  // The first data cell is taxable value (the row header holds the field name).
  await expect(
    taxableRow.getByRole("cell").first().getByLabel(inr(summary.outward.taxable.taxable_value), { exact: true }),
  ).toBeVisible();

  const itc = page.getByRole("table", { name: /\(itc\)$/ });
  const itcRow = itc.getByRole("row", { name: /itc\.all_other/ });
  await expect(
    itcRow.getByRole("cell").first().getByLabel(inr(summary.itc.all_other.taxable_value), { exact: true }),
  ).toBeVisible();
});

test("CSV export is offered only where the backend has one", async ({ page }) => {
  await signIn(page);

  await page.goto("/reports/receivables/customer-balances");
  await expectHeading(page, "Customer balances");
  const exportLink = page.getByRole("link", { name: "Export CSV" });
  await expect(exportLink).toHaveAttribute("href", "/api/bff/reports/receivables/customer-balances?export=csv");
  const csv = await page.request.get("/api/bff/reports/receivables/customer-balances?export=csv");
  expect(csv.ok()).toBe(true);
  expect(csv.headers()["content-type"]).toContain("text/csv");

  await page.goto("/reports/balance-sheet");
  await expectHeading(page, "Balance sheet");
  await expect(page.getByRole("link", { name: "Export CSV" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Print" })).toBeVisible();
});
