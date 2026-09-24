import { describe, expect, it } from "vitest";
import { productionConfigProblems } from "./production-preflight";

const GOOD = {
  SESSION_COOKIE_SECURE: "1",
  BFF_PROXY_SECRET: "x".repeat(64),
  API_BASE_URL: "https://app.example.com/api/v1",
  TRUSTED_PROXY_COUNT: "1",
};

describe("productionConfigProblems", () => {
  it("accepts a complete production configuration", () => {
    expect(productionConfigProblems(GOOD)).toEqual([]);
    expect(productionConfigProblems({ ...GOOD, API_BASE_URL: "http://api.internal:8000/api/v1/" })).toEqual([]);
  });

  it("fails closed on every missing or unsafe value", () => {
    expect(productionConfigProblems({})).toHaveLength(4);
    expect(productionConfigProblems({ ...GOOD, SESSION_COOKIE_SECURE: "0" }).join()).toMatch(/SESSION_COOKIE_SECURE/);
    expect(productionConfigProblems({ ...GOOD, BFF_PROXY_SECRET: "short" }).join()).toMatch(/BFF_PROXY_SECRET/);
    expect(productionConfigProblems({ ...GOOD, API_BASE_URL: "app.example.com/api/v1" }).join()).toMatch(/absolute/);
    expect(productionConfigProblems({ ...GOOD, API_BASE_URL: "ftp://app.example.com/api/v1" }).join()).toMatch(/absolute/);
    expect(productionConfigProblems({ ...GOOD, API_BASE_URL: "https://app.example.com/admin" }).join()).toMatch(/\/api\/v1/);
    expect(productionConfigProblems({ ...GOOD, API_BASE_URL: "https://u:p@app.example.com/api/v1" }).join()).toMatch(/credentials/);
    expect(productionConfigProblems({ ...GOOD, TRUSTED_PROXY_COUNT: "-1" }).join()).toMatch(/TRUSTED_PROXY_COUNT/);
  });
});
