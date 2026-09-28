import type { DateString, DateTimeString } from "./api/types";

/**
 * Date and time presentation (spec §72). One module so timezone handling is
 * never scattered through feature code.
 *
 * The distinction that matters in an accounting product:
 *
 *  - A DateString ("2026-03-31") is a CALENDAR date — an invoice date, a
 *    period end. It has no timezone and must never be converted through one.
 *    `new Date("2026-03-31")` parses as UTC midnight, so rendering it in
 *    Asia/Kolkata is fine but rendering it in America/Los_Angeles shows the
 *    30th — an off-by-one on a fiscal year boundary. formatDate() therefore
 *    formats the parts literally and never touches a timezone.
 *
 *  - A DateTimeString is an INSTANT — when a journal was posted, when a
 *    document finished OCR. That does get converted, into the organization's
 *    configured timezone, so two users in different places agree on it.
 */

/** Falls back to the org default when a user preference is not set. */
export interface TimeFormatContext {
  /** IANA zone from the organization, e.g. "Asia/Kolkata". */
  timeZone: string;
  locale?: string;
}

const MONTHS_SHORT = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
] as const;

interface DateParts {
  year: number;
  month: number;
  day: number;
}

/** Splits "2026-03-31" without going through Date, avoiding any zone shift. */
export function parseDateString(value: DateString | null | undefined): DateParts | null {
  if (!value) return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (!match) return null;
  const [, y, m, d] = match;
  if (!y || !m || !d) return null;
  return { year: Number(y), month: Number(m), day: Number(d) };
}

/**
 * A calendar date as "31 Mar 2026". No timezone is applied — see the module
 * comment for why that is deliberate rather than an omission.
 */
export function formatDate(value: DateString | null | undefined): string {
  const parts = parseDateString(value);
  if (!parts) return "—";
  const month = MONTHS_SHORT[parts.month - 1];
  if (!month) return "—";
  return `${String(parts.day).padStart(2, "0")} ${month} ${parts.year}`;
}

/** Numeric form for dense table columns: "31/03/2026". */
export function formatDateNumeric(value: DateString | null | undefined): string {
  const parts = parseDateString(value);
  if (!parts) return "—";
  return `${String(parts.day).padStart(2, "0")}/${String(parts.month).padStart(2, "0")}/${parts.year}`;
}

/** The value an <input type="date"> expects. Identity for a valid DateString. */
export function toDateInputValue(value: DateString | null | undefined): string {
  const parts = parseDateString(value);
  if (!parts) return "";
  return `${parts.year}-${String(parts.month).padStart(2, "0")}-${String(parts.day).padStart(2, "0")}`;
}

/** Today in the organization's timezone — not the browser's. */
export function todayInZone(timeZone: string): DateString {
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  // en-CA yields exactly YYYY-MM-DD.
  return formatter.format(new Date());
}

/** An instant rendered in the organization's timezone: "31 Mar 2026, 14:05". */
export function formatDateTime(
  value: DateTimeString | null | undefined,
  context: TimeFormatContext,
): string {
  if (!value) return "—";
  const instant = new Date(value);
  if (Number.isNaN(instant.getTime())) return "—";
  return new Intl.DateTimeFormat(context.locale ?? "en-IN", {
    timeZone: context.timeZone,
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    // hourCycle, NOT hour12:false — the latter selects the h24 clock, which
    // renders midnight as "24:00". h23 is the 00–23 cycle people expect.
    hourCycle: "h23",
  }).format(instant);
}

/** Date only, but derived from an instant, so the zone does matter here. */
export function formatInstantAsDate(
  value: DateTimeString | null | undefined,
  context: TimeFormatContext,
): string {
  if (!value) return "—";
  const instant = new Date(value);
  if (Number.isNaN(instant.getTime())) return "—";
  return new Intl.DateTimeFormat(context.locale ?? "en-IN", {
    timeZone: context.timeZone,
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(instant);
}

/**
 * "3 days ago" for activity feeds. Always paired with the absolute time in a
 * title/tooltip — a relative stamp alone is not good enough for an audit or
 * activity trail.
 */
export function formatRelative(
  value: DateTimeString | null | undefined,
  locale = "en-IN",
): string {
  if (!value) return "—";
  const instant = new Date(value);
  if (Number.isNaN(instant.getTime())) return "—";

  const deltaSeconds = Math.round((instant.getTime() - Date.now()) / 1000);
  const absolute = Math.abs(deltaSeconds);
  const formatter = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });

  const divisions: Array<[number, Intl.RelativeTimeFormatUnit]> = [
    [60, "second"],
    [3600, "minute"],
    [86_400, "hour"],
    [604_800, "day"],
    [2_629_800, "week"],
    [31_557_600, "month"],
  ];

  if (absolute < 60) return formatter.format(deltaSeconds, "second");
  for (let i = 1; i < divisions.length; i += 1) {
    const entry = divisions[i];
    const previous = divisions[i - 1];
    if (!entry || !previous) break;
    if (absolute < entry[0]) {
      return formatter.format(Math.round(deltaSeconds / previous[0]), entry[1]);
    }
  }
  return formatter.format(Math.round(deltaSeconds / 31_557_600), "year");
}

/** Days between two calendar dates. Used for ageing buckets and overdue counts. */
/**
 * Calendar arithmetic on a DateString — "2026-01-31" + 30 days. Done in UTC
 * on purpose: a date has no zone, and UTC has no daylight-saving jumps, so
 * adding days can never land on the wrong calendar day.
 */
export function addDays(value: DateString, days: number): DateString | null {
  const parts = parseDateString(value);
  if (!parts || !Number.isFinite(days)) return null;
  const shifted = new Date(Date.UTC(parts.year, parts.month - 1, parts.day + Math.trunc(days)));
  return shifted.toISOString().slice(0, 10);
}

export function daysBetween(from: DateString, to: DateString): number | null {
  const a = parseDateString(from);
  const b = parseDateString(to);
  if (!a || !b) return null;
  const aUtc = Date.UTC(a.year, a.month - 1, a.day);
  const bUtc = Date.UTC(b.year, b.month - 1, b.day);
  return Math.round((bUtc - aUtc) / 86_400_000);
}

/** Decimal hours ("7.50") as "7h 30m", for timesheets. */
export function formatDuration(hours: string | number | null | undefined): string {
  if (hours === null || hours === undefined || hours === "") return "—";
  const asNumber = typeof hours === "number" ? hours : Number(hours);
  if (Number.isNaN(asNumber)) return "—";
  const whole = Math.floor(Math.abs(asNumber));
  const minutes = Math.round((Math.abs(asNumber) - whole) * 60);
  const sign = asNumber < 0 ? "-" : "";
  if (minutes === 0) return `${sign}${whole}h`;
  return `${sign}${whole}h ${minutes}m`;
}
