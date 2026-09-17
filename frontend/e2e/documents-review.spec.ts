import { expect, test } from "@playwright/test";
import { expectHeading, signIn } from "./helpers";

/**
 * OCR review E2E: approve an extraction with a corrected field and assert the
 * server moved the document to Completed.
 *
 * A document only reaches `needs_review` after a Celery worker has run the
 * OCR task, and nothing in the API can do that synchronously. So this test
 * looks for an existing documents-module document awaiting review and SKIPS,
 * saying why, when the environment has none (e.g. no worker ran). Approving
 * is final, so each such document is consumed by one run.
 */

interface ListedDocument {
  id: string;
  title: string;
  ocr_status: string;
}

test("approves an OCR extraction with a corrected field", async ({ page }) => {
  test.setTimeout(120_000);
  await signIn(page);

  const listing = await page.request.get("/api/bff/documents?page_size=200");
  expect(listing.ok()).toBe(true);
  const { results } = (await listing.json()) as { results: ListedDocument[] };
  const candidate = results.find((doc) => doc.ocr_status === "needs_review" && doc.title.startsWith("Docs"));
  test.skip(!candidate, "No documents-module document is awaiting OCR review (requires an OCR worker run).");
  const target = candidate as ListedDocument;

  await page.goto(`/documents/${target.id}`);
  await expectHeading(page, target.title);
  await expect(page.getByText("Needs Review", { exact: false }).first()).toBeVisible();

  await page.getByRole("button", { name: "Review extraction" }).click();
  const dialog = page.getByRole("dialog", { name: "Review OCR extraction" });
  await expect(dialog.getByText("Extracted by OCR — verify before use")).toBeVisible();
  await dialog.getByRole("button", { name: "Add field" }).click();
  const field = dialog.getByRole("group", { name: "Field 1", exact: true });
  await field.getByLabel("Field 1 name").fill("total");
  await field.getByLabel("Field 1 value").fill("118.00");
  await dialog.getByLabel("Notes").fill("Checked against the paper receipt.");
  await dialog.getByRole("button", { name: "Approve extraction" }).click();

  // The approval moved ocr_status to completed on the server.
  await expect(page.getByRole("button", { name: "Review extraction" })).toHaveCount(0);
  await expect(page.getByText("Completed", { exact: false }).first()).toBeVisible();
});
