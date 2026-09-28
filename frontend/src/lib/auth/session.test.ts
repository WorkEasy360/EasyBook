// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const set = vi.fn();
vi.mock("server-only", () => ({}));
vi.mock("next/headers", () => ({ cookies: async () => ({ set, get: vi.fn() }) }));

const { ACCESS_COOKIE, ORG_COOKIE, REFRESH_COOKIE, clearSession } = await import("./session");

describe("clearSession", () => {
  const original = process.env.SESSION_COOKIE_SECURE;

  beforeEach(() => set.mockReset());
  afterEach(() => {
    if (original === undefined) delete process.env.SESSION_COOKIE_SECURE;
    else process.env.SESSION_COOKIE_SECURE = original;
  });

  it("expires every session cookie with the attributes it was set with", async () => {
    process.env.SESSION_COOKIE_SECURE = "1";
    await clearSession();

    const cleared = new Map(set.mock.calls.map(([name, value, options]) => [name, { value, options }]));
    expect([...cleared.keys()].sort()).toEqual([ACCESS_COOKIE, ORG_COOKIE, REFRESH_COOKIE].sort());
    for (const { value, options } of cleared.values()) {
      expect(value).toBe("");
      expect(options).toMatchObject({ maxAge: 0, httpOnly: true, secure: true, sameSite: "lax", path: "/" });
    }
  });
});
