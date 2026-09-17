import type { DecimalString } from "@/lib/api/types";
import {
  BALANCE_SHEET_ASSET_SECTIONS,
  BALANCE_SHEET_LIABILITY_SECTIONS,
  PNL_SECTIONS,
  type BalanceSheet,
  type CashFlow,
  type CashFlowItem,
  type ProfitAndLoss,
  type StatementRow,
} from "@/types/api/reports";

/**
 * Turns a statement payload into display lines.
 *
 * Pure layout — no arithmetic. Every amount on a line is a value the API
 * returned (an account row, or a named total from `totals`); nothing is summed
 * here, so a subtotal can never disagree with the engine. Sections the API
 * adds later are still rendered (after the known ones), rather than silently
 * dropped by a fixed list.
 */

export type StatementLine =
  | { kind: "heading"; key: string; label: string; level: 1 | 2 }
  | {
      kind: "row";
      key: string;
      label: string;
      /** "" for computed lines with no account behind them. */
      code: string;
      /** Null when there is no account to drill into. */
      accountId: string | null;
      amount: DecimalString;
    }
  | { kind: "empty"; key: string; label: string }
  | { kind: "total"; key: string; label: string; amount: DecimalString; weight: "section" | "subtotal" | "grand" };

export const PNL_SECTION_LABELS: Record<string, string> = {
  operating_income: "Operating income",
  other_income: "Other income",
  cogs: "Cost of goods sold",
  operating_expenses: "Operating expenses",
  other_expense: "Other expenses",
};

export const BALANCE_SECTION_LABELS: Record<string, string> = {
  current_assets: "Current assets",
  fixed_assets: "Fixed assets",
  other_assets: "Other assets",
  current_liabilities: "Current liabilities",
  long_term_liabilities: "Long-term liabilities",
};

/** "long_term_liabilities" → "Long term liabilities", for a key this build does not know. */
export function humanizeKey(key: string): string {
  const text = key.replace(/_/g, " ").trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : key;
}

function accountRows(prefix: string, rows: StatementRow[] | CashFlowItem[]): StatementLine[] {
  return rows.map((row, index) => ({
    kind: "row" as const,
    key: `${prefix}:${row.account_id ?? `computed-${index}`}`,
    label: row.account_name,
    code: row.account_code,
    accountId: row.account_id,
    amount: row.amount,
  }));
}

function sectionBody(prefix: string, rows: StatementRow[] | CashFlowItem[] | undefined, emptyLabel: string): StatementLine[] {
  if (!rows || rows.length === 0) return [{ kind: "empty", key: `${prefix}:empty`, label: emptyLabel }];
  return accountRows(prefix, rows);
}

/** Known keys first, in the given order, then anything else the payload carries. */
function orderedKeys(record: Record<string, unknown>, known: readonly string[]): string[] {
  const extra = Object.keys(record).filter((key) => !known.includes(key));
  return [...known.filter((key) => key in record), ...extra];
}

/**
 * P&L in reading order: income, cost of sales, gross profit, operating
 * expenses, operating profit, other income and expense, net profit. The API
 * returns sections in its own order; each section is placed beside the total
 * the engine computed for it.
 */
export function pnlLines(pnl: ProfitAndLoss): StatementLine[] {
  const { sections, totals } = pnl;
  const lines: StatementLine[] = [];

  const block = (key: string, totalLabel: string, total: DecimalString) => {
    lines.push({ kind: "heading", key: `h:${key}`, label: PNL_SECTION_LABELS[key] ?? humanizeKey(key), level: 1 });
    lines.push(...sectionBody(key, sections[key as keyof typeof sections], "No accounts in this section"));
    lines.push({ kind: "total", key: `t:${key}`, label: totalLabel, amount: total, weight: "section" });
  };

  block("operating_income", "Revenue", totals.revenue);
  block("cogs", "Total cost of goods sold", totals.cogs);
  lines.push({ kind: "total", key: "t:gross_profit", label: "Gross profit", amount: totals.gross_profit, weight: "subtotal" });
  block("operating_expenses", "Total operating expenses", totals.operating_expenses);
  lines.push({
    kind: "total",
    key: "t:operating_profit",
    label: "Operating profit",
    amount: totals.operating_profit,
    weight: "subtotal",
  });
  block("other_income", "Total other income", totals.other_income);
  block("other_expense", "Total other expenses", totals.other_expense);

  // A section the engine added after this build: shown, but with no total —
  // there is no API figure to put beside it and the UI will not invent one.
  for (const key of orderedKeys(sections, PNL_SECTIONS).filter((key) => !(PNL_SECTIONS as readonly string[]).includes(key))) {
    lines.push({ kind: "heading", key: `h:${key}`, label: humanizeKey(key), level: 1 });
    lines.push(...sectionBody(key, sections[key as keyof typeof sections], "No accounts in this section"));
  }

  lines.push({ kind: "total", key: "t:net_profit", label: "Net profit", amount: totals.net_profit, weight: "grand" });
  return lines;
}

function subsections(
  group: string,
  record: Record<string, StatementRow[]>,
  known: readonly string[],
): StatementLine[] {
  const lines: StatementLine[] = [];
  for (const key of orderedKeys(record, known)) {
    const rows = record[key] ?? [];
    // Empty sub-sections carry no figure of their own, so they are omitted
    // rather than listed as a column of "none".
    if (rows.length === 0) continue;
    lines.push({ kind: "heading", key: `h:${group}:${key}`, label: BALANCE_SECTION_LABELS[key] ?? humanizeKey(key), level: 2 });
    lines.push(...accountRows(`${group}:${key}`, rows));
  }
  if (lines.length === 0) lines.push({ kind: "empty", key: `${group}:empty`, label: "No balances" });
  return lines;
}

export function balanceSheetLines(sheet: BalanceSheet): StatementLine[] {
  return [
    { kind: "heading", key: "h:assets", label: "Assets", level: 1 },
    ...subsections("assets", sheet.assets, BALANCE_SHEET_ASSET_SECTIONS),
    { kind: "total", key: "t:assets", label: "Total assets", amount: sheet.totals.total_assets, weight: "subtotal" },
    { kind: "heading", key: "h:liabilities", label: "Liabilities", level: 1 },
    ...subsections("liabilities", sheet.liabilities, BALANCE_SHEET_LIABILITY_SECTIONS),
    {
      kind: "total",
      key: "t:liabilities",
      label: "Total liabilities",
      amount: sheet.totals.total_liabilities,
      weight: "section",
    },
    { kind: "heading", key: "h:equity", label: "Equity", level: 1 },
    ...sectionBody("equity", sheet.equity, "No balances"),
    { kind: "total", key: "t:equity", label: "Total equity", amount: sheet.totals.total_equity, weight: "section" },
    {
      kind: "total",
      key: "t:liabilities_equity",
      label: "Total liabilities and equity",
      amount: sheet.totals.total_liabilities_and_equity,
      weight: "grand",
    },
  ];
}

export function cashFlowLines(flow: CashFlow): StatementLine[] {
  return [
    { kind: "total", key: "t:opening", label: "Opening cash", amount: flow.opening_cash, weight: "subtotal" },
    { kind: "heading", key: "h:operating", label: "Operating activities", level: 1 },
    {
      kind: "row",
      key: "operating:net_profit",
      label: "Net profit for the period",
      code: "",
      accountId: null,
      amount: flow.operating_activities.net_profit,
    },
    ...accountRows("operating", flow.operating_activities.adjustments),
    {
      kind: "total",
      key: "t:operating",
      label: "Net cash from operating activities",
      amount: flow.operating_activities.total,
      weight: "section",
    },
    { kind: "heading", key: "h:investing", label: "Investing activities", level: 1 },
    ...sectionBody("investing", flow.investing_activities.items, "No investing movements"),
    {
      kind: "total",
      key: "t:investing",
      label: "Net cash from investing activities",
      amount: flow.investing_activities.total,
      weight: "section",
    },
    { kind: "heading", key: "h:financing", label: "Financing activities", level: 1 },
    ...sectionBody("financing", flow.financing_activities.items, "No financing movements"),
    {
      kind: "total",
      key: "t:financing",
      label: "Net cash from financing activities",
      amount: flow.financing_activities.total,
      weight: "section",
    },
    { kind: "total", key: "t:net_change", label: "Net change in cash", amount: flow.net_change_in_cash, weight: "subtotal" },
    { kind: "total", key: "t:closing", label: "Closing cash", amount: flow.closing_cash, weight: "grand" },
  ];
}
