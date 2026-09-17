import { describe, expect, it } from "vitest";
import { normalizeProfitability } from "./profitability";
import { pivotWeek, startOfWeek, sumHours, weekDates, weekdayIndex } from "./week";

describe("normalizeProfitability", () => {
  it("turns the pre-fix float payload into strings and keeps an undefined margin null", () => {
    // Captured live from GET /projects/{id}/profitability/ before the backend fix.
    const raw = {
      project_id: "bdd3f1ff-d69b-4c9c-92be-5ace9d19ab85",
      revenue: 0.0,
      unbilled_value: 5000.0,
      margin_percent: null,
      budget_amount: 150000.0,
      budget_hours: null,
      total_hours: 5.75,
    };
    const result = normalizeProfitability(raw);
    expect(result.revenue).toBe("0");
    expect(result.unbilled_value).toBe("5000");
    expect(result.total_hours).toBe("5.75");
    expect(result.margin_percent).toBeNull();
    expect(result.budget_amount).toBe("150000");
    expect(result.budget_hours).toBeNull();
    // Keys the old payload lacked fall back to zero rather than undefined.
    expect(result.pending_approval_hours).toBe("0");
  });

  it("passes the fixed string payload through untouched", () => {
    const result = normalizeProfitability({ revenue: "5850.00", margin_percent: "65.81", total_hours: "5.75" });
    expect(result.revenue).toBe("5850.00");
    expect(result.margin_percent).toBe("65.81");
  });
});

describe("week helpers", () => {
  it("finds Monday regardless of timezone", () => {
    expect(weekdayIndex("2026-09-14")).toBe(0); // Monday
    expect(weekdayIndex("2026-09-20")).toBe(6); // Sunday
    expect(startOfWeek("2026-09-17")).toBe("2026-09-14");
    expect(startOfWeek("2026-09-20")).toBe("2026-09-14");
    expect(startOfWeek("2027-01-01")).toBe("2026-12-28"); // across a year end
    expect(startOfWeek("nonsense")).toBeNull();
  });

  it("lists the seven days of a week", () => {
    expect(weekDates("2026-09-28")).toEqual([
      "2026-09-28",
      "2026-09-29",
      "2026-09-30",
      "2026-10-01",
      "2026-10-02",
      "2026-10-03",
      "2026-10-04",
    ]);
  });

  it("sums hours in decimal", () => {
    expect(sumHours(["0.10", "0.20"])).toBe("0.30");
    expect(sumHours([])).toBe("0.00");
  });

  it("pivots entries into one row per project and task", () => {
    const rows = pivotWeek(
      [
        { project: "p1", task: "t1", entry_date: "2026-09-14", hours: "2.50" },
        { project: "p1", task: "t1", entry_date: "2026-09-14", hours: "1.25" },
        { project: "p1", task: "t2", entry_date: "2026-09-16", hours: "3.00" },
        { project: "p1", task: "t1", entry_date: "2026-09-21", hours: "8.00" }, // next week
      ],
      "2026-09-14",
    );
    expect(rows).toHaveLength(2);
    expect(rows[0]).toEqual({
      key: "p1:t1",
      project: "p1",
      task: "t1",
      days: ["3.75", "", "", "", "", "", ""],
      total: "3.75",
    });
    expect(rows[1]?.days[2]).toBe("3.00");
  });
});
