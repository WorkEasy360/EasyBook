// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";

const readSession = vi.fn();
const clearSession = vi.fn();
const callUpstream = vi.fn();

vi.mock("@/lib/auth/session", () => ({ readSession, clearSession }));
vi.mock("@/lib/api/upstream", () => ({ callUpstream }));

const { POST } = await import("./route");

function logoutRequest(headers: Record<string, string> = {}): Request {
  return new Request("https://app.example.test/api/auth/logout", {
    method: "POST",
    headers: { Origin: "https://app.example.test", "Sec-Fetch-Site": "same-origin", ...headers },
  });
}

describe("POST /api/auth/logout", () => {
  beforeEach(() => {
    readSession.mockReset();
    clearSession.mockReset();
    callUpstream.mockReset();
  });

  it("revokes the refresh token at the API's logout endpoint, then clears the cookies", async () => {
    readSession.mockResolvedValue({ access: "a", refresh: "the-refresh-token", organizationId: null });
    callUpstream.mockResolvedValue({ status: 204 });

    const response = await POST(logoutRequest());

    expect(response.status).toBe(200);
    expect(callUpstream).toHaveBeenCalledTimes(1);
    const call = callUpstream.mock.calls[0]?.[0] as { path: string; method: string; body: string };
    expect(call.path).toBe("/auth/logout/");
    expect(call.method).toBe("POST");
    expect(JSON.parse(call.body)).toEqual({ refresh: "the-refresh-token" });
    expect(clearSession).toHaveBeenCalledTimes(1);
  });

  it("still clears the cookies when the API cannot be reached", async () => {
    readSession.mockResolvedValue({ access: "a", refresh: "r", organizationId: null });
    callUpstream.mockRejectedValue(new Error("ECONNREFUSED"));

    const response = await POST(logoutRequest());

    expect(response.status).toBe(200);
    expect(clearSession).toHaveBeenCalledTimes(1);
  });

  it("clears cookies without an upstream call when there is no session", async () => {
    readSession.mockResolvedValue(null);

    const response = await POST(logoutRequest());

    expect(response.status).toBe(200);
    expect(callUpstream).not.toHaveBeenCalled();
    expect(clearSession).toHaveBeenCalledTimes(1);
  });

  it("refuses a cross-site sign-out without touching the session", async () => {
    readSession.mockResolvedValue({ access: "a", refresh: "r", organizationId: null });

    const response = await POST(logoutRequest({ Origin: "https://evil.example", "Sec-Fetch-Site": "cross-site" }));

    expect(response.status).toBe(403);
    expect(callUpstream).not.toHaveBeenCalled();
    expect(clearSession).not.toHaveBeenCalled();
  });
});
