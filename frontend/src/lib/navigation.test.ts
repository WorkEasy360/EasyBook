import { describe, expect, it } from "vitest";
import { blockedRoutes, isActiveHref, navigationForRole } from "./navigation";
import { PERMISSIONS, ROLES, roleHasPermission } from "./authz/permissions";

function labels(role: Parameters<typeof navigationForRole>[0]): string[] {
  return navigationForRole(role).flatMap((section) =>
    section.items.length > 0 ? section.items.map((item) => item.label) : [section.label],
  );
}

describe("permission mirror", () => {
  it("matches the backend's segregation-of-duties line for Staff", () => {
    /*
     * backend/authz/roles.py states this asymmetry explicitly and its own
     * test asserts it: Staff may record money ARRIVING but not money LEAVING.
     * Mirroring it wrongly would show a button that always 403s.
     */
    expect(roleHasPermission("staff", PERMISSIONS.RECORD_PAYMENT)).toBe(true);
    expect(roleHasPermission("staff", PERMISSIONS.RECORD_VENDOR_PAYMENT)).toBe(false);
  });

  it("keeps filing acts on the Accountant side", () => {
    expect(roleHasPermission("staff", PERMISSIONS.GENERATE_EWAYBILL)).toBe(true);
    expect(roleHasPermission("staff", PERMISSIONS.GENERATE_EINVOICE)).toBe(false);
    expect(roleHasPermission("staff", PERMISSIONS.CANCEL_EWAYBILL)).toBe(false);
    expect(roleHasPermission("accountant", PERMISSIONS.GENERATE_EINVOICE)).toBe(true);
  });

  it("denies a Viewer every write", () => {
    for (const permission of [
      PERMISSIONS.POST_INVOICE,
      PERMISSIONS.POST_BILL,
      PERMISSIONS.RECORD_PAYMENT,
      PERMISSIONS.ADJUST_INVENTORY,
      PERMISSIONS.MANAGE_ACCOUNTING,
      PERMISSIONS.RECONCILE_BANK,
    ]) {
      expect(roleHasPermission("viewer", permission)).toBe(false);
    }
  });

  it("reserves organization management for the Owner", () => {
    expect(roleHasPermission("owner", PERMISSIONS.MANAGE_ORGANIZATION)).toBe(true);
    expect(roleHasPermission("admin", PERMISSIONS.MANAGE_ORGANIZATION)).toBe(false);
  });

  it("grants nothing for an unknown or absent role", () => {
    expect(roleHasPermission(null, PERMISSIONS.VIEW_INVOICES)).toBe(false);
    expect(roleHasPermission("superuser", PERMISSIONS.VIEW_INVOICES)).toBe(false);
  });
});

describe("navigation filtering", () => {
  it("gives the Owner the full menu", () => {
    const owner = labels("owner");
    expect(owner).toContain("Invoices");
    expect(owner).toContain("Chart of Accounts");
    expect(owner).toContain("Reconciliation");
    expect(owner).toContain("Ask Books");
  });

  it("shows a list to exactly the roles the backend lets READ it", () => {
    // Nav gating follows each list view's GET permission (e.g.
    // BankReconciliationListCreateView reads with VIEW_BANK_TRANSACTIONS),
    // not the permission to change things: a read-only role may open the
    // page, and its write actions are gated inside it.
    for (const role of ROLES) {
      const visible = labels(role);
      expect(visible.includes("Reconciliation")).toBe(roleHasPermission(role, PERMISSIONS.VIEW_BANK_TRANSACTIONS));
      expect(visible.includes("Journals")).toBe(roleHasPermission(role, PERMISSIONS.VIEW_TRANSACTIONS));
      expect(visible.includes("Trial Balance")).toBe(roleHasPermission(role, PERMISSIONS.VIEW_REPORTS));
    }
  });

  it("gives a Viewer the read destinations it can load", () => {
    const viewer = labels("viewer");
    expect(viewer).toContain("Invoices");
    expect(viewer).toContain("Reports");
  });

  it("renders nothing for a session with no role", () => {
    // Fail closed: an unresolved role must not be treated as permissive.
    const sections = navigationForRole(null);
    const reachable = sections.flatMap((section) => section.items.map((item) => item.href));
    expect(reachable).toHaveLength(0);
  });

  it("drops a section entirely when the role can reach none of its items", () => {
    for (const section of navigationForRole("viewer")) {
      if (section.items.length === 0) continue;
      expect(section.items.length).toBeGreaterThan(0);
    }
  });

  it("never renders a route whose API does not exist", () => {
    // Spec §105 / §108: no screens disconnected from a real backend.
    const blocked = new Set(blockedRoutes().map((route) => route.href));
    expect(blocked.size).toBeGreaterThan(0);

    for (const role of ROLES) {
      for (const section of navigationForRole(role)) {
        for (const item of section.items) {
          expect(blocked.has(item.href)).toBe(false);
        }
      }
    }
  });

  it("records every blocked route with a reason", () => {
    for (const route of blockedRoutes()) {
      expect(route.reason).toMatch(/No (audit|compliance|tax) REST API/);
    }
  });
});

describe("isActiveHref", () => {
  it("matches the exact route", () => {
    expect(isActiveHref("/sales/invoices", "/sales/invoices")).toBe(true);
  });

  it("keeps the list entry lit on a detail page", () => {
    expect(isActiveHref("/sales/invoices/abc-123", "/sales/invoices")).toBe(true);
  });

  it("does not match a sibling with a shared prefix", () => {
    // "/sales/invoices-archive" must not light up "/sales/invoices".
    expect(isActiveHref("/sales/invoices-archive", "/sales/invoices")).toBe(false);
  });
});
