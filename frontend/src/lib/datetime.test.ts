import { describe, expect, it } from "vitest";
import {
  addDays,
  daysBetween,
  formatDate,
  formatDateNumeric,
  formatDateTime,
  formatDuration,
  formatInstantAsDate,
  parseDateString,
  todayInZone,
  toDateInputValue,
} from "./datetime";

describe("calendar dates", () => {
  it("renders the date exactly as stored", () => {
    expect(formatDate("2026-03-31")).toBe("31 Mar 2026");
    expect(formatDateNumeric("2026-03-31")).toBe("31/03/2026");
  });

  it("never shifts a calendar date across a timezone", () => {
    /*
     * The bug this guards: `new Date("2026-03-31")` parses as UTC midnight,
     * so any renderer that goes through a Date and a western timezone shows
     * the 30th — moving an invoice out of the fiscal year it belongs to.
     * formatDate must be invariant to the host timezone.
     */
    const original = process.env.TZ;
    try {
      for (const zone of ["UTC", "America/Los_Angeles", "Pacific/Kiritimati", "Asia/Kolkata"]) {
        process.env.TZ = zone;
        expect(formatDate("2026-03-31")).toBe("31 Mar 2026");
        expect(formatDate("2026-04-01")).toBe("01 Apr 2026");
      }
    } finally {
      process.env.TZ = original;
    }
  });

  it("round-trips through a date input value", () => {
    expect(toDateInputValue("2026-03-31")).toBe("2026-03-31");
    expect(toDateInputValue(null)).toBe("");
  });

  it("shows an em dash for a missing or malformed date", () => {
    expect(formatDate(null)).toBe("—");
    expect(formatDate(undefined)).toBe("—");
    expect(formatDate("")).toBe("—");
    expect(formatDate("not-a-date")).toBe("—");
  });

  it("parses the parts without a Date", () => {
    expect(parseDateString("2026-03-31")).toEqual({ year: 2026, month: 3, day: 31 });
    expect(parseDateString("garbage")).toBeNull();
  });

  it("tolerates a full timestamp where a date was expected", () => {
    expect(formatDate("2026-03-31T18:30:00Z")).toBe("31 Mar 2026");
  });
});

describe("instants", () => {
  it("renders in the organization timezone, not the host's", () => {
    // 18:30 UTC is 00:00 the NEXT day in Asia/Kolkata (+05:30).
    const instant = "2026-03-31T18:30:00Z";
    expect(formatDateTime(instant, { timeZone: "Asia/Kolkata" })).toBe("01 Apr 2026, 00:00");
    expect(formatDateTime(instant, { timeZone: "UTC" })).toBe("31 Mar 2026, 18:30");
  });

  it("derives a date from an instant using the organization timezone", () => {
    expect(formatInstantAsDate("2026-03-31T18:30:00Z", { timeZone: "Asia/Kolkata" })).toBe(
      "01 Apr 2026",
    );
  });

  it("shows an em dash for a missing or invalid instant", () => {
    expect(formatDateTime(null, { timeZone: "UTC" })).toBe("—");
    expect(formatDateTime("nonsense", { timeZone: "UTC" })).toBe("—");
  });
});

describe("todayInZone", () => {
  it("returns a YYYY-MM-DD string", () => {
    expect(todayInZone("Asia/Kolkata")).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it("can differ between zones at the same instant", () => {
    // Not asserting a specific pair (it depends on the run time), only that
    // both are well-formed — the shape is what callers depend on.
    expect(todayInZone("Pacific/Kiritimati")).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(todayInZone("Pacific/Niue")).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});

describe("daysBetween", () => {
  it("counts days for an ageing bucket", () => {
    expect(daysBetween("2026-03-01", "2026-03-31")).toBe(30);
    expect(daysBetween("2026-03-31", "2026-03-01")).toBe(-30);
    expect(daysBetween("2026-03-31", "2026-03-31")).toBe(0);
  });

  it("crosses a month and a leap-year February correctly", () => {
    expect(daysBetween("2024-02-28", "2024-03-01")).toBe(2);
    expect(daysBetween("2026-02-28", "2026-03-01")).toBe(1);
  });

  it("returns null for an unparseable input", () => {
    expect(daysBetween("bad", "2026-03-01")).toBeNull();
  });
});

describe("durations", () => {
  it("renders decimal hours as hours and minutes", () => {
    expect(formatDuration("7.5")).toBe("7h 30m");
    expect(formatDuration("8")).toBe("8h");
    expect(formatDuration("0.25")).toBe("0h 15m");
  });

  it("handles a negative adjustment and a missing value", () => {
    expect(formatDuration("-1.5")).toBe("-1h 30m");
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration("")).toBe("—");
  });
});

describe("addDays", () => {
  it("crosses month and year boundaries on the calendar", () => {
    expect(addDays("2026-01-31", 30)).toBe("2026-03-02");
    expect(addDays("2026-12-15", 30)).toBe("2027-01-14");
  });

  it("handles leap years", () => {
    expect(addDays("2028-02-28", 1)).toBe("2028-02-29");
    expect(addDays("2027-02-28", 1)).toBe("2027-03-01");
  });

  it("returns null for a malformed date", () => {
    expect(addDays("31/01/2026", 1)).toBeNull();
  });
});
