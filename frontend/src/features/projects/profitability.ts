import type { DecimalString } from "@/lib/api/types";
import type { ProjectProfitability } from "@/types/api/projects";

/**
 * Normalises GET /projects/{id}/profitability/ to decimal strings.
 *
 * BACKEND BUG (fixed in projects/api/views.py, live once Django restarts):
 * the view returned the selector's Decimals bare, and DRF's JSON encoder
 * renders a bare Decimal as a JSON float — `"revenue": 5850.0`. The figures
 * are already floats by the time they reach the browser, so no precision can
 * be recovered here; this only stops a number flowing into <Money>, which
 * expects the string form every other endpoint sends. Once the fixed view is
 * deployed every value is already a string and passes through untouched.
 */
const MONEY_AND_HOURS = [
  "revenue",
  "unbilled_value",
  "labour_cost",
  "expense_cost",
  "total_cost",
  "margin",
  "hours_over_budget",
  "total_hours",
  "billable_hours",
  "non_billable_hours",
  "approved_hours",
  "invoiced_hours",
  "pending_approval_hours",
] as const;

const NULLABLE = ["margin_percent", "budget_amount", "budget_hours"] as const;

function asDecimal(value: unknown): DecimalString {
  if (typeof value === "string") return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return "0";
}

export function normalizeProfitability(raw: unknown): ProjectProfitability {
  const source = (typeof raw === "object" && raw !== null ? raw : {}) as Record<string, unknown>;
  const out: Record<string, unknown> = { ...source };
  for (const key of MONEY_AND_HOURS) out[key] = asDecimal(source[key]);
  for (const key of NULLABLE) {
    const value = source[key];
    out[key] = value === null || value === undefined ? null : asDecimal(value);
  }
  return out as unknown as ProjectProfitability;
}
