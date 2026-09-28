import type { DateString } from "@/lib/api/types";
import { addDays, todayInZone } from "@/lib/datetime";
import { currentFiscalYear, currentMonth, currentQuarter, resolvePeriod } from "@/lib/fiscal";
import { DEFAULT_PAGE_SIZE, dateParamOf, paramOf, type RawSearchParams } from "@/lib/list-query";

/**
 * Report period plumbing: which dates to ASK the engine about.
 *
 * Calendar arithmetic only — choosing a range is not accounting logic. Every
 * figure shown for that range still comes from the backend. Periods live in
 * the URL so a report is a shareable, bookmarkable link (spec §66).
 */

// A type alias, not an interface: it is spread into query records, and only
// aliases get the implicit index signature those need.
export type DateRange = {
  from_date: DateString;
  to_date: DateString;
};

export interface PresetLink {
  label: string;
  href: string;
  /** True when the page is currently showing exactly this preset. */
  active: boolean;
}

/** Query values for an href. Undefined and empty entries are dropped. */
export type QueryValues = Record<string, string | undefined>;

export function hrefWith(pathname: string, params: QueryValues): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) search.set(key, value);
  }
  const query = search.toString();
  return query ? `${pathname}?${query}` : pathname;
}

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isUuid(value: string | undefined | null): value is string {
  return typeof value === "string" && UUID_PATTERN.test(value);
}

/**
 * A UUID param, or undefined. The report views look ids up with
 * `Model.objects.get(pk=…)`, and a malformed id there raises Django's
 * ValidationError, which the views do not catch — a hand-edited URL would
 * surface as a 500. So a non-UUID never reaches the API.
 */
export function uuidParamOf(params: RawSearchParams, key: string): string | undefined {
  const value = paramOf(params, key);
  return isUuid(value) ? value : undefined;
}

/**
 * The reporting period from the URL, defaulting to the fiscal year to date —
 * never the calendar year (src/lib/fiscal.ts explains why).
 */
export function resolveReportRange(
  params: RawSearchParams,
  timeZone: string,
  fiscalStartMonth: number,
  keys: { from: string; to: string } = { from: "from_date", to: "to_date" },
): DateRange {
  const period = resolvePeriod(
    { from_date: dateParamOf(params, keys.from), to_date: dateParamOf(params, keys.to) },
    timeZone,
    fiscalStartMonth,
  );
  return { from_date: period.from_date, to_date: period.to_date };
}

/**
 * GST reports REQUIRE both dates (400 `date_range_required`), and a return is
 * filed per month, so the default is the current month in the organization's
 * timezone rather than the fiscal year.
 */
export function resolveMonthRange(params: RawSearchParams, timeZone: string): DateRange {
  const month = currentMonth(todayInZone(timeZone));
  return {
    from_date: dateParamOf(params, "from_date") ?? month.from_date,
    to_date: dateParamOf(params, "to_date") ?? month.to_date,
  };
}

/** A from date after the to date is not an error to the API — it just returns nothing. */
export function isReversedRange(range: { from_date?: string | null; to_date?: string | null }): boolean {
  return Boolean(range.from_date && range.to_date && range.from_date > range.to_date);
}

function previousFiscalYear(today: DateString, fiscalStartMonth: number) {
  const fiscal = currentFiscalYear(today, fiscalStartMonth);
  const dayBefore = addDays(fiscal.from_date, -1) ?? fiscal.from_date;
  return currentFiscalYear(dayBefore, fiscalStartMonth);
}

/** This month / This quarter / This fiscal year / Previous fiscal year. */
export function rangePresets(
  today: DateString,
  fiscalStartMonth: number,
): Array<DateRange & { label: string }> {
  const month = currentMonth(today);
  const quarter = currentQuarter(today);
  const fiscal = currentFiscalYear(today, fiscalStartMonth);
  const previous = previousFiscalYear(today, fiscalStartMonth);
  return [
    { label: "This month", from_date: month.from_date, to_date: month.to_date },
    { label: "This quarter", from_date: quarter.from_date, to_date: quarter.to_date },
    { label: `This fiscal year (${fiscal.label})`, from_date: fiscal.from_date, to_date: fiscal.to_date },
    {
      label: `Previous fiscal year (${previous.label})`,
      from_date: previous.from_date,
      to_date: previous.to_date,
    },
  ];
}

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
] as const;

/** The current month and the `count - 1` months before it, labelled by name. */
export function monthPresets(today: DateString, count = 3): Array<DateRange & { label: string }> {
  const presets: Array<DateRange & { label: string }> = [];
  let anchor: DateString = today;
  for (let index = 0; index < count; index += 1) {
    const month = currentMonth(anchor);
    const [year, monthPart] = month.from_date.split("-");
    const name = MONTH_NAMES[Number(monthPart) - 1] ?? month.from_date;
    presets.push({
      label: index === 0 ? `This month (${name} ${year})` : `${name} ${year}`,
      from_date: month.from_date,
      to_date: month.to_date,
    });
    anchor = addDays(month.from_date, -1) ?? month.from_date;
  }
  return presets;
}

/** Point-in-time presets for as-of reports (balance sheet, ageing, valuation). */
export function asOfPresets(today: DateString, fiscalStartMonth: number): Array<{ label: string; date: DateString }> {
  const month = currentMonth(today);
  const quarter = currentQuarter(today);
  const fiscal = currentFiscalYear(today, fiscalStartMonth);
  return [
    { label: "Today", date: today },
    { label: "End of last month", date: addDays(month.from_date, -1) ?? today },
    { label: "End of last quarter", date: addDays(quarter.from_date, -1) ?? today },
    { label: "End of last fiscal year", date: addDays(fiscal.from_date, -1) ?? today },
  ];
}

/** Preset links for a from/to report, preserving the page's other filters. */
export function rangePresetLinks(
  pathname: string,
  presets: Array<DateRange & { label: string }>,
  current: DateRange,
  preserve: QueryValues = {},
): PresetLink[] {
  return presets.map((preset) => ({
    label: preset.label,
    href: hrefWith(pathname, { ...preserve, from_date: preset.from_date, to_date: preset.to_date }),
    active: preset.from_date === current.from_date && preset.to_date === current.to_date,
  }));
}

/** Preset links for an as-of report. */
export function asOfPresetLinks(
  pathname: string,
  presets: Array<{ label: string; date: DateString }>,
  current: DateString | undefined,
  preserve: QueryValues = {},
  key = "as_of_date",
): PresetLink[] {
  return presets.map((preset) => ({
    label: preset.label,
    href: hrefWith(pathname, { ...preserve, [key]: preset.date }),
    active: preset.date === current,
  }));
}

/**
 * Page links for a paginated report (journals, stock movements/adjustments).
 * parseListQuery only carries filters listed in LIST_CAPABILITIES, which does
 * not cover report endpoints, so the report's own filters are passed here and
 * preserved on every page link.
 */
export function pagedHrefBuilder(
  pathname: string,
  filters: QueryValues,
  pageSize: number,
): (page: number) => string {
  return (page) =>
    hrefWith(pathname, {
      ...filters,
      page: page > 1 ? String(page) : undefined,
      page_size: pageSize !== DEFAULT_PAGE_SIZE ? String(pageSize) : undefined,
    });
}

/**
 * The instant a report page was rendered, for the "figures as of" note. Kept
 * out of component bodies: reading the clock is impure, and the note states
 * when the engine was asked, not a value any component should derive.
 */
export function reportTimestamp(): string {
  return new Date().toISOString();
}
