// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { CLIENT_IP_HEADER, PROXY_AUTH_HEADER, clientIpFromHeaders, forwardedClientHeaders } from "./client-ip";

function forwardedFor(value: string): Headers {
  return new Headers({ "x-forwarded-for": value });
}

describe("clientIpFromHeaders", () => {
  it("takes the address the trusted proxy appended, never the client-written prefix", () => {
    expect(clientIpFromHeaders(forwardedFor("1.2.3.4, 5.6.7.8, 203.0.113.7"), 1)).toBe("203.0.113.7");
    expect(clientIpFromHeaders(forwardedFor("1.2.3.4, 203.0.113.7, 10.0.0.2"), 2)).toBe("203.0.113.7");
  });

  it("forwards nothing when no proxy is trusted", () => {
    expect(clientIpFromHeaders(forwardedFor("203.0.113.7"), 0)).toBeNull();
  });

  it("forwards nothing without a forwarded-for header", () => {
    expect(clientIpFromHeaders(new Headers(), 1)).toBeNull();
  });

  it("never forwards something that is not an IP address", () => {
    expect(clientIpFromHeaders(forwardedFor("evil, not-an-ip"), 1)).toBeNull();
    expect(clientIpFromHeaders(forwardedFor("999.1.1.1"), 1)).toBeNull();
  });

  it("accepts IPv6", () => {
    expect(clientIpFromHeaders(forwardedFor("2001:db8::1"), 1)).toBe("2001:db8::1");
  });
});

describe("forwardedClientHeaders", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("asserts the address together with the proxy secret", () => {
    vi.stubEnv("BFF_PROXY_SECRET", "shared-secret");
    expect(forwardedClientHeaders("203.0.113.7")).toEqual({
      [CLIENT_IP_HEADER]: "203.0.113.7",
      [PROXY_AUTH_HEADER]: "shared-secret",
    });
  });

  it("sends nothing without a secret or without an address", () => {
    vi.stubEnv("BFF_PROXY_SECRET", "");
    expect(forwardedClientHeaders("203.0.113.7")).toEqual({});
    vi.stubEnv("BFF_PROXY_SECRET", "shared-secret");
    expect(forwardedClientHeaders(null)).toEqual({});
  });
});
