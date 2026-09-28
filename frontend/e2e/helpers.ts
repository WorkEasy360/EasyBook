import { expect, type Page } from "@playwright/test";

/**
 * Shared E2E plumbing. Tests run against a real backend and a real tenant
 * (spec §90) — nothing here mocks the API.
 */

export const EMAIL = process.env.E2E_EMAIL ?? "fe-test@easybook.local";
export const PASSWORD = process.env.E2E_PASSWORD ?? "FrontendTest123!";

export async function signIn(page: Page): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email address").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL("**/dashboard");
}

/** A suffix unique enough to keep parallel runs from colliding on codes. */
export function uniqueSuffix(): string {
  return `${Date.now().toString().slice(-7)}${Math.floor(Math.random() * 90 + 10)}`;
}

/**
 * Picks an option from a Combobox by typing into it. The picker filters one
 * page locally (no server search exists), so the option must be on that page.
 */
export async function pickOption(page: Page, label: string | RegExp, optionText: string | RegExp): Promise<void> {
  const input = page.getByRole("combobox", { name: label });
  await input.click();
  if (typeof optionText === "string") await input.fill(optionText);
  const option = page.getByRole("option", { name: optionText }).first();
  await expect(option).toBeVisible();
  await option.dispatchEvent("pointerdown");
}

/** Waits for the h1 of a page — every route renders exactly one. */
export async function expectHeading(page: Page, name: string | RegExp): Promise<void> {
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
}
