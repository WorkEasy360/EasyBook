// @vitest-environment node
import { describe, expect, it } from "vitest";
import {
  AUTO_START_GUARD_SECONDS,
  SSO_START_PATH,
  autoStartPath,
  autoStartRefusal,
  isPageNavigation,
} from "./direct-visit";

const headers = (init: Record<string, string>) => new Headers(init);

describe("isPageNavigation", () => {
  it("is true for a browser loading a page", () => {
    expect(
      isPageNavigation("GET", headers({ "sec-fetch-mode": "navigate", "sec-fetch-dest": "document", accept: "text/html" })),
    ).toBe(true);
  });

  it("falls back to Accept where the browser sends no Sec-Fetch headers (plain HTTP, old browsers)", () => {
    expect(isPageNavigation("GET", headers({ accept: "text/html,application/xhtml+xml,*/*;q=0.8" }))).toBe(true);
    expect(isPageNavigation("GET", headers({ accept: "*/*" }))).toBe(false);
    expect(isPageNavigation("GET", headers({ accept: "application/json" }))).toBe(false);
    expect(isPageNavigation("GET", headers({}))).toBe(false);
  });

  it.each([
    ["a fetch()", { "sec-fetch-mode": "cors", "sec-fetch-dest": "empty", accept: "*/*" }],
    ["a same-origin fetch()", { "sec-fetch-mode": "same-origin", "sec-fetch-dest": "empty", accept: "text/html" }],
    ["a frame", { "sec-fetch-mode": "navigate", "sec-fetch-dest": "iframe", accept: "text/html" }],
    ["an embedded object", { "sec-fetch-mode": "navigate", "sec-fetch-dest": "embed", accept: "text/html" }],
    ["a Next client-side navigation", { rsc: "1", accept: "text/x-component" }],
    [
      "a Next client-side navigation that looks like a document",
      { rsc: "1", "sec-fetch-mode": "navigate", "sec-fetch-dest": "document" },
    ],
    ["a Next link prefetch", { rsc: "1", "next-router-prefetch": "1" }],
    ["a router-state request", { "next-router-state-tree": "%5B%5D", accept: "text/html" }],
    ["a browser prefetch", { "sec-purpose": "prefetch", "sec-fetch-mode": "navigate", "sec-fetch-dest": "document" }],
    [
      "a browser prerender",
      { "sec-purpose": "prefetch;prerender", "sec-fetch-mode": "navigate", "sec-fetch-dest": "document" },
    ],
    ["a legacy prefetch", { purpose: "prefetch", accept: "text/html" }],
  ])("is false for %s", (_label, init) => {
    expect(isPageNavigation("GET", headers(init))).toBe(false);
  });

  it.each(["POST", "PUT", "DELETE", "HEAD", "OPTIONS"])("is false for a %s", (method) => {
    expect(
      isPageNavigation(method, headers({ "sec-fetch-mode": "navigate", "sec-fetch-dest": "document", accept: "text/html" })),
    ).toBe(false);
  });
});

describe("autoStartRefusal", () => {
  const now = 1_800_000_000_000;

  it("allows a browser with no marker and no recent automatic start", () => {
    expect(autoStartRefusal({}, now)).toBeNull();
    expect(autoStartRefusal({ signedOut: undefined, autoStarted: undefined }, now)).toBeNull();
    expect(autoStartRefusal({ signedOut: "", autoStarted: "" }, now)).toBeNull();
  });

  it("refuses after a sign-out, however long ago", () => {
    expect(autoStartRefusal({ signedOut: "1" }, now)).toBe("signed_out");
    expect(autoStartRefusal({ signedOut: "1", autoStarted: String(now - 3_600_000) }, now)).toBe("signed_out");
  });

  it("refuses within the loop-guard window and allows once it has passed", () => {
    const window = AUTO_START_GUARD_SECONDS * 1000;
    expect(AUTO_START_GUARD_SECONDS).toBe(60);
    expect(autoStartRefusal({ autoStarted: String(now) }, now)).toBe("recent_auto_start");
    expect(autoStartRefusal({ autoStarted: String(now - (window - 1)) }, now)).toBe("recent_auto_start");
    expect(autoStartRefusal({ autoStarted: String(now - window) }, now)).toBeNull();
    expect(autoStartRefusal({ autoStarted: String(now - 3_600_000) }, now)).toBeNull();
  });

  it("reads a loop-guard cookie it cannot parse as recent: the sign-in page, never another round trip", () => {
    expect(autoStartRefusal({ autoStarted: "garbage" }, now)).toBe("recent_auto_start");
  });
});

describe("autoStartPath", () => {
  const params = (path: string) => new URL(path, "https://x.invalid").searchParams;

  it("starts with no destination for the home page, and never an organization hint", () => {
    expect(autoStartPath("/", "")).toBe(SSO_START_PATH);
    expect(autoStartPath("/", "?x=1")).toBe(SSO_START_PATH);
    expect(SSO_START_PATH).toBe("/api/auth/workeasy360/start");
  });

  it("carries the page asked for, query included, as an encoded same-site path", () => {
    const path = autoStartPath("/sales/invoices", "?page=2&q=a%26b");
    expect(path).toBe(`${SSO_START_PATH}?next=%2Fsales%2Finvoices%3Fpage%3D2%26q%3Da%2526b`);
    expect(params(path).get("next")).toBe("/sales/invoices?page=2&q=a%26b");
    expect(params(path).has("organization")).toBe(false);
  });

  it("keeps a hostile ?next= of the page itself inside the encoded value", () => {
    const path = autoStartPath("/sales/invoices", "?next=https://evil.example");
    expect(params(path).getAll("next")).toEqual(["/sales/invoices?next=https://evil.example"]);
  });

  it.each([
    ["a protocol-relative path", "//evil.example/dashboard", ""],
    ["a backslash", String.fromCharCode(47, 92) + "evil.example", ""],
    ["a tab", "/" + String.fromCharCode(9) + "/evil.example", ""],
    ["a newline in the query", "/dashboard", "?x=1" + String.fromCharCode(10)],
    ["an absolute URL", "https://evil.example/", ""],
    ["a path that does not start with a slash", "evil.example", ""],
    ["an over-long path", `/${"a".repeat(2100)}`, ""],
  ])("never forwards %s as next", (_label, pathname, search) => {
    expect(autoStartPath(pathname, search)).toBe(SSO_START_PATH);
  });
});
