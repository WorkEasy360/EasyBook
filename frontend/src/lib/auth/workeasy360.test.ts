// @vitest-environment node
import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
vi.mock("@/lib/api/upstream", () => ({ callUpstream: vi.fn(), decodeJson: vi.fn() }));

const { backendErrorCode, organizationParam, providerErrorCode, safePath, ssoErrorMessage } = await import(
  "./workeasy360"
);

describe("workeasy360 helpers", () => {
  // The tab case: a browser strips it and reads "//evil.com" (sign-in landing open redirect).
  it.each(["//evil.com", "https://evil.com", "/\\evil.com", "evil", "", null, `/${String.fromCharCode(9)}/evil.com`])(
    "refuses %j as a landing path",
    (value) => {
      expect(safePath(value)).toBeNull();
    },
  );

  it("accepts a same-site path", () => {
    expect(safePath("/invoices?page=2")).toBe("/invoices?page=2");
  });

  it("forwards only a UUID organization", () => {
    expect(organizationParam("7b0f4f7e-2a3c-4f4e-9a59-3f5c1b2d4e6a")).toBe("7b0f4f7e-2a3c-4f4e-9a59-3f5c1b2d4e6a");
    expect(organizationParam("acme")).toBe("");
    expect(organizationParam(null)).toBe("");
  });

  it("keeps the Workeasy360 membership and the EasyBook membership apart", () => {
    expect(providerErrorCode("membership_inactive")).toBe("workeasy360_access");
    expect(backendErrorCode("membership_inactive")).toBe("membership_inactive");
  });

  it("maps provider and backend codes onto a fixed vocabulary with a message for each", () => {
    expect(providerErrorCode("product_not_entitled")).toBe("not_entitled");
    expect(providerErrorCode("product_no_seat")).toBe("no_seat");
    expect(providerErrorCode("access_denied")).toBe("cancelled");
    expect(providerErrorCode("<script>")).toBe("failed");
    expect(backendErrorCode("state_mismatch")).toBe("expired");
    for (const code of ["not_entitled", "no_seat", "cancelled", "expired", "failed", "unavailable"]) {
      expect(ssoErrorMessage(code)).toBeTruthy();
    }
    expect(ssoErrorMessage(undefined)).toBeNull();
  });

  it("maps the universal sign-in codes (ADR-044) onto that vocabulary", () => {
    // EasyBook is not registered to use Workeasy360 sign-in: nothing to retry here.
    expect(providerErrorCode("unauthorized_client")).toBe("unavailable");
    // Answers to prompt=none and a lapsed sign-in: start again.
    expect(providerErrorCode("interaction_required")).toBe("expired");
    expect(providerErrorCode("login_required")).toBe("expired");
  });
});

describe("sign-in methods", () => {
  it("never offers signing out of Workeasy360 on a guess", async () => {
    const upstream = await import("@/lib/api/upstream");
    vi.mocked(upstream.callUpstream).mockRejectedValueOnce(new Error("ECONNREFUSED"));
    const { readSignInMethods } = await import("./workeasy360");
    expect(await readSignInMethods()).toEqual({ password: true, workeasy360: true, workeasy360SignOut: false });
  });

  it("reads the server's answer, including the sign-out option", async () => {
    const upstream = await import("@/lib/api/upstream");
    vi.mocked(upstream.callUpstream).mockResolvedValueOnce({ status: 200 } as never);
    vi.mocked(upstream.decodeJson).mockReturnValueOnce({ password: false, workeasy360: true, workeasy360_sign_out: true });
    const { readSignInMethods } = await import("./workeasy360");
    expect(await readSignInMethods({ revalidateSeconds: 300 })).toEqual({
      password: false,
      workeasy360: true,
      workeasy360SignOut: true,
    });
    expect(vi.mocked(upstream.callUpstream).mock.lastCall?.[0]).toMatchObject({ next: { revalidate: 300 } });
  });
});
