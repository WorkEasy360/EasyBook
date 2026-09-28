import type { DateString } from "@/lib/api/types";
import { currentFiscalYear } from "@/lib/fiscal";

/**
 * Fiscal-year onboarding helpers. Pure date arithmetic only: the backend
 * (accounting.services.fiscal.create_fiscal_year) is the authority on what
 * range is acceptable — these checks exist so the form can explain a mistake
 * before a round trip, and they mirror, never extend, the server's rules.
 */

/** Mirrors accounting.services.fiscal.MAX_FISCAL_YEAR_MONTHS. */
export const MAX_FISCAL_YEAR_MONTHS = 18;

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/**
 * The suggested first fiscal year: the one containing `today`, starting at the
 * organization's own configured month (April for India's default). Only a
 * prefill — the user confirms or edits both dates.
 */
export function suggestedFiscalYear(today: DateString, startMonth: number): { start_date: DateString; end_date: DateString } {
  const month = Number.isInteger(startMonth) && startMonth >= 1 && startMonth <= 12 ? startMonth : 1;
  const period = currentFiscalYear(today, month);
  return { start_date: period.from_date, end_date: period.to_date };
}

function firstDayMonthsAfter(date: DateString, months: number): DateString {
  const year = Number(date.slice(0, 4));
  const index = Number(date.slice(5, 7)) - 1 + months;
  const y = year + Math.floor(index / 12);
  const m = (index % 12) + 1;
  return `${y}-${String(m).padStart(2, "0")}-01`;
}

/** Returns a user-facing problem with the range, or null when it looks acceptable. */
export function fiscalYearRangeProblem(start: string, end: string): string | null {
  if (!ISO_DATE.test(start) || Number.isNaN(Date.parse(`${start}T00:00:00Z`))) return "Enter the first day of the fiscal year.";
  if (!ISO_DATE.test(end) || Number.isNaN(Date.parse(`${end}T00:00:00Z`))) return "Enter the last day of the fiscal year.";
  // ISO dates compare correctly as strings.
  if (end <= start) return "The fiscal year must end after it starts.";
  if (end >= firstDayMonthsAfter(start, MAX_FISCAL_YEAR_MONTHS)) {
    return `A fiscal year cannot be longer than ${MAX_FISCAL_YEAR_MONTHS} months.`;
  }
  return null;
}

/** Whether `today` falls inside the range — the app needs a CURRENT fiscal year to continue. */
export function coversToday(start: string, end: string, today: DateString): boolean {
  return start <= today && today <= end;
}
