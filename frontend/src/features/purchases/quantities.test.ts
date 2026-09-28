import { describe, expect, it } from "vitest";
import {
  allocationSummary,
  billedByReceiptLine,
  estimateExpense,
  exceeds,
  isPositive,
  outstandingOrderQuantity,
} from "./quantities";

describe("outstandingOrderQuantity", () => {
  it("offers the whole order when nothing arrived or was billed", () => {
    expect(outstandingOrderQuantity("10.0000", "0", "0")).toBe("10");
  });

  it("subtracts goods received on a receipt", () => {
    expect(outstandingOrderQuantity("10.0000", "4.0000", "0")).toBe("6");
  });

  it("subtracts goods a direct bill already received, even though no receipt shows them", () => {
    // Billed 7 without a goods receipt: the bill received that stock itself.
    expect(outstandingOrderQuantity("10.0000", "4.0000", "7.0000")).toBe("3");
  });

  it("does not double-subtract receipt-linked billing", () => {
    expect(outstandingOrderQuantity("10.0000", "4.0000", "4.0000")).toBe("6");
  });

  it("never goes negative on an over-received line", () => {
    expect(outstandingOrderQuantity("10", "12", "0")).toBe("0");
  });

  it("keeps fractional quantities exact", () => {
    expect(outstandingOrderQuantity("2.5000", "0.1250", null)).toBe("2.375");
  });

  it("tolerates a numeric wire value from an older API build", () => {
    expect(outstandingOrderQuantity(10, 4, 0)).toBe("6");
  });
});

describe("allocationSummary", () => {
  it("books the unallocated remainder as an advance that needs an account", () => {
    expect(allocationSummary("500.00", ["150.00", ""])).toEqual({
      allocated: "150.00",
      remainder: "350.00",
      overAllocated: false,
      needsAdvanceAccount: true,
    });
  });

  it("needs no advance account when fully allocated", () => {
    const summary = allocationSummary("300", ["100.10", "199.90"]);
    expect(summary.remainder).toBe("0.00");
    expect(summary.needsAdvanceAccount).toBe(false);
    expect(summary.overAllocated).toBe(false);
  });

  it("flags allocations above the payment", () => {
    expect(allocationSummary("100", ["60", "50"]).overAllocated).toBe(true);
  });

  it("ignores blank, malformed and non-positive entries", () => {
    expect(allocationSummary("10", ["abc", "-5", "0", "2.5"]).allocated).toBe("2.50");
  });

  it("avoids float drift", () => {
    expect(allocationSummary("0.3", ["0.1", "0.2"]).remainder).toBe("0.00");
  });
});

describe("estimateExpense", () => {
  it("rounds tax half-up to two places like the service", () => {
    expect(estimateExpense("1200.50", "18")).toEqual({ amount: "1200.50", tax: "216.09", total: "1416.59" });
  });

  it("treats a blank rate as zero", () => {
    expect(estimateExpense("99.99", "")).toEqual({ amount: "99.99", tax: "0.00", total: "99.99" });
  });

  it("returns null for inputs the server would reject", () => {
    expect(estimateExpense("", "5")).toBeNull();
    expect(estimateExpense("0", "5")).toBeNull();
    expect(estimateExpense("10", "-1")).toBeNull();
  });
});

describe("billedByReceiptLine", () => {
  it("sums every non-void bill's linked lines, drafts included", () => {
    const totals = billedByReceiptLine([
      { status: "open", lines: [{ source_goods_receipt_line: "grl-1", quantity: "4.0000" }] },
      { status: "draft", lines: [{ source_goods_receipt_line: "grl-1", quantity: "1.5000" }] },
      { status: "void", lines: [{ source_goods_receipt_line: "grl-1", quantity: "10.0000" }] },
      { status: "open", lines: [{ source_goods_receipt_line: null, quantity: "3" }] },
    ]);
    expect(totals.get("grl-1")).toBe("5.5");
    expect(totals.size).toBe(1);
  });

  it("feeds exceeds() to find unbilled receipt lines", () => {
    const totals = billedByReceiptLine([{ status: "paid", lines: [{ source_goods_receipt_line: "a", quantity: "4" }] }]);
    expect(exceeds("4.0000", totals.get("a"))).toBe(false);
    expect(exceeds("6.0000", totals.get("b"))).toBe(true);
  });
});

describe("isPositive", () => {
  it("compares decimals, not strings", () => {
    expect(isPositive("0.0001")).toBe(true);
    expect(isPositive("0.0000")).toBe(false);
    expect(isPositive(null)).toBe(false);
  });
});
