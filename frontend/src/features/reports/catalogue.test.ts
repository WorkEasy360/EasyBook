import { existsSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { ROLES } from "@/lib/authz/permissions";
import { REPORT_GROUPS, reportEntry, reportGroupsForRole } from "./catalogue";

const APP_DIR = path.resolve(__dirname, "../../app/(app)");

function allEntries() {
  return REPORT_GROUPS.flatMap((group) => group.entries);
}

describe("report catalogue", () => {
  it("links only to routes that exist in this app", () => {
    // Every entry is either one of this module's pages or an existing page
    // owned elsewhere — never a report the UI does not have.
    for (const entry of allEntries()) {
      const pageFile = path.join(APP_DIR, ...entry.href.split("/").filter(Boolean), "page.tsx");
      expect(existsSync(pageFile), `${entry.href} has no page.tsx`).toBe(true);
    }
  });

  it("has unique links and a permission on every entry", () => {
    const hrefs = allEntries().map((entry) => entry.href);
    expect(new Set(hrefs).size).toBe(hrefs.length);
    for (const entry of allEntries()) expect(entry.permissions.length).toBeGreaterThan(0);
  });

  it("shows the owner every report", () => {
    const visible = reportGroupsForRole("owner").flatMap((group) => group.entries);
    expect(visible).toHaveLength(allEntries().length);
  });

  it("hides project profitability from staff, whose role lacks VIEW_ALL_TIMESHEETS", () => {
    const hrefs = reportGroupsForRole("staff").flatMap((group) => group.entries.map((entry) => entry.href));
    expect(hrefs).not.toContain("/reports/projects/profitability");
    // The project group has nothing else, so the whole group disappears.
    expect(reportGroupsForRole("staff").map((group) => group.key)).not.toContain("projects");
  });

  it("shows nothing to an unknown role and never an empty group", () => {
    expect(reportGroupsForRole("nobody")).toEqual([]);
    expect(reportGroupsForRole(null)).toEqual([]);
    for (const role of ROLES) {
      for (const group of reportGroupsForRole(role)) expect(group.entries.length).toBeGreaterThan(0);
    }
  });

  it("looks entries up by route and fails loudly for an unknown one", () => {
    expect(reportEntry("/reports/balance-sheet").title).toBe("Balance sheet");
    expect(() => reportEntry("/reports/nope")).toThrow();
  });
});
