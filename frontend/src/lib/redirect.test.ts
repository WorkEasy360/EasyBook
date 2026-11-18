// @vitest-environment node
import { describe, expect, it } from "vitest";
import { redirectToPath, sameSitePath } from "./redirect";

const TAB = String.fromCharCode(9);
const NEWLINE = String.fromCharCode(10);
const BACKSLASH = String.fromCharCode(92);

describe("redirectToPath", () => {
  it("sends a relative Location, so the browser stays on the address it used", () => {
    const response = redirectToPath("/login?sso_error=expired");
    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe("/login?sso_error=expired");
  });

  it.each(["https://evil.example/x", "//evil.example/x", `/${BACKSLASH}evil.example`, "dashboard", `/${TAB}/evil.example`])(
    "never sends the browser off-site (%j)",
    (path) => {
      expect(redirectToPath(path).headers.get("location")).toBe("/");
    },
  );
});

describe("sameSitePath", () => {
  it("keeps a same-site path with its query", () => {
    expect(sameSitePath("/invoices?page=2&q=a%20b")).toBe("/invoices?page=2&q=a%20b");
  });

  it.each([
    // A browser strips tabs and newlines and reads a backslash as a slash: each would become //evil.example.
    `/${TAB}/evil.example`,
    `/${NEWLINE}/evil.example`,
    `/${BACKSLASH}evil.example`,
    `/a${BACKSLASH}b`,
    "/ /evil.example",
    "//evil.example",
    "https://evil.example",
    "evil.example",
    "",
    null,
    undefined,
    `/${"a".repeat(2048)}`,
  ])("refuses %j", (value) => {
    expect(sameSitePath(value)).toBeNull();
  });
});
