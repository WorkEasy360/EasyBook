import { describe, expect, it } from "vitest";
import type { BalanceSheet, CashFlow, ProfitAndLoss } from "@/types/api/reports";
import { balanceSheetLines, cashFlowLines, humanizeKey, pnlLines, type StatementLine } from "./statement";

/** Captured from the dev tenant, then totals deliberately made NOT the sum of rows. */
const PNL: ProfitAndLoss = {
  period: { from_date: "2026-04-01", to_date: "2026-09-17" },
  currency: "INR",
  sections: {
    operating_income: [
      { account_id: "5b8c5787-cf97-4ee1-8849-739f95f3f2ba", account_code: "4000", account_name: "Sales", amount: "8899.99" },
    ],
    other_income: [],
    cogs: [],
    operating_expenses: [
      {
        account_id: "4657d270-eb7c-41f7-94df-ddfa2a5959a1",
        account_code: "5000",
        account_name: "Cost of Goods Sold",
        amount: "4000.00",
      },
    ],
    other_expense: [],
  },
  totals: {
    revenue: "111.11",
    cogs: "0",
    gross_profit: "222.22",
    operating_expenses: "333.33",
    operating_profit: "444.44",
    other_income: "0",
    other_expense: "0",
    net_profit: "555.55",
  },
};

function totals(lines: StatementLine[]) {
  return Object.fromEntries(
    lines.filter((line) => line.kind === "total").map((line) => [line.label, line.kind === "total" ? line.amount : ""]),
  );
}

describe("pnlLines", () => {
  it("uses the engine's totals verbatim — never a sum of the rows", () => {
    expect(totals(pnlLines(PNL))).toEqual({
      Revenue: "111.11",
      "Total cost of goods sold": "0",
      "Gross profit": "222.22",
      "Total operating expenses": "333.33",
      "Operating profit": "444.44",
      "Total other income": "0",
      "Total other expenses": "0",
      "Net profit": "555.55",
    });
  });

  it("orders the statement for reading and ends on net profit", () => {
    const lines = pnlLines(PNL);
    const headings = lines.filter((line) => line.kind === "heading").map((line) => line.label);
    expect(headings).toEqual([
      "Operating income",
      "Cost of goods sold",
      "Operating expenses",
      "Other income",
      "Other expenses",
    ]);
    expect(lines.at(-1)).toMatchObject({ kind: "total", label: "Net profit", weight: "grand" });
    expect(lines.filter((line) => line.kind === "empty")).toHaveLength(3);
  });

  it("still renders a section the backend adds later, without inventing a total", () => {
    const extended = {
      ...PNL,
      sections: {
        ...PNL.sections,
        exceptional_items: [{ account_id: "x", account_code: "6900", account_name: "Write-off", amount: "10" }],
      },
    } as ProfitAndLoss;
    const lines = pnlLines(extended);
    expect(lines.some((line) => line.kind === "heading" && line.label === "Exceptional items")).toBe(true);
    expect(lines.some((line) => line.kind === "row" && line.label === "Write-off")).toBe(true);
    expect(Object.keys(totals(lines))).toHaveLength(8);
  });
});

describe("balanceSheetLines", () => {
  const sheet: BalanceSheet = {
    as_of_date: "2026-09-17",
    currency: "INR",
    assets: {
      current_assets: [
        { account_id: "a", account_code: "1100", account_name: "Accounts Receivable", amount: "10501.99" },
      ],
      fixed_assets: [],
      other_assets: [],
    },
    liabilities: { current_liabilities: [], long_term_liabilities: [] },
    equity: [
      { account_id: "e", account_code: "3000", account_name: "Owner Equity", amount: "40000.00" },
      { account_id: null, account_code: "", account_name: "Current Period Earnings", amount: "4899.99" },
    ],
    totals: {
      total_assets: "46501.99",
      total_liabilities: "1602.00",
      total_equity: "44899.99",
      total_liabilities_and_equity: "46501.99",
    },
    is_balanced: true,
  };

  it("omits empty sub-sections and keeps the computed earnings line un-linkable", () => {
    const lines = balanceSheetLines(sheet);
    expect(lines.some((line) => line.kind === "heading" && line.label === "Fixed assets")).toBe(false);
    expect(lines.find((line) => line.kind === "row" && line.label === "Current Period Earnings")).toMatchObject({
      accountId: null,
      code: "",
    });
    expect(lines.find((line) => line.key === "liabilities:empty")).toBeDefined();
    expect(totals(lines)).toEqual({
      "Total assets": "46501.99",
      "Total liabilities": "1602.00",
      "Total equity": "44899.99",
      "Total liabilities and equity": "46501.99",
    });
  });
});

describe("cashFlowLines", () => {
  it("shows opening, each activity total and closing cash as returned", () => {
    const flow: CashFlow = {
      period: { from_date: "2026-04-01", to_date: "2026-09-17" },
      currency: "INR",
      opening_cash: "0",
      operating_activities: {
        net_profit: "4899.99",
        adjustments: [{ account_id: "a", account_code: "1100", account_name: "Accounts Receivable", amount: "-10501.99" }],
        total: "-40000.00",
      },
      investing_activities: { items: [], total: "0" },
      financing_activities: {
        items: [{ account_id: "e", account_code: "3000", account_name: "Owner Equity", amount: "40000.00" }],
        total: "40000.00",
      },
      net_change_in_cash: "0",
      closing_cash: "0",
      cash_accounts: [],
      reconciles: true,
    };
    const lines = cashFlowLines(flow);
    expect(totals(lines)).toEqual({
      "Opening cash": "0",
      "Net cash from operating activities": "-40000.00",
      "Net cash from investing activities": "0",
      "Net cash from financing activities": "40000.00",
      "Net change in cash": "0",
      "Closing cash": "0",
    });
    expect(lines.find((line) => line.key === "operating:net_profit")).toMatchObject({ amount: "4899.99", accountId: null });
  });
});

describe("humanizeKey", () => {
  it("reads a snake_case key", () => {
    expect(humanizeKey("long_term_liabilities")).toBe("Long term liabilities");
  });
});
