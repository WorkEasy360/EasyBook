import { describe, expect, it } from "vitest";
import {
  add,
  compare,
  formatMoney,
  formatPercent,
  formatQuantity,
  isValidDecimal,
  money,
  moneyAriaLabel,
  percentOf,
  subtract,
  sum,
  toDecimalString,
} from "./money";

const INR = { currency: "INR", locale: "en-IN" } as const;

describe("decimal arithmetic", () => {
  it("does not lose precision on values a float cannot hold", () => {
    // 0.1 + 0.2 === 0.30000000000000004 in float arithmetic. An invoice line
    // that rounds like that produces a journal whose debits != credits.
    expect(toDecimalString(add("0.10", "0.20"))).toBe("0.30");
  });

  it("keeps precision beyond Number.MAX_SAFE_INTEGER", () => {
    // 9007199254740993 is not representable as a double; formatting it via a
    // JS number silently reports ...994.
    expect(toDecimalString("9007199254740993.01")).toBe("9007199254740993.01");
    expect(formatMoney("9007199254740993.01", INR)).toContain("993.01");
  });

  it("sums a column of line amounts exactly", () => {
    const lines = ["1234.56", "0.01", "99.99", "-34.56"];
    expect(toDecimalString(sum(lines))).toBe("1300.00");
  });

  it("computes a GST amount without drift", () => {
    // 18% of 1,04,999.99 — the kind of figure that exposes float rounding.
    expect(toDecimalString(percentOf("104999.99", "18"))).toBe("18900.00");
  });

  it("subtracts to exact zero", () => {
    expect(toDecimalString(subtract("1000.10", "1000.10"))).toBe("0.00");
  });

  it("treats null, undefined and empty string as zero", () => {
    expect(money(null).toFixed(2)).toBe("0.00");
    expect(money(undefined).toFixed(2)).toBe("0.00");
    expect(money("").toFixed(2)).toBe("0.00");
  });

  it("does not throw on a malformed amount", () => {
    // A malformed value must degrade to zero, never crash a ledger render.
    expect(money("not-a-number").toFixed(2)).toBe("0.00");
  });

  it("compares without float coercion", () => {
    expect(compare("0.30", add("0.10", "0.20"))).toBe(0);
    expect(compare("1.00", "2.00")).toBe(-1);
    expect(compare("2.00", "1.00")).toBe(1);
  });

  it("validates decimal input", () => {
    expect(isValidDecimal("12.50")).toBe(true);
    expect(isValidDecimal("-3")).toBe(true);
    expect(isValidDecimal("")).toBe(false);
    expect(isValidDecimal("12.5.0")).toBe(false);
    expect(isValidDecimal("abc")).toBe(false);
  });
});

describe("currency presentation", () => {
  it("uses Indian digit grouping", () => {
    // 1,00,000 — not 100,000. Getting this wrong is immediately visible to
    // every Indian user.
    expect(formatMoney("100000", INR)).toBe("₹1,00,000.00");
  });

  it("renders a negative with a minus sign by default", () => {
    expect(formatMoney("-1000", INR)).toBe("-₹1,000.00");
  });

  it("wraps the whole figure in parentheses in accounting mode", () => {
    // (₹1,000.00), not ₹(1,000.00) — the symbol goes inside the brackets.
    expect(formatMoney("-1000", { ...INR, accounting: true })).toBe("(₹1,000.00)");
  });

  it("omits the symbol when the column header carries it", () => {
    expect(formatMoney("1000", { ...INR, hideSymbol: true })).toBe("1,000.00");
  });

  it("always shows two fraction digits", () => {
    expect(formatMoney("5", INR)).toBe("₹5.00");
    expect(formatMoney("5.1", INR)).toBe("₹5.10");
  });

  it("announces a negative as a word, since parentheses are not read aloud", () => {
    expect(moneyAriaLabel("-1000", INR)).toBe("negative ₹1,000.00");
    expect(moneyAriaLabel("1000", INR)).toBe("₹1,000.00");
  });

  it("formats a non-INR currency with its own symbol", () => {
    expect(formatMoney("1000", { currency: "USD", locale: "en-US" })).toBe("$1,000.00");
  });
});

describe("quantity and percentage", () => {
  it("formats a quantity without a currency symbol", () => {
    expect(formatQuantity("12.5")).toBe("12.5");
    expect(formatQuantity("1000")).toBe("1,000");
  });

  it("keeps fractional quantities", () => {
    expect(formatQuantity("0.25")).toBe("0.25");
  });

  it("formats a tax rate", () => {
    expect(formatPercent("18")).toBe("18%");
    expect(formatPercent("2.5")).toBe("2.5%");
  });
});
