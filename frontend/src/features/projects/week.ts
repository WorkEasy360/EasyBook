import { addDays, parseDateString } from "@/lib/datetime";
import { sum, toDecimalString } from "@/lib/money";
import type { DateString, DecimalString } from "@/lib/api/types";

/**
 * Calendar helpers for the weekly timesheet. Weeks run Monday to Sunday.
 *
 * Weekdays are computed in UTC from the date's own parts — a DateString has
 * no zone, and building it as UTC midnight means the weekday can never shift
 * with the browser's or server's timezone (the same reason lib/datetime.ts ::
 * addDays works in UTC).
 */

/** 0 = Monday … 6 = Sunday, or null for a malformed date. */
export function weekdayIndex(date: DateString): number | null {
  const parts = parseDateString(date);
  if (!parts) return null;
  const sundayFirst = new Date(Date.UTC(parts.year, parts.month - 1, parts.day)).getUTCDay();
  return (sundayFirst + 6) % 7;
}

/** The Monday on or before `date`. */
export function startOfWeek(date: DateString): DateString | null {
  const index = weekdayIndex(date);
  if (index === null) return null;
  return addDays(date, -index);
}

/** The seven dates of the week starting on `monday`. */
export function weekDates(monday: DateString): DateString[] {
  return Array.from({ length: 7 }, (_, offset) => addDays(monday, offset)).filter(
    (value): value is DateString => value !== null,
  );
}

/** Hours on a timesheet grid, summed in decimal (hours are 2dp strings). */
export function sumHours(values: readonly DecimalString[]): DecimalString {
  return toDecimalString(sum([...values]), 2);
}

export interface WeekCellEntry {
  project: string;
  task: string;
  entry_date: DateString;
  hours: DecimalString;
}

export interface WeekRow {
  key: string;
  project: string;
  task: string;
  /** Hours per weekday, Monday first; "" where nothing was logged. */
  days: DecimalString[];
  total: DecimalString;
}

/**
 * Pivots a timesheet's entries into one row per (project, task) with a column
 * per day. Entries outside the week are ignored.
 */
export function pivotWeek(entries: readonly WeekCellEntry[], monday: DateString): WeekRow[] {
  const dates = weekDates(monday);
  const rows = new Map<string, { project: string; task: string; cells: DecimalString[][] }>();

  for (const entry of entries) {
    const day = dates.indexOf(entry.entry_date);
    if (day === -1) continue;
    const key = `${entry.project}:${entry.task}`;
    let row = rows.get(key);
    if (!row) {
      row = { project: entry.project, task: entry.task, cells: dates.map(() => []) };
      rows.set(key, row);
    }
    row.cells[day]?.push(entry.hours);
  }

  return [...rows.entries()].map(([key, row]) => ({
    key,
    project: row.project,
    task: row.task,
    days: row.cells.map((cell) => (cell.length > 0 ? sumHours(cell) : "")),
    total: sumHours(row.cells.flat()),
  }));
}
