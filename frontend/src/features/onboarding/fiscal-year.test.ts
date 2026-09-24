import { describe, expect, it } from "vitest";
import { coversToday, fiscalYearRangeProblem, suggestedFiscalYear } from "./fiscal-year";

describe("suggestedFiscalYear", () => {
  it("uses the organization's own start month (April → March for India's default)", () => {
    expect(suggestedFiscalYear("2026-09-24", 4)).toEqual({ start_date: "2026-04-01", end_date: "2027-03-31" });
    expect(suggestedFiscalYear("2026-02-10", 4)).toEqual({ start_date: "2025-04-01", end_date: "2026-03-31" });
  });

  it("does not hard-code April for everyone", () => {
    expect(suggestedFiscalYear("2026-09-24", 1)).toEqual({ start_date: "2026-01-01", end_date: "2026-12-31" });
    expect(suggestedFiscalYear("2026-09-24", 7)).toEqual({ start_date: "2026-07-01", end_date: "2027-06-30" });
  });

  it("falls back to January for an out-of-range month rather than producing an invalid date", () => {
    expect(suggestedFiscalYear("2026-09-24", 0)).toEqual({ start_date: "2026-01-01", end_date: "2026-12-31" });
  });
});

describe("fiscalYearRangeProblem", () => {
  it("accepts an ordinary year and a short first year", () => {
    expect(fiscalYearRangeProblem("2026-04-01", "2027-03-31")).toBeNull();
    expect(fiscalYearRangeProblem("2027-01-15", "2027-03-31")).toBeNull();
  });

  it("mirrors the server's rules", () => {
    expect(fiscalYearRangeProblem("2027-03-31", "2026-04-01")).toMatch(/end after it starts/);
    expect(fiscalYearRangeProblem("2026-04-01", "2026-04-01")).toMatch(/end after it starts/);
    expect(fiscalYearRangeProblem("2026-04-01", "2028-03-31")).toMatch(/18 months/);
    expect(fiscalYearRangeProblem("", "2027-03-31")).toMatch(/first day/);
    expect(fiscalYearRangeProblem("2026-04-01", "31/03/2027")).toMatch(/last day/);
  });
});

describe("coversToday", () => {
  it("is inclusive at both ends", () => {
    expect(coversToday("2026-04-01", "2027-03-31", "2026-04-01")).toBe(true);
    expect(coversToday("2026-04-01", "2027-03-31", "2027-03-31")).toBe(true);
    expect(coversToday("2026-04-01", "2027-03-31", "2027-04-01")).toBe(false);
  });
});
