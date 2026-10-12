// @vitest-environment node
import type * as React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const callUpstream = vi.fn();
const redirect = vi.fn((url: string) => {
  throw new Error(`NEXT_REDIRECT:${url}`);
});

vi.mock("server-only", () => ({}));
vi.mock("react", async (importOriginal) => ({
  ...(await importOriginal<typeof React>()),
  cache: <T>(fn: T) => fn,
}));
vi.mock("next/navigation", () => ({ redirect }));
vi.mock("@/lib/api/upstream", () => ({
  callUpstream,
  decodeJson: (response: { json: unknown }) => response.json,
}));
vi.mock("./session", () => ({
  readSession: async () => ({ access: "a", refresh: "r", organizationId: "org-1" }),
}));

const { requireSession } = await import("./context");

const reply = (status: number, json: unknown) => ({ status, json, requestId: null });

/** The account calls answer; the organization-scoped fiscal-year check answers `setup`. */
function upstream(setup: ReturnType<typeof reply>) {
  callUpstream.mockImplementation(async ({ path }: { path: string }) => {
    if (path === "/auth/me/") return reply(200, { id: "u1", email: "asha@example.com" });
    if (path === "/organizations/") {
      return reply(200, [{ id: "org-1", role: "owner", default_currency: "INR", timezone: "Asia/Kolkata" }]);
    }
    return setup;
  });
}

describe("requireSession", () => {
  beforeEach(() => {
    callUpstream.mockReset();
    redirect.mockClear();
  });

  it("sends an organization whose Workeasy360 access has ended to its explanation page", async () => {
    upstream(
      reply(403, {
        error: { code: "workeasy360_access_ended", message: "ended", details: null, request_id: "r1" },
      }),
    );
    await expect(requireSession()).rejects.toThrow("NEXT_REDIRECT:/access-ended");
  });

  it("says it cannot confirm the access, not that it ended, when Workeasy360 refused EasyBook's check", async () => {
    upstream(
      reply(403, {
        error: { code: "workeasy360_access_unverified", message: "cannot confirm", details: null, request_id: "r3" },
      }),
    );
    await expect(requireSession()).rejects.toThrow("NEXT_REDIRECT:/access-ended?reason=unverified");
  });

  it("explains a person's own ended access (membership removed, seat unassigned), not the organization's", async () => {
    for (const reason of ["membership_inactive", "no_seat"]) {
      redirect.mockClear();
      upstream(
        reply(403, {
          error: { code: "workeasy360_access_ended", message: "ended", details: { reason }, request_id: "r4" },
        }),
      );
      await expect(requireSession()).rejects.toThrow(`NEXT_REDIRECT:/access-ended?reason=${reason}`);
    }
  });

  it("sends a session with no Workeasy360 account to sign in", async () => {
    upstream(
      reply(403, {
        error: { code: "workeasy360_sign_in_required", message: "sign in", details: null, request_id: "r6" },
      }),
    );
    await expect(requireSession()).rejects.toThrow("NEXT_REDIRECT:/access-ended?reason=sign_in_required");
  });

  it("ignores a reason it does not know", async () => {
    upstream(
      reply(403, {
        error: { code: "workeasy360_access_ended", message: "ended", details: { reason: "<script>" }, request_id: "r5" },
      }),
    );
    await expect(requireSession()).rejects.toThrow("NEXT_REDIRECT:/access-ended");
    expect(redirect).toHaveBeenCalledWith("/access-ended");
  });

  it("does not mistake any other refusal for an ended access", async () => {
    upstream(reply(403, { error: { code: "permission_denied", message: "no", details: null, request_id: "r2" } }));
    await expect(requireSession()).rejects.toThrow("could not load your organization");
    expect(redirect).not.toHaveBeenCalled();
  });

  it("still fails the render, not a redirect, when the check has no answer", async () => {
    upstream(reply(503, null));
    await expect(requireSession()).rejects.toThrow("could not load your organization");
    expect(redirect).not.toHaveBeenCalled();
  });
});
