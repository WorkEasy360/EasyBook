import { PERMISSIONS, type Permission, type Role, roleHasAny } from "@/lib/authz/permissions";

/**
 * The application's navigation model (spec §11).
 *
 * Every entry declares the permission(s) that make it reachable, taken from
 * the generated mirror of backend/authz/roles.py — and specifically the
 * permission the backend's LIST view checks for GET (its required_permission),
 * not the one needed to change things. A read-only role must still see the
 * page it can read. A section disappears when
 * the role grants none of its children — so a Viewer is never shown a
 * "Record Payment" route that would 403 on arrival.
 *
 * Hiding is a usability decision, not a security one (spec §62). Django
 * authorizes every request regardless of what this file says.
 *
 * Entries marked `blocked` have no backend endpoint yet. They are listed here
 * so the gap is visible in one place instead of being rediscovered per
 * feature, and they are NOT rendered.
 */

export interface NavItem {
  label: string;
  href: string;
  /** Visible when the role has ANY of these. */
  permissions: Permission[];
  /**
   * Set when no API exists to back the route. Never rendered; recorded so the
   * contract gap is auditable rather than silently dropped.
   */
  blocked?: string;
}

export interface NavSection {
  label: string;
  /** Sections with a single destination render as a plain link. */
  href?: string;
  items?: NavItem[];
  icon: NavIcon;
}

export type NavIcon =
  | "dashboard"
  | "items"
  | "sales"
  | "purchases"
  | "time"
  | "banking"
  | "accounting"
  | "tax"
  | "reports"
  | "documents"
  | "automation"
  | "ai"
  | "settings";

export const NAVIGATION: NavSection[] = [
  {
    label: "Dashboard",
    href: "/dashboard",
    icon: "dashboard",
  },
  {
    label: "Items",
    icon: "items",
    items: [
      { label: "Items", href: "/items", permissions: [PERMISSIONS.VIEW_ITEMS] },
      { label: "Inventory", href: "/inventory", permissions: [PERMISSIONS.VIEW_INVENTORY] },
      { label: "Warehouses", href: "/inventory/warehouses", permissions: [PERMISSIONS.VIEW_INVENTORY] },
      {
        label: "Stock Adjustments",
        href: "/inventory/adjustments",
        permissions: [PERMISSIONS.VIEW_INVENTORY],
      },
    ],
  },
  {
    label: "Sales",
    icon: "sales",
    items: [
      { label: "Customers", href: "/sales/customers", permissions: [PERMISSIONS.VIEW_CUSTOMERS] },
      { label: "Quotes", href: "/sales/quotes", permissions: [PERMISSIONS.VIEW_QUOTES] },
      { label: "Sales Orders", href: "/sales/orders", permissions: [PERMISSIONS.VIEW_ORDERS] },
      {
        label: "Delivery Challans",
        href: "/sales/deliveries",
        permissions: [PERMISSIONS.VIEW_DELIVERIES],
      },
      { label: "Invoices", href: "/sales/invoices", permissions: [PERMISSIONS.VIEW_INVOICES] },
      {
        label: "Recurring Invoices",
        href: "/sales/recurring-invoices",
        permissions: [PERMISSIONS.VIEW_RECURRING_INVOICES],
      },
      {
        label: "Payments Received",
        href: "/sales/payments",
        permissions: [PERMISSIONS.VIEW_PAYMENTS],
      },
      {
        label: "Credit Notes",
        href: "/sales/credit-notes",
        permissions: [PERMISSIONS.VIEW_CREDIT_NOTES],
      },
    ],
  },
  {
    label: "Purchases",
    icon: "purchases",
    items: [
      { label: "Vendors", href: "/purchases/vendors", permissions: [PERMISSIONS.VIEW_VENDORS] },
      {
        label: "Purchase Orders",
        href: "/purchases/orders",
        permissions: [PERMISSIONS.VIEW_PURCHASE_ORDERS],
      },
      {
        label: "Goods Receipts",
        href: "/purchases/goods-receipts",
        permissions: [PERMISSIONS.VIEW_GOODS_RECEIPTS],
      },
      { label: "Bills", href: "/purchases/bills", permissions: [PERMISSIONS.VIEW_BILLS] },
      {
        label: "Recurring Bills",
        href: "/purchases/recurring-bills",
        permissions: [PERMISSIONS.VIEW_RECURRING_BILLS],
      },
      { label: "Expenses", href: "/purchases/expenses", permissions: [PERMISSIONS.VIEW_EXPENSES] },
      {
        label: "Recurring Expenses",
        href: "/purchases/recurring-expenses",
        permissions: [PERMISSIONS.VIEW_RECURRING_BILLS],
      },
      {
        label: "Payments Made",
        href: "/purchases/payments",
        permissions: [PERMISSIONS.VIEW_VENDOR_PAYMENTS],
      },
      {
        label: "Vendor Credits",
        href: "/purchases/vendor-credits",
        permissions: [PERMISSIONS.VIEW_VENDOR_CREDITS],
      },
    ],
  },
  {
    label: "Time Tracking",
    icon: "time",
    items: [
      { label: "Projects", href: "/projects", permissions: [PERMISSIONS.VIEW_PROJECTS] },
      { label: "Timesheets", href: "/projects/timesheets", permissions: [PERMISSIONS.LOG_TIME] },
    ],
  },
  {
    label: "Banking",
    icon: "banking",
    items: [
      { label: "Bank Accounts", href: "/banking/accounts", permissions: [PERMISSIONS.VIEW_BANK_ACCOUNTS] },
      {
        label: "Transactions",
        href: "/banking/transactions",
        permissions: [PERMISSIONS.VIEW_BANK_TRANSACTIONS],
      },
      {
        label: "Transfers",
        href: "/banking/transfers",
        permissions: [PERMISSIONS.VIEW_BANK_TRANSACTIONS],
      },
      {
        label: "Reconciliation",
        href: "/banking/reconciliation",
        permissions: [PERMISSIONS.VIEW_BANK_TRANSACTIONS],
      },
      { label: "Bank Rules", href: "/banking/rules", permissions: [PERMISSIONS.VIEW_BANK_TRANSACTIONS] },
    ],
  },
  {
    label: "Accountant",
    icon: "accounting",
    items: [
      {
        label: "Chart of Accounts",
        href: "/accounting/accounts",
        permissions: [PERMISSIONS.VIEW_ACCOUNTING],
      },
      { label: "Journals", href: "/accounting/journals", permissions: [PERMISSIONS.VIEW_TRANSACTIONS] },
      {
        label: "General Ledger",
        href: "/accounting/general-ledger",
        permissions: [PERMISSIONS.VIEW_TRANSACTIONS],
      },
      {
        label: "Trial Balance",
        href: "/accounting/trial-balance",
        permissions: [PERMISSIONS.VIEW_REPORTS],
      },
      {
        label: "Audit Trail",
        href: "/accounting/audit",
        permissions: [PERMISSIONS.MANAGE_ORGANIZATION],
        // BACKEND CONTRACT BLOCKER: the `audit` app has models and services
        // but no api/ package and no entry in config/urls.py, so there is no
        // endpoint to list audit records (spec §88).
        blocked: "No audit REST API — backend/audit has no api/urls.py.",
      },
    ],
  },
  {
    label: "GST & Tax",
    icon: "tax",
    items: [
      { label: "GST Summary", href: "/tax/summary", permissions: [PERMISSIONS.VIEW_RETURNS] },
      { label: "GSTR-1", href: "/tax/gstr-1", permissions: [PERMISSIONS.VIEW_RETURNS] },
      { label: "GSTR-3B", href: "/tax/gstr-3b", permissions: [PERMISSIONS.VIEW_RETURNS] },
      { label: "Tax Registers", href: "/tax/registers", permissions: [PERMISSIONS.VIEW_RETURNS] },
      {
        label: "e-Invoices",
        href: "/tax/e-invoices",
        permissions: [PERMISSIONS.GENERATE_EINVOICE],
        // BACKEND CONTRACT BLOCKER: compliance/api/ contains only __init__.py
        // and is not routed in config/urls.py.
        blocked: "No compliance REST API — compliance/api has no views or urls.",
      },
      {
        label: "e-Way Bills",
        href: "/tax/e-way-bills",
        permissions: [PERMISSIONS.GENERATE_EWAYBILL],
        blocked: "No compliance REST API — compliance/api has no views or urls.",
      },
      {
        label: "Tax Rates",
        href: "/tax/rates",
        permissions: [PERMISSIONS.VIEW_TAX_RATES],
        // BACKEND CONTRACT BLOCKER: tax/api/ contains only __init__.py.
        blocked: "No tax REST API — tax/api has no views or urls.",
      },
    ],
  },
  {
    label: "Reports",
    href: "/reports",
    icon: "reports",
  },
  {
    label: "Documents",
    href: "/documents",
    icon: "documents",
  },
  {
    label: "Automation",
    href: "/automation",
    icon: "automation",
  },
  {
    label: "Ask Books",
    href: "/ai",
    icon: "ai",
  },
  {
    label: "Settings",
    href: "/settings",
    icon: "settings",
  },
];

/** Permissions that make a single-destination section reachable. */
const SECTION_PERMISSIONS: Record<string, Permission[]> = {
  "/dashboard": [PERMISSIONS.VIEW_REPORTS],
  "/reports": [PERMISSIONS.VIEW_REPORTS],
  "/documents": [PERMISSIONS.VIEW_DOCUMENTS],
  "/automation": [PERMISSIONS.VIEW_AUTOMATION],
  "/ai": [PERMISSIONS.USE_AI_ASSISTANT],
  // Every role can reach Settings; what is INSIDE it is gated per section.
  "/settings": [],
};

export interface ResolvedSection {
  label: string;
  icon: NavIcon;
  href?: string;
  items: NavItem[];
}

/**
 * Filters the navigation down to what this role can reach, dropping blocked
 * routes and any section left with nothing in it.
 */
export function navigationForRole(role: Role | null | undefined): ResolvedSection[] {
  const sections: ResolvedSection[] = [];

  for (const section of NAVIGATION) {
    if (section.items) {
      const items = section.items.filter(
        (item) => !item.blocked && roleHasAny(role, item.permissions),
      );
      if (items.length > 0) {
        sections.push({ label: section.label, icon: section.icon, items });
      }
      continue;
    }

    if (!section.href) continue;
    const required = SECTION_PERMISSIONS[section.href] ?? [];
    if (required.length === 0 || roleHasAny(role, required)) {
      sections.push({ label: section.label, icon: section.icon, href: section.href, items: [] });
    }
  }

  return sections;
}

/** Every route hidden because its API does not exist yet. */
export function blockedRoutes(): Array<{ href: string; label: string; reason: string }> {
  return NAVIGATION.flatMap((section) =>
    (section.items ?? [])
      .filter((item): item is NavItem & { blocked: string } => Boolean(item.blocked))
      .map((item) => ({ href: item.href, label: item.label, reason: item.blocked })),
  );
}

/**
 * Longest-prefix match for highlighting the active entry. Prefix rather than
 * equality so a detail page keeps its list's entry lit.
 */
export function isActiveHref(pathname: string, href: string): boolean {
  if (pathname === href) return true;
  return pathname.startsWith(`${href}/`);
}
