import { expect, test } from "@playwright/test";
import { expectHeading, pickOption, signIn, uniqueSuffix } from "./helpers";

/**
 * Critical E2E — documents: upload → listed and searchable → detail →
 * download URL minted on click → OCR requested → linked to an invoice →
 * unlinked → archived.
 *
 * Every assertion is on what the SERVER returned (upload status after the
 * scan, the grant from POST download/, OCR status, the link row, the archived
 * state) — never on what the UI assumed. The PNG is generated here from
 * base64 text; no binary fixture is committed. Relies on the dev tenant's
 * posted invoice INV-0001.
 */

// A valid 1×1 PNG: the backend checks the PNG magic bytes, not just the name.
const PNG_1X1 = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
  "base64",
);

test("uploads a document, finds it, downloads it through a signed URL, links and archives it", async ({ page }) => {
  test.setTimeout(180_000);
  const suffix = uniqueSuffix();
  const title = `Docs E2E receipt ${suffix}`;
  const filename = `docs-e2e-${suffix}.png`;

  await signIn(page);
  await page.goto("/documents");
  await expectHeading(page, "Documents");

  // --- Upload, including the client pre-check refusing a disallowed type.
  await page.getByRole("button", { name: "Upload document" }).click();
  const upload = page.getByRole("dialog", { name: "Upload a document" });
  await expect(upload).toBeVisible();

  await upload.getByLabel("File").setInputFiles({ name: "payload.exe", mimeType: "application/octet-stream", buffer: Buffer.from("MZ") });
  await expect(upload.getByText("File type .exe is not allowed.", { exact: false })).toBeVisible();
  await expect(upload.getByRole("button", { name: "Upload", exact: true })).toBeDisabled();

  await upload.getByLabel("File").setInputFiles({ name: filename, mimeType: "image/png", buffer: PNG_1X1 });
  await upload.getByLabel("Title").fill(title);
  await upload.getByLabel("Document type").selectOption("receipt");
  await upload.getByRole("button", { name: "Upload", exact: true }).click();

  // `?duplicates=` is appended when this tenant already holds identical bytes
  // (the backend's possible_duplicate_ids) — expected on every run after the first.
  await page.waitForURL(/\/documents\/[0-9a-f-]{36}(\?.*)?$/);
  const documentId = new URL(page.url()).pathname.split("/").pop() as string;
  await expectHeading(page, title);
  // The scan runs inside the upload request, so the server already says Ready.
  // (The badge carries a non-colour marker glyph before its label.)
  await expect(page.getByRole("main").getByText(/^\W*Ready$/).first()).toBeVisible();

  // --- Listed, and found by the real search endpoint.
  await page.goto("/documents");
  await expect(page.getByRole("link", { name: title })).toBeVisible();
  await page.getByRole("searchbox", { name: "Search" }).fill(suffix);
  await page.getByRole("search").getByRole("button", { name: "Search" }).click();
  await page.waitForURL(/\/documents\?q=/);
  await expect(page.getByText("1 document matches", { exact: false })).toBeVisible();
  await page.getByRole("link", { name: title }).click();
  await page.waitForURL(`**/documents/${documentId}`);
  await expectHeading(page, title);

  // --- Download: the signed URL is minted only on click and never in the page.
  expect(await page.content()).not.toContain("local-storage");
  const grantResponse = page.waitForResponse(
    (response) =>
      response.url().includes(`/api/bff/documents/${documentId}/download`) &&
      response.request().method() === "POST" &&
      // The client's trailing-slash URL is first answered with Next's 308.
      response.status() !== 308,
  );
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download", exact: true }).click();
  const grant = await (await grantResponse).json();
  expect(typeof grant.url).toBe("string");
  expect(grant.expires_in).toBeGreaterThan(0);
  expect((await download).suggestedFilename()).toBe(filename);

  // --- OCR request: a background job, so the server moves it to Queued.
  await page.getByRole("button", { name: "Request OCR" }).click();
  const ocrDialog = page.getByRole("dialog", { name: "Extract text from this document?" });
  await ocrDialog.getByRole("button", { name: "Request OCR" }).click();
  await expect(page.getByText("Queued", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Request OCR" })).toHaveCount(0);

  // --- Link to an invoice, then remove the link.
  await page.getByRole("button", { name: "Link a record" }).click();
  const linkDialog = page.getByRole("dialog", { name: "Link a record" });
  await linkDialog.getByLabel("Record type").selectOption("invoice");
  await pickOption(page, "Invoice", "INV-0001");
  await linkDialog.getByRole("button", { name: "Link record" }).click();
  const linkedTable = page.getByRole("table", { name: /Records linked to/ });
  await expect(linkedTable.getByRole("link", { name: "INV-0001" })).toBeVisible();

  await linkedTable.getByRole("button", { name: /Unlink/ }).click();
  await page.getByRole("dialog", { name: "Remove this link?" }).getByRole("button", { name: "Remove link" }).click();
  await expect(page.getByText("Not linked to any record")).toBeVisible();

  // --- Archive: the server's archived state, and the file stays downloadable.
  await page.getByRole("button", { name: "Archive", exact: true }).click();
  await page.getByRole("dialog", { name: `Archive ${title}?` }).getByRole("button", { name: "Archive" }).click();
  await expect(page.getByRole("main").getByText("Archived", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Archive", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Download", exact: true })).toBeVisible();
});
