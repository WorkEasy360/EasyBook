// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";

const callUpstream = vi.fn();
const writeSession = vi.fn();
const readSession = vi.fn();
const writeOrganizationId = vi.fn();
const cookieJar = new Map<string, string>();
const cookieSets: Array<{ name: string; value: string; options: Record<string, unknown> }> = [];

vi.mock("server-only", () => ({}));
vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => (cookieJar.has(name) ? { value: cookieJar.get(name) } : undefined),
    set: (name: string, value: string, options: Record<string, unknown>) => cookieSets.push({ name, value, options }),
  }),
}));
vi.mock("@/lib/api/upstream", () => ({
  callUpstream,
  decodeJson: (response: { json: unknown }) => response.json,
}));
vi.mock("@/lib/auth/session", () => ({
  writeSession,
  readSession,
  writeOrganizationId,
  cookieSecurity: () => ({ httpOnly: true, sameSite: "lax", secure: false, path: "/" }),
}));

const { GET } = await import("./route");

function callback(query: string): Request {
  return new Request(`https://book.example.test/api/auth/callback/workeasy360?${query}`);
}

describe("GET /api/auth/callback/workeasy360", () => {
  beforeEach(() => {
    callUpstream.mockReset();
    writeSession.mockReset();
    readSession.mockReset().mockResolvedValue(null);
    writeOrganizationId.mockReset();
    cookieJar.clear();
    cookieSets.length = 0;
    cookieJar.set("eb_sso", JSON.stringify({ flow: "signed-flow", next: "/invoices" }));
  });

  it("relays code, state and the kept flow, then sets the session and lands on next", async () => {
    callUpstream.mockResolvedValue({ status: 200, json: { access: "a", refresh: "r", organization_id: "org-1" } });

    const response = await GET(callback("code=c&state=s"));

    const call = callUpstream.mock.calls[0]?.[0] as { path: string; body: string };
    expect(call.path).toBe("/auth/workeasy360/callback/");
    expect(JSON.parse(call.body)).toEqual({ code: "c", state: "s", flow: "signed-flow" });
    expect(writeSession).toHaveBeenCalledWith({ access: "a", refresh: "r" });
    expect(writeOrganizationId).toHaveBeenCalledWith("org-1");
    expect(response.headers.get("location")).toBe("/invoices");
  });

  it("signs the previous person out properly when someone else signs in", async () => {
    // Account switching on a shared computer: the earlier session's refresh
    // token is revoked at the API, not merely replaced in the cookie.
    readSession.mockResolvedValue({ access: "old-a", refresh: "old-refresh", organizationId: "org-0" });
    callUpstream
      .mockResolvedValueOnce({ status: 200, json: { access: "a", refresh: "r", organization_id: "org-1" } })
      .mockResolvedValueOnce({ status: 204, json: null });

    await GET(callback("code=c&state=s"));

    const revoke = callUpstream.mock.calls[1]?.[0] as { path: string; body: string };
    expect(revoke.path).toBe("/auth/logout/");
    expect(JSON.parse(revoke.body)).toEqual({ refresh: "old-refresh" });
    expect(writeSession).toHaveBeenCalledWith({ access: "a", refresh: "r" });
  });

  it("still signs the new person in when revoking the previous session fails", async () => {
    readSession.mockResolvedValue({ access: "old-a", refresh: "old-refresh", organizationId: null });
    callUpstream
      .mockResolvedValueOnce({ status: 200, json: { access: "a", refresh: "r", organization_id: "org-1" } })
      .mockRejectedValueOnce(new Error("ECONNREFUSED"));

    const response = await GET(callback("code=c&state=s"));

    expect(writeSession).toHaveBeenCalledWith({ access: "a", refresh: "r" });
    expect(response.headers.get("location")).toBe("/invoices");
  });

  it("spends the flow cookie whatever the outcome", async () => {
    await GET(callback("error=access_denied"));
    expect(cookieSets).toContainEqual(
      expect.objectContaining({ name: "eb_sso", value: "", options: expect.objectContaining({ maxAge: 0 }) }),
    );
  });

  it("maps a provider error without calling Django", async () => {
    const response = await GET(callback("error=product_not_entitled&state=s"));
    expect(response.headers.get("location")).toBe("/login?sso_error=not_entitled");
    expect(callUpstream).not.toHaveBeenCalled();
  });

  it("treats a missing flow cookie as an expired sign-in", async () => {
    cookieJar.clear();
    const response = await GET(callback("code=c&state=s"));
    expect(response.headers.get("location")).toBe("/login?sso_error=expired");
    expect(callUpstream).not.toHaveBeenCalled();
  });

  it("relays Django's refusal as a fixed code and sets no session", async () => {
    callUpstream.mockResolvedValue({ status: 403, json: { error: { code: "state_mismatch" } } });
    const response = await GET(callback("code=c&state=forged"));
    expect(response.headers.get("location")).toBe("/login?sso_error=expired");
    expect(writeSession).not.toHaveBeenCalled();
  });

  it("never lands off-site even if the kept next was tampered with", async () => {
    cookieJar.set("eb_sso", JSON.stringify({ flow: "signed-flow", next: "//evil.example" }));
    callUpstream.mockResolvedValue({ status: 200, json: { access: "a", refresh: "r", organization_id: "o" } });
    const response = await GET(callback("code=c&state=s"));
    expect(response.headers.get("location")).toBe("/dashboard");
  });

  it("redirects relative to the browser's address, never to the server's own", async () => {
    // What Next hands a route handler behind the production proxy
    // (HOSTNAME=0.0.0.0): its own listening address, not the public host.
    callUpstream.mockResolvedValue({ status: 200, json: { access: "a", refresh: "r", organization_id: "o" } });
    const response = await GET(new Request("http://localhost:3000/api/auth/callback/workeasy360?code=c&state=s"));
    expect(response.headers.get("location")).toBe("/invoices");
    cookieJar.clear();
    const refused = await GET(new Request("http://localhost:3000/api/auth/callback/workeasy360?code=c&state=s"));
    expect(refused.headers.get("location")).toBe("/login?sso_error=expired");
  });

  it("reports Django being unreachable as unavailable", async () => {
    callUpstream.mockRejectedValue(new Error("ECONNREFUSED"));
    const response = await GET(callback("code=c&state=s"));
    expect(response.headers.get("location")).toBe("/login?sso_error=unavailable");
  });
});
