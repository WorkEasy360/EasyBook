import { describe, expect, it } from "vitest";
import { estimateLine, estimateTotals } from "./estimate";

/**
 * Cases worked by hand against backend/core/money.py :: calculate_line, so a
 * drift between the estimate and the engine shows up here rather than as a
 * total that changes after save.
 */

describe("estimateLine", () => {
  it("applies the discount before tax and rounds each step half-up", () => {
    // base 3 × 333.33 = 999.99; discount 10% = 99.999 -> 100.00;
    // taxable 899.99; tax 18% = 161.9982 -> 162.00; total 1061.99
    expect(estimateLine({ quantity: "3", unit_price: "333.33", discount_percent: "10", tax_rate: "18" })).toEqual({
      line_base: "999.99",
      discount_amount: "100.00",
      taxable_amount: "899.99",
      tax_amount: "162.00",
      line_total: "1061.99",
    });
  });

  it("rounds a half cent up, as ROUND_HALF_UP does", () => {
    // base 0.5 × 0.05 = 0.025 -> 0.03
    expect(estimateLine({ quantity: "0.5", unit_price: "0.05", discount_percent: "", tax_rate: "" })?.line_base).toBe(
      "0.03",
    );
  });

  it("treats blank discount and tax as zero", () => {
    expect(estimateLine({ quantity: "2", unit_price: "1250", discount_percent: "", tax_rate: "" })).toEqual({
      line_base: "2500.00",
      discount_amount: "0.00",
      taxable_amount: "2500.00",
      tax_amount: "0.00",
      line_total: "2500.00",
    });
  });

  it("keeps full precision on amounts a float would corrupt", () => {
    const estimate = estimateLine({
      quantity: "1",
      unit_price: "9007199254740993.01",
      discount_percent: "0",
      tax_rate: "0",
    });
    expect(estimate?.line_total).toBe("9007199254740993.01");
  });

  it.each([
    [{ quantity: "", unit_price: "10", discount_percent: "", tax_rate: "" }],
    [{ quantity: "0", unit_price: "10", discount_percent: "", tax_rate: "" }],
    [{ quantity: "1", unit_price: "-1", discount_percent: "", tax_rate: "" }],
    [{ quantity: "1", unit_price: "10", discount_percent: "101", tax_rate: "" }],
    [{ quantity: "1", unit_price: "10", discount_percent: "", tax_rate: "-5" }],
    [{ quantity: "abc", unit_price: "10", discount_percent: "", tax_rate: "" }],
  ])("returns null for a line the backend would reject: %o", (line) => {
    expect(estimateLine(line)).toBeNull();
  });
});

describe("estimateTotals", () => {
  it("sums the already-rounded lines rather than re-rounding the sum", () => {
    // Each line: 1 × 0.333 -> base 0.33, tax 18% of 0.33 = 0.0594 -> 0.06.
    // Summed rounded lines: tax 0.18. Rounding the unrounded sum would give
    // 3 × 0.0594 = 0.1782 -> 0.18 too, so use a case where they differ:
    // tax 5% of 0.33 = 0.0165 -> 0.02 per line, 0.06 total (vs 0.0495 -> 0.05).
    const line = { quantity: "1", unit_price: "0.333", discount_percent: "", tax_rate: "5" };
    const totals = estimateTotals([line, line, line]);
    expect(totals.tax_total).toBe("0.06");
    expect(totals.subtotal).toBe("0.99");
    expect(totals.total).toBe("1.05");
    expect(totals.complete).toBe(true);
  });

  it("reports incomplete while a line is unfinished, totalling the valid lines only", () => {
    const totals = estimateTotals([
      { quantity: "2", unit_price: "100", discount_percent: "", tax_rate: "" },
      { quantity: "", unit_price: "", discount_percent: "", tax_rate: "" },
    ]);
    expect(totals.total).toBe("200.00");
    expect(totals.complete).toBe(false);
  });

  it("is incomplete with no lines at all", () => {
    expect(estimateTotals([]).complete).toBe(false);
  });
});
