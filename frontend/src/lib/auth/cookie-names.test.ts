// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { cookieName, cookiesAreSecure, plainCookieName, type AuthCookie } from "./cookie-names";

const ALL: AuthCookie[] = ["access", "refresh", "organization", "ssoFlow", "signedOut", "autoStart"];
const PLAIN = { access: "eb_at", refresh: "eb_rt", organization: "eb_org", ssoFlow: "eb_sso", signedOut: "eb_signed_out", autoStart: "eb_sso_auto" };

describe("auth cookie names", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("keeps the plain names without Secure (local development over http)", () => {
    vi.stubEnv("SESSION_COOKIE_SECURE", "0");
    expect(cookiesAreSecure()).toBe(false);
    for (const which of ALL) expect(cookieName(which)).toBe(PLAIN[which]);
  });

  it("keeps the plain names when the setting is absent", () => {
    vi.stubEnv("SESSION_COOKIE_SECURE", "");
    for (const which of ALL) expect(cookieName(which)).toBe(PLAIN[which]);
  });

  it("puts __Host- on EVERY auth cookie when cookies are Secure", () => {
    vi.stubEnv("SESSION_COOKIE_SECURE", "1");
    expect(cookiesAreSecure()).toBe(true);
    for (const which of ALL) {
      expect(cookieName(which)).toBe(`__Host-${PLAIN[which]}`);
      // The unprefixed name is only ever for expiring leftovers, never for reading.
      expect(plainCookieName(which)).toBe(PLAIN[which]);
    }
  });

  it("follows the environment at call time, not at import", () => {
    vi.stubEnv("SESSION_COOKIE_SECURE", "1");
    expect(cookieName("access")).toBe("__Host-eb_at");
    vi.stubEnv("SESSION_COOKIE_SECURE", "0");
    expect(cookieName("access")).toBe("eb_at");
  });

  it("gives every cookie a name of its own", () => {
    vi.stubEnv("SESSION_COOKIE_SECURE", "1");
    expect(new Set(ALL.map(cookieName)).size).toBe(ALL.length);
  });
});
