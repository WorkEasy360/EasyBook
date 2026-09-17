import type { DateString } from "./api/types";
import { todayInZone } from "./datetime";

/**
 * Fiscal period helpers.
 *
 * Every report takes `from_date`/`to_date` or `as_of_date`, and "this year"
 * in an accounting product means the FISCAL year, which starts at the
 * organization's `fiscal_year_start_month` (4 for India's April–March). Using
 * the calendar year instead would silently report the wrong period.
 *
 * This is calendar arithmetic, not accounting logic — it only chooses the
 * dates to ask the engine about. Every figure still comes from the backend.
 */

export interface Period {
  from_date: DateString;
  to_date: DateString;
  label: string;
}

function iso(year: number, month: number, day: number): DateString {
  return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

function lastDayOfMonth(year: number, month: number): number {
  // Day 0 of the next month is the last day of this one.
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

/** The fiscal year containing `today`, for an organization starting at `startMonth`. */
export function currentFiscalYear(today: DateString, startMonth: number): Period {
  const [yearPart, monthPart] = today.split("-");
  const year = Number(yearPart);
  const month = Number(monthPart);

  // Before the start month, we are still in the fiscal year that began last
  // calendar year — e.g. February 2026 is in FY 2025-26 when April starts it.
  const startYear = month >= startMonth ? year : year - 1;
  const endYear = startMonth === 1 ? startYear : startYear + 1;
  const endMonth = startMonth === 1 ? 12 : startMonth - 1;

  return {
    from_date: iso(startYear, startMonth, 1),
    to_date: iso(endYear, endMonth, lastDayOfMonth(endYear, endMonth)),
    label:
      startMonth === 1
        ? `FY ${startYear}`
        : `FY ${startYear}–${String(endYear).slice(-2)}`,
  };
}

export function currentMonth(today: DateString): Period {
  const [yearPart, monthPart] = today.split("-");
  const year = Number(yearPart);
  const month = Number(monthPart);
  return {
    from_date: iso(year, month, 1),
    to_date: iso(year, month, lastDayOfMonth(year, month)),
    label: "This month",
  };
}

export function currentQuarter(today: DateString): Period {
  const [yearPart, monthPart] = today.split("-");
  const year = Number(yearPart);
  const month = Number(monthPart);
  const startMonth = Math.floor((month - 1) / 3) * 3 + 1;
  const endMonth = startMonth + 2;
  return {
    from_date: iso(year, startMonth, 1),
    to_date: iso(year, endMonth, lastDayOfMonth(year, endMonth)),
    label: "This quarter",
  };
}

/** Same length as `period`, immediately before it — for a comparison column. */
export function previousPeriod(period: Period): Period {
  const from = new Date(`${period.from_date}T00:00:00Z`);
  const to = new Date(`${period.to_date}T00:00:00Z`);
  const lengthMs = to.getTime() - from.getTime();

  const previousTo = new Date(from.getTime() - 86_400_000);
  const previousFrom = new Date(previousTo.getTime() - lengthMs);

  return {
    from_date: previousFrom.toISOString().slice(0, 10),
    to_date: previousTo.toISOString().slice(0, 10),
    label: "Previous period",
  };
}

/** Named presets for a report's period picker. */
export function periodPresets(today: DateString, fiscalStartMonth: number): Period[] {
  const [yearPart] = today.split("-");
  const year = Number(yearPart);
  const fiscal = currentFiscalYear(today, fiscalStartMonth);
  const previousFiscal = currentFiscalYear(
    iso(Number(fiscal.from_date.slice(0, 4)) - 1, fiscalStartMonth, 1),
    fiscalStartMonth,
  );

  return [
    currentMonth(today),
    currentQuarter(today),
    fiscal,
    { ...previousFiscal, label: `Previous ${previousFiscal.label}` },
    {
      from_date: iso(year, 1, 1),
      to_date: today,
      label: "Calendar year to date",
    },
  ];
}

/** Resolves a report period from URL params, falling back to the fiscal year. */
export function resolvePeriod(
  params: { from_date?: string; to_date?: string },
  timeZone: string,
  fiscalStartMonth: number,
): Period {
  const today = todayInZone(timeZone);
  const fallback = currentFiscalYear(today, fiscalStartMonth);
  const from = isDate(params.from_date) ? params.from_date : fallback.from_date;
  const to = isDate(params.to_date) ? params.to_date : today;
  return { from_date: from, to_date: to, label: "Custom" };
}

function isDate(value: string | undefined): value is DateString {
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value);
}
