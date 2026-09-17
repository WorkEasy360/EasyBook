/**
 * Month names for `fiscal_year_start_month` (1–12). Fixed English names rather
 * than Intl, so the server render and the browser always agree.
 */
export const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
] as const;

export function monthName(month: number): string {
  return (Number.isInteger(month) ? MONTHS[month - 1] : undefined) ?? "—";
}
