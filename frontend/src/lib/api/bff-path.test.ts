import { describe, expect, it } from "vitest";
import { containedUpstreamUrl, resolveBffPath } from "./bff-path";

const API = "http://api-host:8000/api/v1";

/** What the catch-all route would receive: Next decodes each segment once. */
function segmentsOf(rawPath: string): string[] {
  return rawPath.split("/").map((segment) => decodeURIComponent(segment));
}

describe("resolveBffPath", () => {
  it("forwards ordinary API paths", () => {
    expect(resolveBffPath(["accounting", "fiscal-years", "setup-status"])).toBe("/accounting/fiscal-years/setup-status/");
    expect(resolveBffPath(["sales", "invoices", "3fdd5277-c778-47ad-953f-e1efc379811d"])).toBe(
      "/sales/invoices/3fdd5277-c778-47ad-953f-e1efc379811d/",
    );
  });

  it.each([
    ["../admin"],
    ["../../admin"],
    ["./admin"],
    [".%2e/.%2e/admin"],
    ["%2e%2e/%2e%2e/admin"],
    [".%252e/.%252e/admin"],
    ["%252e%252e/%252e%252e/admin"],
    ["%2E%2E/admin"],
    ["..%2fadmin"],
    ["..%5cadmin"],
    ["..\\admin"],
    ["accounts%2f..%2f..%2fadmin"],
    ["accounts/..%00/admin"],
    ["accounts/%20/admin"],
    ["accounts/a;b"],
    ["accounts/ünïcode"],
    [""],
  ])("rejects %s", (rawPath) => {
    expect(resolveBffPath(segmentsOf(rawPath))).toBeNull();
  });

  it("rejects an empty segment list", () => {
    expect(resolveBffPath([])).toBeNull();
  });
});

describe("containedUpstreamUrl", () => {
  it("keeps normal paths inside the API base", () => {
    expect(containedUpstreamUrl(API, "/organizations/")).toBe("http://api-host:8000/api/v1/organizations/"); // gitleaks:allow (a test URL, not a credential)
    expect(containedUpstreamUrl(API, "accounting/fiscal-years", "page=2")).toBe(
      "http://api-host:8000/api/v1/accounting/fiscal-years/?page=2",
    );
  });

  it.each([
    ["/.%2e/.%2e/admin/"],
    ["/../../admin/"],
    ["/./x/"],
    ["/a/%2E%2E/%2E%2E/%2E%2E/admin/"],
    ["/a b/"],
    ["//evil.example/x/"],
  ])("refuses %s, which the URL parser would rewrite", (path) => {
    expect(() => containedUpstreamUrl(API, path)).toThrow(/escapes the API base/);
  });
});
