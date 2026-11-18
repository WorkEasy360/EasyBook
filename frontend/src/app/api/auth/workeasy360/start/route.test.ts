// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const callUpstream = vi.fn();
const cookieSets: Array<{ name: string; value: string; options: Record<string, unknown> }> = [];

vi.mock("server-only", () => ({}));
vi.mock("next/headers", () => ({
  cookies: async () => ({
    set: (name: string, value: string, options: Record<string, unknown>) => cookieSets.push({ name, value, options }),
  }),
}));
vi.mock("@/lib/api/upstream", () => ({
  callUpstream,
  decodeJson: (response: { json: unknown }) => response.json,
}));

const { GET } = await import("./route");

const ORGANIZATION = "7b0f4f7e-2a3c-4f4e-9a59-3f5c1b2d4e6a";

describe("GET /api/auth/workeasy360/start", () => {
  beforeEach(() => {
    callUpstream.mockReset();
    cookieSets.length = 0;
  });
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("asks Django for the authorization URL, keeps the flow in a cookie, and redirects", async () => {
    callUpstream.mockResolvedValue({
      status: 200,
      json: { authorization_url: "https://auth.workeasy360.test/oauth/authorize?x=1", flow: "signed-flow" },
    });

    const response = await GET(
      new Request(`https://book.example.test/api/auth/workeasy360/start?organization=${ORGANIZATION}&next=/invoices`),
    );

    const call = callUpstream.mock.calls[0]?.[0] as { path: string; body: string };
    expect(call.path).toBe("/auth/workeasy360/start/");
    expect(JSON.parse(call.body)).toEqual({ organization: ORGANIZATION });
    expect(response.headers.get("location")).toBe("https://auth.workeasy360.test/oauth/authorize?x=1");
    expect(cookieSets).toHaveLength(1);
    expect(cookieSets[0]?.name).toBe("eb_sso");
    expect(JSON.parse(cookieSets[0]?.value ?? "{}")).toEqual({ flow: "signed-flow", next: "/invoices" });
    expect(cookieSets[0]?.options).toMatchObject({
      httpOnly: true,
      sameSite: "lax",
      path: "/",
      maxAge: 600,
    });
  });

  it("keeps the flow under a __Host- name when cookies are Secure: Secure, Path=/, no Domain", async () => {
    // A sibling subdomain can plant a plain-named eb_sso (Domain=.workeasy360.com) to sign a victim in to
    // the attacker's books; it cannot plant a __Host- one (src/lib/auth/cookie-names.ts).
    vi.stubEnv("SESSION_COOKIE_SECURE", "1");
    callUpstream.mockResolvedValue({ status: 200, json: { authorization_url: "https://a.test/x", flow: "f" } });

    await GET(new Request("https://book.example.test/api/auth/workeasy360/start?next=/invoices"));

    expect(cookieSets).toHaveLength(1);
    expect(cookieSets[0]?.name).toBe("__Host-eb_sso");
    expect(cookieSets[0]?.options).toMatchObject({
      httpOnly: true,
      secure: true,
      sameSite: "lax",
      path: "/",
      maxAge: 600,
    });
    expect(cookieSets[0]?.options).not.toHaveProperty("domain");
  });

  it("keeps the plain eb_sso name without Secure (local development)", async () => {
    vi.stubEnv("SESSION_COOKIE_SECURE", "0");
    callUpstream.mockResolvedValue({ status: 200, json: { authorization_url: "https://a.test/x", flow: "f" } });
    await GET(new Request("https://book.example.test/api/auth/workeasy360/start"));
    expect(cookieSets[0]?.name).toBe("eb_sso");
  });

  it("drops a non-UUID organization and an off-site next", async () => {
    callUpstream.mockResolvedValue({ status: 200, json: { authorization_url: "https://a.test/x", flow: "f" } });
    await GET(new Request("https://book.example.test/api/auth/workeasy360/start?organization=acme&next=//evil"));
    expect(JSON.parse((callUpstream.mock.calls[0]?.[0] as { body: string }).body)).toEqual({ organization: "" });
    expect(JSON.parse(cookieSets[0]?.value ?? "{}")).toEqual({ flow: "f", next: null });
  });

  it("goes back to the login page when Workeasy360 sign-in is unavailable", async () => {
    callUpstream.mockResolvedValue({ status: 503, json: { error: { code: "workeasy360_unavailable" } } });
    const response = await GET(new Request("https://book.example.test/api/auth/workeasy360/start"));
    expect(response.headers.get("location")).toBe("/login?sso_error=unavailable");
    expect(cookieSets).toHaveLength(0);
  });
});
