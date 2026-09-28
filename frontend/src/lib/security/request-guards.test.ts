import { describe, expect, it } from "vitest";
import { BodyTooLargeError, isForeignOriginWrite, readBodyWithLimit } from "./request-guards";

function headers(values: Record<string, string>): Headers {
  return new Headers(values);
}

describe("isForeignOriginWrite", () => {
  it("never blocks a read", () => {
    expect(isForeignOriginWrite("GET", headers({ "sec-fetch-site": "cross-site" }))).toBe(false);
  });

  it("trusts the browser's Sec-Fetch-Site first", () => {
    expect(isForeignOriginWrite("POST", headers({ "sec-fetch-site": "same-origin" }))).toBe(false);
    expect(isForeignOriginWrite("POST", headers({ "sec-fetch-site": "cross-site" }))).toBe(true);
    // A sibling subdomain is same-SITE but not same-ORIGIN; SameSite=Lax would let its cookies through.
    expect(isForeignOriginWrite("DELETE", headers({ "sec-fetch-site": "same-site" }))).toBe(true);
    expect(isForeignOriginWrite("PATCH", headers({ "sec-fetch-site": "none" }))).toBe(false);
  });

  it("falls back to comparing Origin with the host", () => {
    expect(
      isForeignOriginWrite("POST", headers({ origin: "https://books.example.com", host: "books.example.com" })),
    ).toBe(false);
    expect(
      isForeignOriginWrite("POST", headers({ origin: "https://evil.example.net", host: "books.example.com" })),
    ).toBe(true);
    expect(isForeignOriginWrite("POST", headers({ origin: "null", host: "books.example.com" }))).toBe(true);
  });

  it("uses the forwarded host behind a reverse proxy", () => {
    expect(
      isForeignOriginWrite(
        "PUT",
        headers({ origin: "https://books.example.com", host: "10.0.0.5:3000", "x-forwarded-host": "books.example.com" }),
      ),
    ).toBe(false);
  });

  it("lets a non-browser client through (no Origin, no Sec-Fetch-Site)", () => {
    expect(isForeignOriginWrite("POST", headers({ host: "books.example.com" }))).toBe(false);
  });
});

describe("readBodyWithLimit", () => {
  it("returns the body when it fits", async () => {
    const request = new Request("http://test/", { method: "POST", body: "hello" });
    const body = await readBodyWithLimit(request, 10);
    expect(new TextDecoder().decode(body!)).toBe("hello");
  });

  it("rejects on a declared length before reading anything", async () => {
    const request = new Request("http://test/", {
      method: "POST",
      body: "hello",
      headers: { "content-length": "999999" },
    });
    await expect(readBodyWithLimit(request, 10)).rejects.toBeInstanceOf(BodyTooLargeError);
  });

  it("rejects an oversized stream even without a declared length", async () => {
    const request = new Request("http://test/", { method: "POST", body: "x".repeat(64) });
    request.headers.delete("content-length");
    await expect(readBodyWithLimit(request, 16)).rejects.toBeInstanceOf(BodyTooLargeError);
  });

  it("returns null for a bodiless request", async () => {
    expect(await readBodyWithLimit(new Request("http://test/", { method: "POST" }), 10)).toBeNull();
  });
});
