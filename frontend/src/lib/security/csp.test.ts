import { describe, expect, it } from "vitest";
import { buildContentSecurityPolicy, generateNonce } from "./csp";

function directive(policy: string, name: string): string | undefined {
  return policy
    .split(";")
    .map((part) => part.trim())
    .find((part) => part === name || part.startsWith(`${name} `));
}

describe("buildContentSecurityPolicy", () => {
  const production = buildContentSecurityPolicy("abc123", { development: false, https: true });
  const development = buildContentSecurityPolicy("abc123", { development: true, https: false });
  const productionOverHttp = buildContentSecurityPolicy("abc123", { development: false, https: false });

  it("locks scripts to the request nonce with strict-dynamic and no unsafe-inline", () => {
    const scripts = directive(production, "script-src");
    expect(scripts).toBe("script-src 'self' 'nonce-abc123' 'strict-dynamic'");
    expect(production).not.toContain("'unsafe-eval'");
  });

  it("never allows inline style ELEMENTS, only style attributes", () => {
    expect(directive(production, "style-src")).toBe("style-src 'self' 'nonce-abc123'");
    expect(directive(production, "style-src-attr")).toBe("style-src-attr 'unsafe-inline'");
  });

  it("lets the browser talk only to its own origin (the BFF)", () => {
    expect(directive(production, "connect-src")).toBe("connect-src 'self'");
  });

  it("forbids framing, plugins and base-tag hijacking", () => {
    expect(directive(production, "frame-ancestors")).toBe("frame-ancestors 'none'");
    expect(directive(production, "object-src")).toBe("object-src 'none'");
    expect(directive(production, "base-uri")).toBe("base-uri 'self'");
    expect(directive(production, "form-action")).toBe("form-action 'self'");
  });

  it("upgrades insecure requests only for a production page served over https", () => {
    expect(directive(production, "upgrade-insecure-requests")).toBeDefined();
    expect(directive(development, "upgrade-insecure-requests")).toBeUndefined();
    // Regression: over plain http the upgrade turned every same-origin API
    // redirect into a failing https request.
    expect(directive(productionOverHttp, "upgrade-insecure-requests")).toBeUndefined();
    expect(directive(productionOverHttp, "script-src")).toBe("script-src 'self' 'nonce-abc123' 'strict-dynamic'");
  });

  it("adds eval, websockets and inline <style> only for the development server", () => {
    expect(directive(development, "script-src")).toContain("'unsafe-eval'");
    expect(directive(development, "connect-src")).toBe("connect-src 'self' ws:");
    // Dev scripts stay nonce-locked even though dev styles are relaxed.
    expect(directive(development, "script-src")).toContain("'nonce-abc123'");
    expect(directive(development, "style-src")).toBe("style-src 'self' 'unsafe-inline'");
  });
});

describe("generateNonce", () => {
  it("is unique per call and safe to embed in a header", () => {
    const first = generateNonce();
    const second = generateNonce();
    expect(first).not.toBe(second);
    expect(first).toMatch(/^[A-Za-z0-9+/=]+$/);
    expect(first.length).toBeGreaterThanOrEqual(24);
  });
});
