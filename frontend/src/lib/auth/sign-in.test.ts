// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";

const callUpstream = vi.fn();
const writeSession = vi.fn();
const writeOrganizationId = vi.fn();
const clearSignedOutMarker = vi.fn();

vi.mock("server-only", () => ({}));
vi.mock("@/lib/api/upstream", () => ({
  callUpstream,
  decodeJson: (response: { json: unknown }) => response.json,
}));
vi.mock("@/lib/auth/session", () => ({ writeSession, writeOrganizationId, clearSignedOutMarker }));

const { signInWithPassword } = await import("./sign-in");

describe("signInWithPassword and the direct-visit markers", () => {
  beforeEach(() => {
    callUpstream.mockReset();
    writeSession.mockReset();
    writeOrganizationId.mockReset();
    clearSignedOutMarker.mockReset();
  });

  it("clears the sign-out marker once the session is written", async () => {
    callUpstream
      .mockResolvedValueOnce({ status: 200, json: { access: "a", refresh: "r" } })
      .mockResolvedValueOnce({ status: 200, json: [{ id: "org-1", name: "Acme", role: "owner" }] });

    const response = await signInWithPassword("a@example.test", "pw");

    expect(response.status).toBe(200);
    expect(writeSession).toHaveBeenCalledWith({ access: "a", refresh: "r" });
    expect(clearSignedOutMarker).toHaveBeenCalledTimes(1);
  });

  it("leaves them alone when the sign-in is refused", async () => {
    callUpstream.mockResolvedValueOnce({ status: 401, json: null });

    const response = await signInWithPassword("a@example.test", "wrong");

    expect(response.status).toBe(401);
    expect(writeSession).not.toHaveBeenCalled();
    expect(clearSignedOutMarker).not.toHaveBeenCalled();
  });
});
