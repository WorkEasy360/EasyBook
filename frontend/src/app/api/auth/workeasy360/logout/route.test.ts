// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";

const callUpstream = vi.fn();
vi.mock("@/lib/api/upstream", () => ({
  callUpstream,
  decodeJson: (response: { json: unknown }) => response.json,
}));

const { GET } = await import("./route");

const request = () => new Request("http://localhost:3000/api/auth/workeasy360/logout");

describe("GET /api/auth/workeasy360/logout", () => {
  // Braces: a function returned from beforeEach is run as a teardown.
  beforeEach(() => {
    callUpstream.mockReset();
  });

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
