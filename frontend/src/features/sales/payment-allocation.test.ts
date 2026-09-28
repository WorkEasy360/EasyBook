import { describe, expect, it } from "vitest";
import { allocationProblem, allocationsInput, estimateAllocation } from "./payment-allocation";

/** Mirrors the rules in sales/services/payments.py :: record_payment. */

describe("estimateAllocation", () => {
  it("leaves a remainder as unapplied credit, which needs an account", () => {
    expect(estimateAllocation("1300", { a: "1000", b: "" })).toEqual({
      allocated: "1000.00",
      unapplied: "300.00",
      exceedsPayment: false,
      needsUnappliedAccount: true,
    });
  });

  it("needs no unapplied account when the payment is fully allocated", () => {
    const estimate = estimateAllocation("0.30", { a: "0.10", b: "0.20" });
    // Exact decimal arithmetic: 0.1 + 0.2 is 0.30, so nothing is left over.
    expect(estimate.unapplied).toBe("0.00");
    expect(estimate.needsUnappliedAccount).toBe(false);
  });

  it("flags allocations that exceed the payment", () => {
    const estimate = estimateAllocation("100", { a: "60", b: "60" });
    expect(estimate.exceedsPayment).toBe(true);
    expect(estimate.unapplied).toBe("-20.00");
  });

  it("ignores malformed and non-positive rows in the sum", () => {
    expect(estimateAllocation("50", { a: "abc", b: "-5", c: "0", d: "10" }).allocated).toBe("10.00");
  });

  it("reports no unapplied figure while the amount is blank", () => {
    expect(estimateAllocation("", { a: "10" })).toMatchObject({ unapplied: null, needsUnappliedAccount: false });
  });
});

describe("allocationProblem", () => {
  it("accepts blank and amounts up to the balance due", () => {
    expect(allocationProblem("", "100.00")).toBeNull();
    expect(allocationProblem("100", "100.00")).toBeNull();
  });

  it("rejects zero, negatives and more than is due", () => {
    expect(allocationProblem("0", "100.00")).toMatch(/above zero/);
    expect(allocationProblem("100.01", "100.00")).toMatch(/balance due/);
  });
});

describe("allocationsInput", () => {
  it("sends only rows with a positive amount", () => {
    expect(allocationsInput({ a: "10", b: "", c: "0" })).toEqual([{ invoice_id: "a", amount: "10" }]);
  });
});
