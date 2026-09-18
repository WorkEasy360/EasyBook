// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const { callUpstream } = await import("./upstream");

describe("callUpstream client address", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubEnv("BFF_PROXY_SECRET", "shared-secret");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  function sentHeaders(): Headers {
    const init = fetchMock.mock.calls[0]![1] as RequestInit;
    return new Headers(init.headers);
  }

  it("asserts the browser's address with the proxy secret, keeping caller headers", async () => {
    await callUpstream({ path: "/auth/login/", method: "POST", headers: { "Content-Type": "application/json" }, clientIp: "203.0.113.7" });
    const headers = sentHeaders();
    expect(headers.get("x-easybook-client-ip")).toBe("203.0.113.7");
    expect(headers.get("x-easybook-proxy-auth")).toBe("shared-secret");
    expect(headers.get("content-type")).toBe("application/json");
  });

  it("sends no assertion when there is no address", async () => {
    await callUpstream({ path: "/auth/login/", method: "POST" });
    const headers = sentHeaders();
    expect(headers.has("x-easybook-client-ip")).toBe(false);
    expect(headers.has("x-easybook-proxy-auth")).toBe(false);
  });
});
