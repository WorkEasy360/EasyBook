import { expect, test } from "@playwright/test";
import { expectHeading, signIn } from "./helpers";

/**
 * Ask Books E2E. Whatever the backend is configured to do, the UI must be
 * honest about it: either a real answer carrying the non-authoritative notice
 * and its sources, or exactly "AI Assistant is not currently enabled." with no
 * answer at all. One question only — the backend rate-limits per user.
 */

const DISABLED = "AI Assistant is not currently enabled.";
const NOTICE = "Explanation, not a figure of record.";

test("asks a question and shows either a cited, non-authoritative answer or the disabled message", async ({ page }) => {
  test.setTimeout(120_000);
  await signIn(page);
  await page.goto("/ai");
  await expectHeading(page, "Ask Books");

  await page.getByLabel("Your question").fill("What is my net profit this month?");
  const asked = page.waitForResponse(
    (response) =>
      response.url().includes("/api/bff/ai/ask") &&
      response.request().method() === "POST" &&
      // The client's trailing-slash URL is first answered with Next's 308.
      response.status() !== 308,
    { timeout: 100_000 },
  );
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  const response = await asked;

  const conversation = page.getByRole("list", { name: "Conversation" });
  if (response.ok()) {
    const body = await response.json();
    expect(typeof body.answer).toBe("string");
    await expect(conversation.getByText(NOTICE)).toBeVisible();
    await expect(conversation.getByRole("heading", { name: "Sources" })).toBeVisible();
    await expect(page.getByText(DISABLED)).toHaveCount(0);
    // The answer is rendered as text exactly as returned.
    await expect(conversation.getByText(body.answer.slice(0, 40), { exact: false })).toBeVisible();
    // The new conversation is listed without a reload.
    await expect(page.getByRole("navigation", { name: "Conversations" }).getByRole("link", { name: /net profit this month/ }).first()).toBeVisible();
  } else {
    const body = await response.json();
    if (["ai_disabled", "ai_unavailable"].includes(body.error?.code)) {
      await expect(page.getByRole("alert").filter({ hasText: DISABLED })).toHaveText(DISABLED);
      await expect(conversation.getByText(NOTICE)).toHaveCount(0);
    } else {
      // Any other failure (e.g. rate limit) must surface the backend's message.
      await expect(page.getByText(body.error.message)).toBeVisible();
    }
  }
});
