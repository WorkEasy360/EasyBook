import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/errors";
import { estimateJournalTotals, lineSide } from "./journal-math";
import { journalSourceOf } from "./journal-source";
import { lineErrorsOf } from "./journal-errors";

describe("lineSide — mirrors accounting/services/journals.py :: _validate_line_shape", () => {
  it("identifies a one-sided line", () => {
    expect(lineSide({ debit: "100.00", credit: "" })).toBe("debit");
    expect(lineSide({ debit: "0", credit: "5" })).toBe("credit");
  });

  it("flags both sides, neither side, negatives and junk", () => {
    expect(lineSide({ debit: "1", credit: "1" })).toBe("both");
    expect(lineSide({ debit: "", credit: "0.00" })).toBe("none");
    expect(lineSide({ debit: "-5", credit: "" })).toBe("invalid");
    expect(lineSide({ debit: "abc", credit: "" })).toBe("invalid");
  });
});

describe("estimateJournalTotals", () => {
  it("balances in decimal, not float", () => {
    // 0.1 + 0.2 in float is 0.30000000000000004 and would never equal 0.30.
    const totals = estimateJournalTotals([
      { debit: "0.10", credit: "" },
      { debit: "0.20", credit: "" },
      { debit: "", credit: "0.30" },
    ]);
    expect(totals).toEqual({ debit: "0.30", credit: "0.30", difference: "0.00", balanced: true, complete: true });
  });

  it("reports the difference and is not balanced when sides differ", () => {
    const totals = estimateJournalTotals([
      { debit: "125.50", credit: "" },
      { debit: "", credit: "100" },
    ]);
    expect(totals.difference).toBe("25.50");
    expect(totals.balanced).toBe(false);
  });

  it("treats an all-zero journal as unbalanced, as posting does (journal_zero_total)", () => {
    expect(estimateJournalTotals([{ debit: "", credit: "" }, { debit: "0", credit: "0" }]).balanced).toBe(false);
  });

  it("leaves unparseable lines out and says so", () => {
    const totals = estimateJournalTotals([
      { debit: "10", credit: "" },
      { debit: "x", credit: "" },
    ]);
    expect(totals.debit).toBe("10.00");
    expect(totals.complete).toBe(false);
  });

  it("keeps precision beyond a double", () => {
    const totals = estimateJournalTotals([
      { debit: "9007199254740993.01", credit: "" },
      { debit: "", credit: "9007199254740993.01" },
    ]);
    expect(totals.debit).toBe("9007199254740993.01");
    expect(totals.balanced).toBe(true);
  });
});

describe("journalSourceOf", () => {
  const id = "c963842d-f02b-4320-9dff-7266637a52d6";

  it("returns null for a manual journal", () => {
    expect(journalSourceOf("", "")).toBeNull();
  });

  it("links recognised documents to their route", () => {
    expect(journalSourceOf("sales.Invoice", id)).toEqual({ label: "Invoice", href: `/sales/invoices/${id}` });
    expect(journalSourceOf("stock_adjustment", id)).toEqual({
      label: "Stock adjustment",
      href: `/inventory/adjustments/${id}`,
    });
    expect(journalSourceOf("purchases.Bill.void", id)?.href).toBe(`/purchases/bills/${id}`);
  });

  it("never builds a link from a non-UUID id or an unknown type", () => {
    expect(journalSourceOf("sales.Invoice", "../../admin")).toEqual({ label: "Invoice", href: null });
    expect(journalSourceOf("legacy.Import", id)).toEqual({ label: "legacy.Import", href: null });
    expect(journalSourceOf("banking.BankTransfer", id)).toEqual({ label: "Bank transfer", href: null });
    expect(journalSourceOf("banking.BankTransaction", id)?.href).toBe(`/banking/transactions/${id}`);
  });
});

describe("lineErrorsOf", () => {
  it("maps DRF's positional line errors by index and field", () => {
    // Captured live: POST /accounting/journals/ with a bad debit and a missing account.
    const error = new ApiError({
      status: 400,
      message: "Request failed validation.",
      details: { lines: [{ debit: ["A valid number is required."] }, { account_id: ["This field is required."] }] },
    });
    expect(lineErrorsOf(error)).toEqual([
      { debit: "A valid number is required." },
      { account_id: "This field is required." },
    ]);
  });

  it("returns nothing for a service-rule error with no details", () => {
    const error = new ApiError({
      status: 400,
      code: "journal_line_both_sides",
      message: "Line 2: a line cannot have both debit and credit.",
    });
    expect(lineErrorsOf(error)).toEqual([]);
    expect(lineErrorsOf(new Error("network"))).toEqual([]);
  });
});
