import { describe, expect, it } from "vitest";
import {
  EMPTY_MAPPING,
  cellFor,
  mappingErrors,
  normalizeHeader,
  readCsv,
  suggestMapping,
  toMappingPayload,
} from "./csv";
import { confidencePercent, isPositiveAmount, signedAmount, unsigned } from "./amounts";

describe("readCsv", () => {
  it("reads the header and the first rows, skipping blank lines", () => {
    const preview = readCsv("Date,Narration,Amount\r\n05/09/2026,Charges,-118.00\r\n\r\n08/09/2026,NEFT,25000\r\n", 5);
    expect(preview.headers).toEqual(["Date", "Narration", "Amount"]);
    expect(preview.rows).toEqual([
      ["05/09/2026", "Charges", "-118.00"],
      ["08/09/2026", "NEFT", "25000"],
    ]);
    expect(preview.rowCount).toBe(2);
  });

  it("keeps commas and escaped quotes inside quoted cells, as Python's csv module does", () => {
    const preview = readCsv('Date,Narration,Amount\n05/09/2026,"Rent, ""Sept""","1,500.00"');
    expect(preview.rows[0]).toEqual(["05/09/2026", 'Rent, "Sept"', "1,500.00"]);
  });

  it("limits the preview but still counts every row", () => {
    const text = ["Date,Amount", ...Array.from({ length: 12 }, (_, i) => `2026-09-${10 + i},${i + 1}`)].join("\n");
    const preview = readCsv(text, 3);
    expect(preview.rows).toHaveLength(3);
    expect(preview.rowCount).toBe(12);
  });

  it("returns no headers for an empty file", () => {
    expect(readCsv("").headers).toEqual([]);
  });
});

describe("normalizeHeader", () => {
  it("matches the backend's comparison: trimmed, lower-case, underscores as spaces, no dots", () => {
    expect(normalizeHeader("  Txn_Date ")).toBe("txn date");
    expect(normalizeHeader("Chq. No.")).toBe("chq no");
  });
});

describe("suggestMapping", () => {
  it("pre-fills debit/credit mode when both columns are recognised", () => {
    const suggestion = suggestMapping(["Txn Date", "Narration", "Withdrawal Amt.", "Deposit Amt.", "Amount", "UTR"]);
    expect(suggestion).toMatchObject({
      date_column: "Txn Date",
      description_column: "Narration",
      debit_column: "Withdrawal Amt.",
      credit_column: "Deposit Amt.",
      reference_column: "UTR",
      amount_mode: "debit_credit",
    });
    expect(suggestion.amount_column).toBeUndefined();
  });

  it("pre-fills signed mode from a single amount column", () => {
    const suggestion = suggestMapping(["Date", "Details", "Amount"]);
    expect(suggestion).toMatchObject({ date_column: "Date", amount_column: "Amount", amount_mode: "signed" });
  });

  it("suggests nothing it does not recognise", () => {
    expect(suggestMapping(["Col A", "Col B"])).toEqual({});
  });
});

describe("mappingErrors", () => {
  it("requires the columns the amount mode needs", () => {
    expect(mappingErrors({ ...EMPTY_MAPPING })).toMatchObject({
      date_column: expect.any(String),
      amount_column: expect.any(String),
    });
    const debitCredit = mappingErrors({ ...EMPTY_MAPPING, date_column: "Date", amount_mode: "debit_credit" });
    expect(Object.keys(debitCredit).sort()).toEqual(["credit_column", "debit_column"]);
    const indicator = mappingErrors({ ...EMPTY_MAPPING, date_column: "Date", amount_mode: "indicator", amount_column: "Amt" });
    expect(Object.keys(indicator)).toEqual(["indicator_column"]);
  });

  it("refuses the same column for withdrawals and deposits", () => {
    const errors = mappingErrors({
      ...EMPTY_MAPPING,
      date_column: "Date",
      amount_mode: "debit_credit",
      debit_column: "Amount",
      credit_column: "Amount",
    });
    expect(errors.credit_column).toMatch(/different/);
  });

  it("passes a complete signed mapping", () => {
    expect(mappingErrors({ ...EMPTY_MAPPING, date_column: "Date", amount_column: "Amount" })).toEqual({});
  });
});

describe("toMappingPayload", () => {
  it("sends only the columns the chosen mode uses, and the chosen date format", () => {
    const payload = toMappingPayload({
      ...EMPTY_MAPPING,
      date_column: "Date",
      date_format: "%d/%m/%Y",
      amount_mode: "debit_credit",
      amount_column: "Stale amount choice",
      debit_column: "Withdrawal",
      credit_column: "Deposit",
      description_column: "Narration",
    });
    expect(payload).toEqual({
      date_column: "Date",
      amount_mode: "debit_credit",
      debit_column: "Withdrawal",
      credit_column: "Deposit",
      description_column: "Narration",
      counterparty_column: "",
      reference_column: "",
      external_id_column: "",
      date_formats: ["%d/%m/%Y"],
      invert_sign: false,
    });
  });

  it("leaves date_formats empty so the server tries its defaults", () => {
    expect(toMappingPayload({ ...EMPTY_MAPPING, date_column: "Date", amount_column: "Amount" }).date_formats).toEqual([]);
  });
});

describe("cellFor", () => {
  it("reads a raw cell by header name", () => {
    expect(cellFor(["Date", "Amount"], ["05/09/2026", "-118.00"], "Amount")).toBe("-118.00");
    expect(cellFor(["Date"], ["05/09/2026"], "")).toBe("");
    expect(cellFor(["Date"], ["05/09/2026"], "Missing")).toBe("");
  });
});

describe("amount helpers", () => {
  it("puts the sign on a decimal string without arithmetic", () => {
    expect(signedAmount("1250.50", "out")).toBe("-1250.50");
    expect(signedAmount("-1250.50", "in")).toBe("1250.50");
    expect(signedAmount("0.10", "in")).toBe("0.10");
    expect(unsigned("-99999999999999.99")).toBe("99999999999999.99");
  });

  it("accepts only positive, non-zero amounts", () => {
    expect(isPositiveAmount("0.01")).toBe(true);
    expect(isPositiveAmount("0")).toBe(false);
    expect(isPositiveAmount("-5")).toBe(false);
    expect(isPositiveAmount("abc")).toBe(false);
  });

  it("shows the matcher's confidence as a percentage", () => {
    expect(confidencePercent("0.800")).toBe("80%");
    expect(confidencePercent("1.000")).toBe("100%");
    expect(confidencePercent("")).toBe("—");
  });
});
