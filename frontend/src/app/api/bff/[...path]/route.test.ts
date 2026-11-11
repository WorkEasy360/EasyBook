// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const callUpstream = vi.fn();
const refreshTokens = vi.fn();
vi.mock("server-only", () => ({}));
vi.mock("@/lib/api/upstream", () => ({ callUpstream, decodeJson: vi.fn() }));
vi.mock("@/lib/auth/refresh", () => ({ refreshTokens }));

const { GET } = await import("./route");

const SIGNED_IN = "eb_at=access-token; eb_rt=refresh-token; eb_org=org-1";

function request(rawPath: string): { req: Request; ctx: { params: Promise<{ path: string[] }> } } {
  // Next hands the catch-all route each segment decoded exactly once.
  const segments = rawPath.split("/").map((segment) => decodeURIComponent(segment));
  return {
    req: new Request(`https://app.example.test/api/bff/${rawPath}`, { headers: { Cookie: SIGNED_IN } }),
    ctx: { params: Promise.resolve({ path: segments }) },
  };
}

describe("BFF proxy path containment", () => {
  beforeEach(() => {
    callUpstream.mockReset();
    callUpstream.mockResolvedValue({ status: 200, headers: new Headers(), body: new ArrayBuffer(0), requestId: null });
  });

  it.each([
    ["../admin"],
    [".%2e/.%2e/admin"],
    [".%252e/.%252e/admin"],
    ["%252e%252e/%252e%252e/admin"],
    ["..%5cadmin"],
    ["accounts/..%2f..%2fadmin"],
  ])("refuses %s with 404 and never contacts Django", async (rawPath) => {
    const { req, ctx } = request(rawPath);
    const response = await GET(req, ctx);
    expect(response.status).toBe(404);
    expect(callUpstream).not.toHaveBeenCalled();
  });

  it("forwards a normal API path with the session's token", async () => {
    const { req, ctx } = request("accounting/fiscal-years");
    const response = await GET(req, ctx);
    expect(response.status).toBe(200);
    expect(callUpstream).toHaveBeenCalledTimes(1);
    const call = callUpstream.mock.calls[0]?.[0] as { path: string; headers: Headers | Record<string, string> };
    expect(call.path).toBe("/accounting/fiscal-years/");
    const headers = new Headers(call.headers as HeadersInit);
    expect(headers.get("Authorization")).toBe("Bearer access-token");
  });
});

describe("BFF proxy session cookies when cookies are Secure (production)", () => {
  const upstreamOk = { status: 200, headers: new Headers(), body: new ArrayBuffer(0), requestId: null };
  const get = (cookie: string) =>
    GET(new Request("https://app.example.test/api/bff/accounting/fiscal-years", { headers: { Cookie: cookie } }), {
      params: Promise.resolve({ path: ["accounting", "fiscal-years"] }),
    });
  const authorizationOf = (call: unknown) =>
    new Headers((call as { headers: HeadersInit }).headers).get("Authorization");

  beforeEach(() => {
    vi.stubEnv("SESSION_COOKIE_SECURE", "1");
    callUpstream.mockReset();
    refreshTokens.mockReset();
    callUpstream.mockResolvedValue(upstreamOk);
  });
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("reads the __Host- cookies and ignores a plain-named duplicate planted by a sibling subdomain", async () => {
    const response = await get(
      "__Host-eb_at=access-token; __Host-eb_rt=refresh-token; __Host-eb_org=org-1; eb_at=planted; eb_rt=planted; eb_org=planted-org",
    );

    expect(response.status).toBe(200);
    expect(authorizationOf(callUpstream.mock.calls[0]?.[0])).toBe("Bearer access-token");
  });

  it("takes plain-named cookies alone for no session", async () => {
    const response = await get("eb_at=planted; eb_rt=planted; eb_org=planted-org");
    expect(response.status).toBe(401);
    expect(callUpstream).not.toHaveBeenCalled();
  });

  it("writes a rotated pair back under __Host- names: Secure, Path=/, no Domain", async () => {
    callUpstream.mockResolvedValueOnce({ ...upstreamOk, status: 401 }).mockResolvedValueOnce(upstreamOk);
    refreshTokens.mockResolvedValue({ access: "new-access", refresh: "new-refresh" });

    const response = await get("__Host-eb_at=old; __Host-eb_rt=old-refresh; __Host-eb_org=org-1");

    const headers = response.headers.getSetCookie();
    for (const [name, value] of [["__Host-eb_at", "new-access"], ["__Host-eb_rt", "new-refresh"]]) {
      const header = headers.find((candidate) => candidate.startsWith(`${name}=`));
      expect(header, name).toContain(`${name}=${value}`);
      expect(header).toMatch(/; Secure/i);
      expect(header).toMatch(/; Path=\//);
      expect(header).toMatch(/; HttpOnly/i);
      expect(header).not.toMatch(/Domain=/i);
    }
    expect(headers.some((candidate) => /^eb_(at|rt)=/.test(candidate))).toBe(false);
  });

  it("clears the __Host- cookies when the refresh is refused", async () => {
    callUpstream.mockResolvedValueOnce({ ...upstreamOk, status: 401 });
    refreshTokens.mockResolvedValue(null);

    const response = await get("__Host-eb_at=old; __Host-eb_rt=old-refresh; __Host-eb_org=org-1");

    expect(response.status).toBe(401);
    const cleared = response.headers
      .getSetCookie()
      .filter((candidate) => /Max-Age=0/i.test(candidate))
      .map((candidate) => candidate.split("=")[0]);
    expect(cleared.sort()).toEqual(["__Host-eb_at", "__Host-eb_rt"]);
  });
});
