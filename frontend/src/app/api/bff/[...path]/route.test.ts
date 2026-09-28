// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";

const callUpstream = vi.fn();
vi.mock("server-only", () => ({}));
vi.mock("@/lib/api/upstream", () => ({ callUpstream, decodeJson: vi.fn() }));
vi.mock("@/lib/auth/refresh", () => ({ refreshTokens: vi.fn() }));

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
