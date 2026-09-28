// @vitest-environment node
import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NetworkError, TimeoutError } from "@/lib/api/errors";

const refreshTokens = vi.fn();
vi.mock("server-only", () => ({}));
vi.mock("@/lib/auth/refresh", () => ({ refreshTokens: (token: string) => refreshTokens(token) }));

const { proxy } = await import("./proxy");

/** An unsigned JWT whose exp is `secondsFromNow` away — only the claims are read. */
function tokenExpiringIn(secondsFromNow: number): string {
  const encode = (value: object) => Buffer.from(JSON.stringify(value)).toString("base64url");
  const exp = Math.floor(Date.now() / 1000) + secondsFromNow;
  return `${encode({ alg: "HS256", typ: "JWT" })}.${encode({ exp })}.signature`;
}

function pageRequest(path: string, cookies: Record<string, string> = {}): NextRequest {
  const cookie = Object.entries(cookies)
    .map(([name, value]) => `${name}=${value}`)
    .join("; ");
  return new NextRequest(`http://localhost:3000${path}`, { headers: cookie ? { cookie } : {} });
}

const expiringSession = { eb_at: tokenExpiringIn(10), eb_rt: "refresh-token", eb_org: "org-1" };

function clearedCookies(response: Response): string[] {
  return response.headers
    .getSetCookie()
    .filter((header) => /Max-Age=0/i.test(header))
    .map((header) => header.split("=")[0] ?? "");
}

describe("proxy token refresh", () => {
  // Block body: a function returned from beforeEach runs as teardown, and
  // mockReset() returns the mock itself.
  beforeEach(() => {
    refreshTokens.mockReset();
  });

  it.each([
    ["unreachable", new NetworkError("Could not reach EasyBook.")],
    ["timing out", new TimeoutError(15_000)],
  ])("renders the page and keeps the session when the backend is %s", async (_label, failure) => {
    refreshTokens.mockRejectedValue(failure);

    const response = await proxy(pageRequest("/dashboard", expiringSession));

    expect(response.status).toBe(200);
    expect(response.headers.get("location")).toBeNull();
    expect(response.headers.get("content-security-policy")).toContain("nonce-");
    expect(clearedCookies(response)).toEqual([]);
  });

  it("still ends a session the backend rejects", async () => {
    refreshTokens.mockResolvedValue(null);

    const response = await proxy(pageRequest("/dashboard", expiringSession));

    expect(response.headers.get("location")).toBe("http://localhost:3000/login?reason=expired");
    expect(clearedCookies(response).sort()).toEqual(["eb_at", "eb_org", "eb_rt"]);
  });

  it("does not swallow errors that are not network failures", async () => {
    refreshTokens.mockRejectedValue(new Error("bug"));
    await expect(proxy(pageRequest("/dashboard", expiringSession))).rejects.toThrow("bug");
  });

  it("does not refresh a token that is not close to expiry", async () => {
    const response = await proxy(
      pageRequest("/dashboard", { ...expiringSession, eb_at: tokenExpiringIn(3600) }),
    );
    expect(response.status).toBe(200);
    expect(refreshTokens).not.toHaveBeenCalled();
  });
});

describe("proxy public pages", () => {
  it("sends a signed-in visitor from /register into the app", async () => {
    const response = await proxy(pageRequest("/register", { ...expiringSession, eb_at: tokenExpiringIn(3600) }));
    expect(response.headers.get("location")).toBe("http://localhost:3000/dashboard");
  });

  it("does not bounce /login?reason=expired, which would loop", async () => {
    const response = await proxy(
      pageRequest("/login?reason=expired", { ...expiringSession, eb_at: tokenExpiringIn(3600) }),
    );
    expect(response.headers.get("location")).toBeNull();
  });

  it("sends a signed-out visitor to sign in, carrying the destination", async () => {
    const response = await proxy(pageRequest("/sales/invoices?page=2"));
    const location = new URL(response.headers.get("location") ?? "");
    expect(location.pathname).toBe("/login");
    expect(location.searchParams.get("next")).toBe("/sales/invoices?page=2");
  });
});
