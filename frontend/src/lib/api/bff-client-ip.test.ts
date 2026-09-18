// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Regression (phase 12 P0 remediation): Django only ever saw this server's
// address, so every browser user shared one anonymous rate-limit bucket. The
// BFF and the sign-in path must forward the browser's address, with the proxy
// secret, on every upstream call.

const callUpstream = vi.fn();
vi.mock("server-only", () => ({}));
vi.mock("@/lib/api/upstream", () => ({
  callUpstream: (request: unknown) => callUpstream(request),
  decodeJson: () => null,
}));
vi.mock("@/lib/auth/session", async () => ({
  ACCESS_COOKIE: "eb_at",
  REFRESH_COOKIE: "eb_rt",
  ORG_COOKIE: "eb_org",
  ORG_MAX_AGE_SECONDS: 60,
  cookieSecurity: () => ({ httpOnly: true, sameSite: "lax", secure: false, path: "/" }),
  writeSession: vi.fn(),
  writeOrganizationId: vi.fn(),
}));

const { GET } = await import("@/app/api/bff/[...path]/route");
const { signInWithPassword } = await import("@/lib/auth/sign-in");

function upstreamOk() {
  return { status: 200, headers: new Headers(), body: new ArrayBuffer(0), requestId: null };
}

describe("client address forwarding", () => {
  beforeEach(() => {
    vi.stubEnv("TRUSTED_PROXY_COUNT", "1");
    vi.stubEnv("BFF_PROXY_SECRET", "shared-secret");
    callUpstream.mockReset();
    callUpstream.mockResolvedValue(upstreamOk());
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("the BFF proxy forwards the browser's address", async () => {
    const request = new Request("http://books.example.com/api/bff/invoices", {
      headers: {
        cookie: "eb_at=access; eb_rt=refresh; eb_org=org-1",
        "x-forwarded-for": "198.51.100.9, 203.0.113.7",
      },
    });
    await GET(request, { params: Promise.resolve({ path: ["invoices"] }) });

    expect(callUpstream).toHaveBeenCalled();
    const upstreamRequest = callUpstream.mock.calls[0]![0] as { clientIp?: string };
    expect(upstreamRequest.clientIp).toBe("203.0.113.7");
  });

  it("sign-in forwards the browser's address", async () => {
    callUpstream.mockResolvedValue({ status: 401, headers: new Headers(), body: new ArrayBuffer(0), requestId: null });
    await signInWithPassword("someone@example.com", "wrong-password", "203.0.113.7");

    const upstreamRequest = callUpstream.mock.calls[0]![0] as { path: string; clientIp?: string };
    expect(upstreamRequest.path).toBe("/auth/login/");
    expect(upstreamRequest.clientIp).toBe("203.0.113.7");
  });
});
