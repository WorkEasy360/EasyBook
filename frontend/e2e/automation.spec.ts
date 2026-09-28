import { expect, test } from "@playwright/test";
import { expectHeading, signIn, uniqueSuffix } from "./helpers";

/**
 * Critical E2E — automation rule lifecycle: build from the live catalog →
 * save draft → activate → rename.
 *
 * Asserts what the SERVER holds after each step (status, version, the
 * condition and action it stored), read back through the BFF, never what the
 * form showed. Renaming must not bump the version: sending conditions or
 * actions on PATCH would, and the form only sends them when they changed.
 */

test("builds a rule from the catalog, saves it as a draft, activates it and renames it", async ({ page }) => {
  // Four page loads plus two saves; a shared dev server compiling on first hit needs longer than the default.
  test.setTimeout(120_000);
  const name = `automation-e2e ${uniqueSuffix()}`;
  await signIn(page);

  await page.goto("/automation/rules/new");
  await expectHeading(page, "New automation rule");

  await page.getByRole("textbox", { name: "Name (required)", exact: true }).fill(name);
  // Trigger options come from GET automation/catalog/triggers/.
  await page.getByRole("combobox", { name: "Trigger (required)", exact: true }).selectOption("invoice.posted");

  await page.getByRole("button", { name: "Add condition" }).click();
  const condition = page.getByRole("group", { name: "Condition 1" });
  await condition.getByRole("combobox", { name: "Field (required)", exact: true }).selectOption("amount_due");
  await condition.getByRole("combobox", { name: "Comparison (required)", exact: true }).selectOption("greater_than");
  await condition.getByRole("textbox", { name: "Value (required)", exact: true }).fill("1000");

  const action = page.getByRole("group", { name: "Action 1" });
  await action.getByRole("combobox", { name: "Action (required)", exact: true }).selectOption("send_notification");
  // The Message input is generated from the action's config_schema.
  await action.getByRole("textbox", { name: "Message (required)", exact: true }).fill("A large invoice was posted.");

  await page.getByRole("button", { name: "Save as draft" }).click();
  await page.waitForURL(/\/automation\/rules\/[0-9a-f-]{36}$/);
  await expectHeading(page, name);
  const ruleId = page.url().split("/").pop() ?? "";

  const main = page.getByRole("main");
  await expect(main.getByText("Draft", { exact: true }).first()).toBeVisible();

  let saved = await (await page.request.get(`/api/bff/automation/rules/${ruleId}`)).json();
  expect(saved.status).toBe("draft");
  expect(saved.version).toBe(1);
  // The amount input normalizes to the currency's scale; the value stays a decimal string.
  expect(saved.conditions).toEqual([{ field: "amount_due", operator: "greater_than", value: "1000.00" }]);
  expect(saved.actions).toEqual([{ action_id: "send_notification", config: { message: "A large invoice was posted." } }]);

  await page.getByRole("button", { name: "Activate", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: `Activate ${name}?` });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Activate", exact: true }).click();

  // The dialog closes only on a successful response; the page then re-renders from the server.
  await expect(dialog).toBeHidden();
  // Pause is offered only to an active rule; the badge's marker glyph sits inside its text, hence the regex.
  await expect(page.getByRole("button", { name: "Pause", exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(main.getByText(/^\W*Active$/).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Activate", exact: true })).toHaveCount(0);

  saved = await (await page.request.get(`/api/bff/automation/rules/${ruleId}`)).json();
  expect(saved.status).toBe("active");

  // Rename only: conditions and actions are untouched, so the version stays.
  await page.getByRole("link", { name: "Edit", exact: true }).click();
  await expectHeading(page, `Edit ${name}`);
  await page.getByRole("textbox", { name: "Name (required)", exact: true }).fill(`${name} renamed`);
  await page.getByRole("button", { name: "Save rule" }).click();
  await expect(page.getByRole("heading", { level: 1, name: `${name} renamed` })).toBeVisible({ timeout: 30_000 });

  saved = await (await page.request.get(`/api/bff/automation/rules/${ruleId}`)).json();
  expect(saved.name).toBe(`${name} renamed`);
  expect(saved.status).toBe("active");
  expect(saved.version).toBe(1);
});
