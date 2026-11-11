// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";

const callUpstream = vi.fn();
const markSignedOut = vi.fn();
vi.mock("@/lib/auth/session", () => ({ markSignedOut }));
vi.mock("@/lib/api/upstream", () => ({
  callUpstream,
  decodeJson: (response: { json: unknown }) => response.json,
}));

const { GET } = await import("./route");

const request = (fetchSite: string | null = null) =>
  new Request("http://localhost:3000/api/auth/workeasy360/logout", {
    headers: fetchSite ? { "Sec-Fetch-Site": fetchSite } : {},
  });

describe("GET /api/auth/workeasy360/logout", () => {
  // Braces: a function returned from beforeEach is run as a teardown.
  beforeEach(() => {
    callUpstream.mockReset();
    markSignedOut.mockReset();
  });

  it.each([
    ["Workeasy360 names its end-session URL", { status: 200, json: { url: "https://auth.workeasy360.test/logout" } }],
    ["it is not offered", { status: 200, json: { url: "" } }],
    ["the API cannot answer", { status: 503, json: null }],
  ])("leaves the sign-out marker whichever way it continues (%s)", async (_label, reply) => {
    // The person is shown the sign-in page when they come back, never signed
    // straight back in (src/lib/auth/direct-visit.ts).
    callUpstream.mockResolvedValue(reply);
    await GET(request());
    expect(markSignedOut).toHaveBeenCalledTimes(1);
  });

  it.each([["same-origin"], [null]])(
    "leaves the marker for a request from EasyBook's own pages (Sec-Fetch-Site: %s)",
    async (site) => {
      callUpstream.mockResolvedValue({ status: 200, json: { url: "https://auth.workeasy360.test/logout" } });
      await GET(request(site));
      expect(markSignedOut).toHaveBeenCalledTimes(1);
    },
  );

  it.each([["cross-site"], ["same-site"], ["none"]])(
    "does not set the marker for a request from elsewhere (Sec-Fetch-Site: %s), and still continues",
    async (site) => {
      // A link on another site - or a sibling subdomain, which is same-site - must not be able to
      // suppress this browser's direct-visit sign-in.
      const url = "https://auth.workeasy360.test/logout";
      callUpstream.mockResolvedValue({ status: 200, json: { url } });

      const response = await GET(request(site));

      expect(markSignedOut).not.toHaveBeenCalled();
      expect(response.headers.get("location")).toBe(url);
    },
  );

  it("sends the browser to the Workeasy360 end-session URL the API names", async () => {
    const url = "https://auth.workeasy360.test/api/v1/integrations/oauth/logout?client_id=c&post_logout_redirect_uri=x";
    callUpstream.mockResolvedValue({ status: 200, json: { url } });

    const response = await GET(request());

    expect(callUpstream.mock.calls[0]?.[0]).toMatchObject({ path: "/auth/workeasy360/end-session/" });
    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe(url);
  });

  it.each([
    ["not offered", { status: 200, json: { url: "" } }],
    ["not a web URL", { status: 200, json: { url: "javascript:alert(1)" } }],
    ["an API error", { status: 503, json: null }],
  ])("lands on sign-in when %s", async (_label, reply) => {
    callUpstream.mockResolvedValue(reply);
    const response = await GET(request());
    expect(response.headers.get("location")).toBe("/login");
  });

  it("lands on sign-in when the API cannot be reached", async () => {
    callUpstream.mockRejectedValue(new Error("ECONNREFUSED"));
    const response = await GET(request());
    expect(response.headers.get("location")).toBe("/login");
  });
});
