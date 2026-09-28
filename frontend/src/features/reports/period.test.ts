import { describe, expect, it } from "vitest";
import {
  asOfPresetLinks,
  asOfPresets,
  hrefWith,
  isReversedRange,
  isUuid,
  monthPresets,
  pagedHrefBuilder,
  rangePresetLinks,
  rangePresets,
  uuidParamOf,
} from "./period";

describe("rangePresets", () => {
  it("uses the fiscal year, not the calendar year, for an April start", () => {
    const presets = rangePresets("2026-09-17", 4);
    expect(presets.map((preset) => preset.label)).toEqual([
      "This month",
      "This quarter",
      "This fiscal year (FY 2026–27)",
      "Previous fiscal year (FY 2025–26)",
    ]);
    expect(presets[0]).toMatchObject({ from_date: "2026-09-01", to_date: "2026-09-30" });
    expect(presets[1]).toMatchObject({ from_date: "2026-07-01", to_date: "2026-09-30" });
    expect(presets[2]).toMatchObject({ from_date: "2026-04-01", to_date: "2027-03-31" });
    expect(presets[3]).toMatchObject({ from_date: "2025-04-01", to_date: "2026-03-31" });
  });

  it("puts February in the fiscal year that began the previous April", () => {
    const presets = rangePresets("2026-02-10", 4);
    expect(presets[2]).toMatchObject({ from_date: "2025-04-01", to_date: "2026-03-31" });
    expect(presets[3]).toMatchObject({ from_date: "2024-04-01", to_date: "2025-03-31" });
  });
});

describe("monthPresets", () => {
  it("walks back across a year boundary", () => {
    const presets = monthPresets("2026-01-15", 3);
    expect(presets.map((preset) => [preset.label, preset.from_date, preset.to_date])).toEqual([
      ["This month (January 2026)", "2026-01-01", "2026-01-31"],
      ["December 2025", "2025-12-01", "2025-12-31"],
      ["November 2025", "2025-11-01", "2025-11-30"],
    ]);
  });

  it("handles a leap-year February", () => {
    const [, previous] = monthPresets("2028-03-05", 2);
    expect(previous).toMatchObject({ from_date: "2028-02-01", to_date: "2028-02-29" });
  });
});

describe("asOfPresets", () => {
  it("gives month, quarter and fiscal year ends before today", () => {
    expect(asOfPresets("2026-09-17", 4)).toEqual([
      { label: "Today", date: "2026-09-17" },
      { label: "End of last month", date: "2026-08-31" },
      { label: "End of last quarter", date: "2026-06-30" },
      { label: "End of last fiscal year", date: "2026-03-31" },
    ]);
  });
});

describe("preset links", () => {
  it("marks the active range and preserves other filters", () => {
    const links = rangePresetLinks(
      "/reports/journals",
      [
        { label: "A", from_date: "2026-09-01", to_date: "2026-09-30" },
        { label: "B", from_date: "2026-04-01", to_date: "2027-03-31" },
      ],
      { from_date: "2026-09-01", to_date: "2026-09-30" },
      { status: "posted", account: undefined },
    );
    expect(links[0]).toEqual({
      label: "A",
      href: "/reports/journals?status=posted&from_date=2026-09-01&to_date=2026-09-30",
      active: true,
    });
    expect(links[1]?.active).toBe(false);
  });

  it("builds as-of links on the given key", () => {
    const [link] = asOfPresetLinks("/reports/balance-sheet", [{ label: "Today", date: "2026-09-17" }], "2026-09-17");
    expect(link).toEqual({ label: "Today", href: "/reports/balance-sheet?as_of_date=2026-09-17", active: true });
  });
});

describe("hrefWith / pagedHrefBuilder", () => {
  it("drops empty values", () => {
    expect(hrefWith("/x", { a: "1", b: undefined, c: "" })).toBe("/x?a=1");
    expect(hrefWith("/x", {})).toBe("/x");
  });

  it("keeps filters on page links and omits defaults", () => {
    const build = pagedHrefBuilder("/reports/journals", { from_date: "2026-04-01", status: undefined }, 25);
    expect(build(1)).toBe("/reports/journals?from_date=2026-04-01");
    expect(build(3)).toBe("/reports/journals?from_date=2026-04-01&page=3");
    expect(pagedHrefBuilder("/r", {}, 50)(2)).toBe("/r?page=2&page_size=50");
  });
});

describe("uuid guards", () => {
  it("only forwards well-formed UUIDs", () => {
    expect(isUuid("6c20df3b-7ac3-41fa-a636-e399a40263a9")).toBe(true);
    expect(isUuid("notauuid")).toBe(false);
    expect(uuidParamOf({ item: "notauuid" }, "item")).toBeUndefined();
    expect(uuidParamOf({ item: ["6c20df3b-7ac3-41fa-a636-e399a40263a9"] }, "item")).toBe(
      "6c20df3b-7ac3-41fa-a636-e399a40263a9",
    );
  });
});

describe("isReversedRange", () => {
  it("flags a start after the end", () => {
    expect(isReversedRange({ from_date: "2026-10-01", to_date: "2026-09-01" })).toBe(true);
    expect(isReversedRange({ from_date: "2026-09-01", to_date: "2026-09-01" })).toBe(false);
    expect(isReversedRange({ from_date: null, to_date: "2026-09-01" })).toBe(false);
  });
});
