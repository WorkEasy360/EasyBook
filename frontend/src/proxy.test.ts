// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NetworkError, TimeoutError } from "@/lib/api/errors";

const refreshTokens = vi.fn();
const ssoOnly = vi.fn<() => Promise<boolean>>();
/** The browser's cookies for the tests that sign out and in through the real session.ts writers. */
const jar = new Map<string, string>();
vi.mock("server-only", () => ({}));
vi.mock("@/lib/auth/refresh", () => ({ refreshTokens: (token: string) => refreshTokens(token) }));
vi.mock("@/lib/auth/sso-only", () => ({ isSsoOnly: () => ssoOnly() }));
vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => (jar.has(name) ? { name, value: jar.get(name) } : undefined),
    set: (name: string, value: string, options?: { maxAge?: number }) => {
      if (options?.maxAge === 0) jar.delete(name);
      else jar.set(name, value);
    },
  }),
}));

const { config, proxy } = await import("./proxy");
const { clearSignedOutMarker, markSignedOut } = await import("@/lib/auth/session");

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

describe("proxy: opened from the Workeasy360 launcher", () => {
  const organization = "7b0f4f7e-2a3c-4f4e-9a59-3f5c1b2d4e6a";

  it("sends a signed-out visitor to Workeasy360 sign-in for that organization", async () => {
    const response = await proxy(pageRequest(`/?organization=${organization}`));
    const location = new URL(response.headers.get("location") ?? "");
    expect(response.status).toBe(307);
    expect(location.pathname).toBe("/api/auth/workeasy360/start");
    expect(location.searchParams.get("organization")).toBe(organization);
    expect(location.searchParams.has("next")).toBe(false);
  });

  it("re-runs sign-in even with a session, keeping a deep link's path", async () => {
    const response = await proxy(
      pageRequest(`/sales/invoices?organization=${organization}`, { ...expiringSession, eb_at: tokenExpiringIn(3600) }),
    );
    const location = new URL(response.headers.get("location") ?? "");
    expect(location.pathname).toBe("/api/auth/workeasy360/start");
    expect(location.searchParams.get("next")).toBe("/sales/invoices");
  });

  it("ignores an organization value that is not a UUID", async () => {
    const response = await proxy(pageRequest("/login?organization=..%2F..%2Fevil"));
    expect(response.headers.get("location")).toBeNull();
  });
});

/** A browser loading a page: what Chromium, Firefox and Safari send for a typed address. */
const NAVIGATION = { "sec-fetch-mode": "navigate", "sec-fetch-dest": "document", accept: "text/html,application/xhtml+xml" };

function visit(path: string, cookies: Record<string, string> = {}, extra: Record<string, string> = {}): NextRequest {
  const cookie = Object.entries(cookies)
    .map(([name, value]) => `${name}=${value}`)
    .join("; ");
  return new NextRequest(`http://localhost:3000${path}`, {
    headers: { ...NAVIGATION, ...(cookie ? { cookie } : {}), ...extra },
  });
}

const START = "/api/auth/workeasy360/start";

function locationOf(response: Response): URL {
  return new URL(response.headers.get("location") ?? "", "http://localhost:3000");
}

/** `name=value` pairs a browser would keep from a response's Set-Cookie headers. */
function keptCookies(response: Response): Record<string, string> {
  const kept: Record<string, string> = {};
  for (const header of response.headers.getSetCookie()) {
    const [pair = ""] = header.split(";");
    const index = pair.indexOf("=");
    if (!/Max-Age=0/i.test(header)) kept[pair.slice(0, index)] = pair.slice(index + 1);
  }
  return kept;
}

describe("proxy: someone opens EasyBook directly (SSO-only, integration guide §6b)", () => {
  const liveSession = () => ({ eb_at: tokenExpiringIn(3600), eb_rt: "refresh-token", eb_org: "org-1" });

  beforeEach(() => {
    refreshTokens.mockReset();
    ssoOnly.mockReset().mockResolvedValue(true);
    jar.clear();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  describe("start, do not ask", () => {
    it("sends a page with no session straight to Workeasy360 sign-in, returning to that page", async () => {
      const before = Date.now();
      const response = await proxy(visit("/sales/invoices?page=2"));

      expect(response.status).toBe(307);
      const location = locationOf(response);
      expect(location.pathname).toBe(START);
      expect(location.searchParams.get("next")).toBe("/sales/invoices?page=2");
      // No organization hint (that is the launcher's), and the sign-in page is not in between.
      expect(location.searchParams.has("organization")).toBe(false);
      expect(response.headers.get("location")).not.toContain("/login");
      expect(response.headers.get("cache-control")).toBe("no-store");

      // The loop guard, set by this very redirect: a 60 s window.
      const guard = response.headers.getSetCookie().find((header) => header.startsWith("eb_sso_auto="));
      expect(guard).toMatch(/Max-Age=60/i);
      expect(guard).toMatch(/HttpOnly/i);
      expect(guard).toMatch(/SameSite=lax/i);
      expect(guard).toMatch(/Path=\//);
      const startedAt = Number(keptCookies(response).eb_sso_auto);
      expect(startedAt).toBeGreaterThanOrEqual(before);
      expect(startedAt).toBeLessThanOrEqual(Date.now());
    });

    it("starts from the home page with nothing to return to", async () => {
      const location = locationOf(await proxy(visit("/")));
      expect(location.pathname).toBe(START);
      expect(location.search).toBe("");
    });

    it("sends the person to /login, as before, when EasyBook's password sign-in is on", async () => {
      ssoOnly.mockResolvedValue(false);

      const response = await proxy(visit("/sales/invoices?page=2"));

      const location = locationOf(response);
      expect(location.pathname).toBe("/login");
      expect(location.searchParams.get("next")).toBe("/sales/invoices?page=2");
      expect(response.headers.getSetCookie()).toEqual([]);
      expect(locationOf(await proxy(visit("/"))).pathname).toBe("/login");
    });
  });

  describe("never by itself", () => {
    it.each([
      "/login",
      "/login?next=%2Fsales%2Finvoices",
      "/login?sso_error=failed",
      "/login?sso_error=expired",
      "/login?reason=expired",
      "/login?method=password",
      "/register",
      "/forgot-password",
    ])("does not start from the sign-in page %s", async (path) => {
      const response = await proxy(visit(path));

      expect(response.status).toBe(200);
      expect(response.headers.get("location")).toBeNull();
      expect(response.headers.getSetCookie()).toEqual([]);
      expect(ssoOnly).not.toHaveBeenCalled();
    });

    it("does not start from a sign-in page for a visitor whose cookies are dead", async () => {
      // /login?reason=expired is where a rejected session ends; it must stay a page, not a hop.
      const response = await proxy(visit("/login?reason=expired", { eb_at: "dead" }));
      expect(response.status).toBe(200);
      expect(ssoOnly).not.toHaveBeenCalled();
    });

    it.each([
      ["a deep page", "/sales/invoices?page=2"],
      ["the home page", "/"],
    ])("shows the sign-in page after a sign-out of EasyBook in this browser: %s", async (_label, path) => {
      const response = await proxy(visit(path, { eb_signed_out: "1" }));

      expect(locationOf(response).pathname).toBe("/login");
      expect(ssoOnly).not.toHaveBeenCalled();
      expect(response.headers.getSetCookie()).toEqual([]);
    });

    it("keeps the sign-in page until the person signs in again", async () => {
      // "Sign out of EasyBook" and "...and Workeasy360" both go through POST /api/auth/logout, which leaves the marker.
      expect(locationOf(await proxy(visit("/"))).pathname).toBe(START);

      await markSignedOut();
      expect(jar.get("eb_signed_out")).toBe("1");
      expect(locationOf(await proxy(visit("/", Object.fromEntries(jar)))).pathname).toBe("/login");
      expect(locationOf(await proxy(visit("/sales/invoices", Object.fromEntries(jar)))).pathname).toBe("/login");

      // A successful sign-in clears it; the next visit with no session starts again.
      await clearSignedOutMarker();
      expect(jar.size).toBe(0);
      expect(locationOf(await proxy(visit("/", Object.fromEntries(jar)))).pathname).toBe(START);
    });

    it("keeps the loop guard through a sign-in: only the sign-out marker is cleared", async () => {
      // A browser that drops the session it was just given must still be caught by the guard.
      jar.set("eb_signed_out", "1");
      jar.set("eb_sso_auto", String(Date.now()));

      await clearSignedOutMarker();

      expect([...jar.keys()]).toEqual(["eb_sso_auto"]);
      expect(locationOf(await proxy(visit("/sales/invoices", Object.fromEntries(jar)))).pathname).toBe("/login");
      expect(ssoOnly).not.toHaveBeenCalled();
    });

    it("does not start again within 60 seconds of starting, and does after", async () => {
      vi.useFakeTimers({ toFake: ["Date"] });
      vi.setSystemTime(new Date("2026-10-12T10:00:00Z"));

      const first = await proxy(visit("/sales/invoices"));
      expect(locationOf(first).pathname).toBe(START);
      const cookies = keptCookies(first);
      expect(cookies).toHaveProperty("eb_sso_auto");

      // Back with no session (a dropped cookie, a failure not reported as ?sso_error=): the sign-in page.
      vi.setSystemTime(new Date("2026-10-12T10:00:10Z"));
      const second = await proxy(visit("/sales/invoices", cookies));
      expect(locationOf(second).pathname).toBe("/login");
      expect(locationOf(second).searchParams.get("next")).toBe("/sales/invoices");
      expect(ssoOnly).toHaveBeenCalledTimes(1);

      vi.setSystemTime(new Date("2026-10-12T10:00:59.999Z"));
      expect(locationOf(await proxy(visit("/sales/invoices", cookies))).pathname).toBe("/login");

      vi.setSystemTime(new Date("2026-10-12T10:01:00Z"));
      expect(locationOf(await proxy(visit("/sales/invoices", cookies))).pathname).toBe(START);
    });

    it("reads a loop-guard cookie it cannot parse as recent", async () => {
      const response = await proxy(visit("/", { eb_sso_auto: "garbage" }));
      expect(locationOf(response).pathname).toBe("/login");
    });
  });

  describe("only page navigations", () => {
    it("leaves API routes out of the proxy altogether", () => {
      const matcher = new RegExp(`^${config.matcher[0]}$`);
      for (const path of [
        "/api/auth/workeasy360/start",
        "/api/auth/callback/workeasy360",
        "/api/auth/logout",
        "/api/bff/sales/invoices",
        "/api/health",
        "/_next/static/chunks/app.js",
        "/favicon.ico",
      ]) {
        expect(matcher.test(path), path).toBe(false);
      }
      for (const path of ["/", "/dashboard", "/sales/invoices", "/login"]) {
        expect(matcher.test(path), path).toBe(true);
      }
    });

    it("never starts a sign-in for an API path, even if one reached the proxy", async () => {
      for (const path of ["/api/bff/sales/invoices", "/api/auth/logout", "/api"]) {
        const response = await proxy(visit(path));
        expect(locationOf(response).pathname, path).not.toBe(START);
        expect(response.headers.getSetCookie(), path).toEqual([]);
      }
      expect(ssoOnly).not.toHaveBeenCalled();
    });

    it.each([
      ["a fetch()", { "sec-fetch-mode": "cors", "sec-fetch-dest": "empty", accept: "application/json" }],
      ["a same-origin fetch()", { "sec-fetch-mode": "same-origin", "sec-fetch-dest": "empty", accept: "*/*" }],
      ["a Next client-side navigation", { rsc: "1", "sec-fetch-mode": "cors", "sec-fetch-dest": "empty" }],
      ["a Next link prefetch", { rsc: "1", "next-router-prefetch": "1", "sec-fetch-mode": "cors" }],
      ["a browser prefetch", { "sec-purpose": "prefetch", accept: "text/html" }],
      ["a frame", { "sec-fetch-mode": "navigate", "sec-fetch-dest": "iframe", accept: "text/html" }],
      ["a request that is not a browser's", { accept: "*/*" }],
    ])("does not start a sign-in for %s", async (_label, headers) => {
      const response = await proxy(new NextRequest("http://localhost:3000/sales/invoices", { headers }));

      // The answer it always was: the sign-in page. Never the start route, and no loop-guard cookie.
      expect(locationOf(response).pathname).toBe("/login");
      expect(response.headers.getSetCookie()).toEqual([]);
      expect(ssoOnly).not.toHaveBeenCalled();
    });

    it("does not start a sign-in for a POST", async () => {
      const response = await proxy(
        new NextRequest("http://localhost:3000/sales/invoices", { method: "POST", headers: NAVIGATION }),
      );
      expect(locationOf(response).pathname).toBe("/login");
      expect(ssoOnly).not.toHaveBeenCalled();
    });

    it("recognises a navigation by Accept alone where the browser sends no Sec-Fetch headers", async () => {
      const response = await proxy(
        new NextRequest("http://localhost:3000/sales/invoices", { headers: { accept: "text/html,*/*;q=0.8" } }),
      );
      expect(locationOf(response).pathname).toBe(START);
    });
  });

  describe("next is a same-site path or nothing", () => {
    it.each([
      ["a protocol-relative path", "//evil.example/dashboard"],
      ["a backslash path", "/\\evil.example/dashboard"],
      ["an encoded backslash path", "/%5Cevil.example"],
    ])("never forwards %s", async (_label, path) => {
      const response = await proxy(visit(path));

      const location = locationOf(response);
      expect(location.origin).toBe("http://localhost:3000");
      expect(location.pathname).toBe(START);
      for (const value of location.searchParams.getAll("next")) {
        expect(value.startsWith("/")).toBe(true);
        expect(value.startsWith("//")).toBe(false);
        expect(value).not.toContain("\\");
      }
    });

    it("drops a next it cannot vouch for and still starts", async () => {
      const location = locationOf(await proxy(visit("//evil.example/dashboard")));
      expect(location.pathname).toBe(START);
      expect(location.searchParams.has("next")).toBe(false);
    });

    it("keeps a hostile ?next= of the page inside the encoded path it forwards", async () => {
      const location = locationOf(await proxy(visit("/sales/invoices?next=https://evil.example")));
      expect(location.pathname).toBe(START);
      expect(location.searchParams.getAll("next")).toEqual(["/sales/invoices?next=https://evil.example"]);
    });
  });

  describe("a session is never replaced", () => {
    it.each([
      "/dashboard",
      "/sales/invoices",
      "/access-ended",
      "/access-ended?reason=membership_inactive",
      "/access-ended?reason=no_seat",
      "/access-ended?reason=unverified",
      "/access-ended?reason=sign_in_required",
    ])("lets a person with a session through to %s (access that ended keeps its explanation)", async (path) => {
      const response = await proxy(visit(path, liveSession()));

      expect(response.status).toBe(200);
      expect(response.headers.get("location")).toBeNull();
      expect(response.headers.getSetCookie()).toEqual([]);
      expect(ssoOnly).not.toHaveBeenCalled();
    });

    it("lets a session through while the backend is down, instead of starting a sign-in", async () => {
      refreshTokens.mockRejectedValue(new NetworkError("Could not reach EasyBook."));
      const response = await proxy(visit("/dashboard", { eb_at: tokenExpiringIn(10), eb_rt: "r", eb_org: "org-1" }));
      expect(response.status).toBe(200);
      expect(ssoOnly).not.toHaveBeenCalled();
    });

    it("ends a session the backend rejects at the sign-in page, which never starts", async () => {
      refreshTokens.mockResolvedValue(null);

      const response = await proxy(visit("/dashboard", { eb_at: tokenExpiringIn(10), eb_rt: "r", eb_org: "org-1" }));

      const location = locationOf(response);
      expect(location.pathname).toBe("/login");
      expect(location.searchParams.get("reason")).toBe("expired");
      expect(ssoOnly).not.toHaveBeenCalled();
      // ...and following that redirect stays on the page.
      expect((await proxy(visit("/login?reason=expired"))).status).toBe(200);
    });
  });

  describe("the Workeasy360 launcher", () => {
    it("still starts for the chosen organization, even after a sign-out", async () => {
      const organization = "7b0f4f7e-2a3c-4f4e-9a59-3f5c1b2d4e6a";
      const location = locationOf(await proxy(visit(`/?organization=${organization}`, { eb_signed_out: "1" })));
      expect(location.pathname).toBe(START);
      expect(location.searchParams.get("organization")).toBe(organization);
    });
  });
});

describe("proxy: __Host- cookie names when cookies are Secure (production)", () => {
  /** The browser has the host-only, Secure, __Host- cookies this server set. */
  const hostSession = () => ({ "__Host-eb_at": tokenExpiringIn(3600), "__Host-eb_rt": "refresh-token", "__Host-eb_org": "org-1" });
  /** What a compromised sibling subdomain can plant with Domain=.workeasy360.com: plain names only. */
  const planted = () => ({ eb_at: tokenExpiringIn(3600), eb_rt: "attackers-refresh", eb_org: "attackers-org" });

  beforeEach(() => {
    vi.stubEnv("SESSION_COOKIE_SECURE", "1");
    refreshTokens.mockReset();
    ssoOnly.mockReset().mockResolvedValue(true);
    jar.clear();
  });
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("recognises a session by its __Host- cookies", async () => {
    const response = await proxy(visit("/dashboard", hostSession()));
    expect(response.status).toBe(200);
    expect(response.headers.get("location")).toBeNull();
    expect(ssoOnly).not.toHaveBeenCalled();
  });

  it("does not take cookies under the plain names for a session", async () => {
    // A sibling-planted (or stale) eb_at / eb_rt pair is simply not a session: the visitor has none.
    ssoOnly.mockResolvedValue(false);
    const response = await proxy(visit("/dashboard", planted()));
    expect(locationOf(response).pathname).toBe("/login");
    expect(refreshTokens).not.toHaveBeenCalled();
  });

  it("prefers the __Host- cookies when a plain-named duplicate rides along", async () => {
    // Both in one Cookie header, planted one LAST - the order a duplicate-keeping parser would pick.
    const cookie = { ...hostSession(), ...planted() };
    const response = await proxy(visit("/dashboard", cookie));
    expect(response.status).toBe(200);
    expect(refreshTokens).not.toHaveBeenCalled();
  });

  it("refreshes the __Host- session and writes the rotated pair under __Host- names, Secure, Path=/, no Domain", async () => {
    refreshTokens.mockResolvedValue({ access: tokenExpiringIn(900), refresh: "rotated-refresh" });

    const response = await proxy(
      visit("/dashboard", { "__Host-eb_at": tokenExpiringIn(10), "__Host-eb_rt": "old-refresh", "__Host-eb_org": "org-1", ...planted() }),
    );

    expect(refreshTokens).toHaveBeenCalledWith("old-refresh");
    const headers = response.headers.getSetCookie();
    for (const name of ["__Host-eb_at", "__Host-eb_rt"]) {
      const header = headers.find((value) => value.startsWith(`${name}=`));
      expect(header, name).toBeDefined();
      expect(header).toMatch(/; Secure/i);
      expect(header).toMatch(/; Path=\//);
      expect(header).toMatch(/; HttpOnly/i);
      expect(header).not.toMatch(/Domain=/i);
    }
    expect(headers.some((value) => /^eb_(at|rt)=/.test(value))).toBe(false);
  });

  it("ends a rejected session by clearing the __Host- cookies", async () => {
    refreshTokens.mockResolvedValue(null);
    const response = await proxy(visit("/dashboard", { ...hostSession(), "__Host-eb_at": tokenExpiringIn(10) }));
    expect(clearedCookies(response).sort()).toEqual(["__Host-eb_at", "__Host-eb_org", "__Host-eb_rt"]);
  });

  it("auto-starts for a visitor holding only planted plain-named cookies, and sets the guard under its __Host- name", async () => {
    const response = await proxy(visit("/sales/invoices", planted()));

    expect(locationOf(response).pathname).toBe("/api/auth/workeasy360/start");
    const guard = response.headers.getSetCookie().find((value) => value.startsWith("__Host-eb_sso_auto="));
    expect(guard).toBeDefined();
    expect(guard).toMatch(/; Secure/i);
    expect(guard).toMatch(/; Path=\//);
    expect(guard).toMatch(/Max-Age=60/i);
    expect(guard).not.toMatch(/Domain=/i);
    expect(response.headers.getSetCookie().some((value) => value.startsWith("eb_sso_auto="))).toBe(false);
  });

  it("honours the __Host- sign-out marker and loop guard, and ignores the plain-named ones", async () => {
    expect(locationOf(await proxy(visit("/", { "__Host-eb_signed_out": "1" }))).pathname).toBe("/login");
    expect(locationOf(await proxy(visit("/", { "__Host-eb_sso_auto": String(Date.now()) }))).pathname).toBe("/login");

    // A sibling planting the plain marker names cannot suppress (or fake) anything.
    const response = await proxy(visit("/", { eb_signed_out: "1", eb_sso_auto: String(Date.now()) }));
    expect(locationOf(response).pathname).toBe("/api/auth/workeasy360/start");
  });
});
