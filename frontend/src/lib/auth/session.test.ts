// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const set = vi.fn();
/** What the request carries, by cookie name. */
const incoming = new Map<string, string>();
vi.mock("server-only", () => ({}));
vi.mock("next/headers", () => ({
  cookies: async () => ({
    set,
    get: (name: string) => (incoming.has(name) ? { name, value: incoming.get(name) } : undefined),
  }),
}));

const {
  clearSession,
  clearSignedOutMarker,
  cookieSecurity,
  markSignedOut,
  readOrganizationId,
  readSession,
  writeOrganizationId,
  writeSession,
} = await import("./session");

/** `[name, value, options]` of every cookie set, by name. */
function setCookies() {
  return new Map(set.mock.calls.map(([name, value, options]) => [name as string, { value, options }]));
}

describe("session cookies", () => {
  beforeEach(() => {
    set.mockReset();
    incoming.clear();
  });
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  describe("with Secure cookies (production): __Host- names", () => {
    beforeEach(() => {
      vi.stubEnv("SESSION_COOKIE_SECURE", "1");
    });

    it("cookieSecurity is exactly what a __Host- cookie requires: Secure, Path=/, no Domain", () => {
      const options = cookieSecurity();
      expect(options).toMatchObject({ secure: true, path: "/", httpOnly: true, sameSite: "lax" });
      expect(options).not.toHaveProperty("domain");
    });

    it("writes the session under __Host- names, with no Domain and Path=/", async () => {
      await writeSession({ access: "a", refresh: "r" });
      await writeOrganizationId("org-1");

      const written = setCookies();
      for (const name of ["__Host-eb_at", "__Host-eb_rt", "__Host-eb_org"]) {
        const cookie = written.get(name);
        expect(cookie, name).toBeDefined();
        expect(cookie?.options).toMatchObject({ secure: true, path: "/", httpOnly: true, sameSite: "lax" });
        expect(cookie?.options).not.toHaveProperty("domain");
        expect(cookie?.options.maxAge).toBeGreaterThan(0);
      }
      expect(written.get("__Host-eb_at")?.value).toBe("a");
      expect(written.get("__Host-eb_rt")?.value).toBe("r");
      expect(written.get("__Host-eb_org")?.value).toBe("org-1");
    });

    it("expires what an earlier version left under the plain names when a session is written", async () => {
      await writeSession({ access: "a", refresh: "r" });
      await writeOrganizationId("org-1");

      const written = setCookies();
      for (const name of ["eb_at", "eb_rt", "eb_org"]) {
        expect(written.get(name)?.value, name).toBe("");
        expect(written.get(name)?.options, name).toMatchObject({ maxAge: 0, secure: true, path: "/" });
      }
    });

    it("reads ONLY the prefixed cookies: a duplicate under the plain name is ignored", async () => {
      // A compromised sibling subdomain can set Domain=.workeasy360.com cookies under plain names. It
      // cannot set a __Host- one.
      incoming.set("eb_at", "planted-access");
      incoming.set("eb_rt", "planted-refresh");
      incoming.set("eb_org", "planted-org");
      expect(await readSession()).toBeNull();
      expect(await readOrganizationId()).toBeNull();

      incoming.set("__Host-eb_at", "real-access");
      incoming.set("__Host-eb_rt", "real-refresh");
      expect(await readSession()).toEqual({ access: "real-access", refresh: "real-refresh", organizationId: null });

      incoming.set("__Host-eb_org", "real-org");
      expect(await readSession()).toEqual({
        access: "real-access",
        refresh: "real-refresh",
        organizationId: "real-org",
      });
      expect(await readOrganizationId()).toBe("real-org");
    });

    it("clearSession expires every session cookie, prefixed and plain, with the attributes they were set with", async () => {
      await clearSession();

      const cleared = setCookies();
      expect([...cleared.keys()].sort()).toEqual(
        ["__Host-eb_at", "__Host-eb_org", "__Host-eb_rt", "eb_at", "eb_org", "eb_rt"].sort(),
      );
      for (const { value, options } of cleared.values()) {
        expect(value).toBe("");
        expect(options).toMatchObject({ maxAge: 0, httpOnly: true, secure: true, sameSite: "lax", path: "/" });
        expect(options).not.toHaveProperty("domain");
      }
    });

    it("markSignedOut leaves a __Host- SESSION cookie: no Max-Age or Expires, no Domain, Path=/", async () => {
      await markSignedOut();

      expect(set).toHaveBeenCalledTimes(1);
      const [name, value, options] = set.mock.calls[0] ?? [];
      expect(name).toBe("__Host-eb_signed_out");
      expect(value).toBe("1");
      expect(options).toMatchObject({ httpOnly: true, secure: true, sameSite: "lax", path: "/" });
      expect(options).not.toHaveProperty("maxAge");
      expect(options).not.toHaveProperty("expires");
      expect(options).not.toHaveProperty("domain");
    });

    it("a sign-in clears the sign-out marker and ONLY that: the loop guard is left to expire", async () => {
      await clearSignedOutMarker();

      const cleared = setCookies();
      expect([...cleared.keys()]).toEqual(["__Host-eb_signed_out"]);
      expect(cleared.get("__Host-eb_signed_out")?.value).toBe("");
      expect(cleared.get("__Host-eb_signed_out")?.options).toMatchObject({
        maxAge: 0,
        httpOnly: true,
        secure: true,
        sameSite: "lax",
        path: "/",
      });
      expect([...cleared.keys()].some((name) => name.includes("sso_auto"))).toBe(false);
    });
  });

  describe("without Secure (local development over http): plain names", () => {
    beforeEach(() => {
      vi.stubEnv("SESSION_COOKIE_SECURE", "0");
    });

    it("writes, reads and clears under the plain names, and touches nothing else", async () => {
      await writeSession({ access: "a", refresh: "r" });
      await writeOrganizationId("org-1");
      expect([...setCookies().keys()].sort()).toEqual(["eb_at", "eb_org", "eb_rt"]);
      expect(cookieSecurity().secure).toBe(false);

      incoming.set("eb_at", "a");
      incoming.set("eb_rt", "r");
      incoming.set("eb_org", "org-1");
      expect(await readSession()).toEqual({ access: "a", refresh: "r", organizationId: "org-1" });

      set.mockReset();
      await clearSession();
      expect([...setCookies().keys()].sort()).toEqual(["eb_at", "eb_org", "eb_rt"]);
      for (const { value, options } of setCookies().values()) {
        expect(value).toBe("");
        expect(options).toMatchObject({ maxAge: 0, httpOnly: true, secure: false, sameSite: "lax", path: "/" });
      }
    });

    it("does not read a __Host- cookie it would never have written", async () => {
      incoming.set("__Host-eb_at", "a");
      incoming.set("__Host-eb_rt", "r");
      expect(await readSession()).toBeNull();
    });

    it("the sign-out marker keeps its plain name and is still a session cookie", async () => {
      await markSignedOut();
      const [name, , options] = set.mock.calls[0] ?? [];
      expect(name).toBe("eb_signed_out");
      expect(options).not.toHaveProperty("maxAge");

      set.mockReset();
      await clearSignedOutMarker();
      expect([...setCookies().keys()]).toEqual(["eb_signed_out"]);
    });
  });
});
