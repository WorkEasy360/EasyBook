import { PERMISSIONS, roleHasAll, type Permission, type Role } from "@/lib/authz/permissions";

/**
 * The report centre's catalogue.
 *
 * Only reports that exist are listed, each gated on the permission its API
 * view actually requires (`required_permission` in
 * backend/reports/api/views.py — read per view, not assumed): the AR reports
 * need VIEW_INVOICES, customer balances VIEW_CUSTOMERS, project profitability
 * VIEW_ALL_TIMESHEETS (margin exposes labour cost), the journal report
 * VIEW_TRANSACTIONS. Entries that live on another module's page (general
 * ledger, trial balance, stock summary) also carry that page's navigation
 * permission, so the link never lands on a forbidden screen.
 *
 * Hiding is a usability decision; Django authorizes every request regardless.
 */

export interface ReportEntry {
  title: string;
  href: string;
  /** One line: the question this report answers. */
  description: string;
  /** Every one of these is required. */
  permissions: Permission[];
}

export interface ReportGroup {
  key: string;
  label: string;
  entries: ReportEntry[];
}

export const REPORT_GROUPS: ReportGroup[] = [
  {
    key: "business",
    label: "Business overview",
    entries: [
      {
        title: "Profit and loss",
        href: "/reports/profit-loss",
        description: "Did the business make money over a period? Income, costs and net profit, with an optional comparison.",
        permissions: [PERMISSIONS.VIEW_REPORTS],
      },
      {
        title: "Balance sheet",
        href: "/reports/balance-sheet",
        description: "What does the business own and owe on a given date?",
        permissions: [PERMISSIONS.VIEW_REPORTS],
      },
      {
        title: "Cash flow statement",
        href: "/reports/cash-flow",
        description: "Where did cash come from and go over a period? Needs a linked bank account.",
        permissions: [PERMISSIONS.VIEW_REPORTS],
      },
    ],
  },
  {
    key: "receivables",
    label: "Receivables",
    entries: [
      {
        title: "Receivables ageing",
        href: "/reports/receivables/ageing",
        description: "How long has money owed by customers been outstanding?",
        permissions: [PERMISSIONS.VIEW_INVOICES],
      },
      {
        title: "Customer balances",
        href: "/reports/receivables/customer-balances",
        description: "How much does each customer owe right now?",
        permissions: [PERMISSIONS.VIEW_CUSTOMERS],
      },
      {
        title: "Outstanding invoices",
        href: "/reports/receivables/outstanding-invoices",
        description: "Which posted invoices still have a balance due?",
        permissions: [PERMISSIONS.VIEW_INVOICES],
      },
      {
        title: "Overdue invoices",
        href: "/reports/receivables/overdue-invoices",
        description: "Which invoices are past their due date and still unpaid?",
        permissions: [PERMISSIONS.VIEW_INVOICES],
      },
    ],
  },
  {
    key: "payables",
    label: "Payables",
    entries: [
      {
        title: "Payables ageing",
        href: "/reports/payables/ageing",
        description: "How long have bills owed to vendors been outstanding?",
        permissions: [PERMISSIONS.VIEW_BILLS],
      },
      {
        title: "Vendor balances",
        href: "/reports/payables/vendor-balances",
        description: "How much does the business owe each vendor right now?",
        permissions: [PERMISSIONS.VIEW_VENDORS],
      },
      {
        title: "Outstanding bills",
        href: "/reports/payables/outstanding-bills",
        description: "Which posted bills still have a balance to pay?",
        permissions: [PERMISSIONS.VIEW_BILLS],
      },
      {
        title: "Overdue bills",
        href: "/reports/payables/overdue-bills",
        description: "Which bills are past their due date and still unpaid?",
        permissions: [PERMISSIONS.VIEW_BILLS],
      },
    ],
  },
  {
    key: "sales",
    label: "Sales",
    entries: [
      {
        title: "Sales by customer",
        href: "/reports/sales/by-customer",
        description: "Who bought the most over a period, before and after tax?",
        permissions: [PERMISSIONS.VIEW_INVOICES],
      },
      {
        title: "Sales by item",
        href: "/reports/sales/by-item",
        description: "Which items and services sold, in what quantity and value?",
        permissions: [PERMISSIONS.VIEW_INVOICES],
      },
    ],
  },
  {
    key: "purchases",
    label: "Purchases & expenses",
    entries: [
      {
        title: "Purchases by vendor",
        href: "/reports/purchases/by-vendor",
        description: "Which vendors did the business buy the most from over a period?",
        permissions: [PERMISSIONS.VIEW_BILLS],
      },
      {
        title: "Purchases by item",
        href: "/reports/purchases/by-item",
        description: "Which items were bought, in what quantity and value?",
        permissions: [PERMISSIONS.VIEW_BILLS],
      },
      {
        title: "Expenses by category",
        href: "/reports/expenses/by-category",
        description: "Which expense accounts did recorded expenses go to over a period?",
        permissions: [PERMISSIONS.VIEW_EXPENSES],
      },
    ],
  },
  {
    key: "inventory",
    label: "Inventory",
    entries: [
      {
        title: "Stock summary",
        href: "/inventory",
        description: "What is on hand in each warehouse, and what is it worth?",
        permissions: [PERMISSIONS.VIEW_INVENTORY],
      },
      {
        title: "Inventory valuation",
        href: "/reports/inventory/valuation",
        description: "What was stock worth at moving average cost on a given date?",
        permissions: [PERMISSIONS.VIEW_INVENTORY],
      },
      {
        title: "Stock movements",
        href: "/reports/inventory/movements",
        description: "What came in and went out, for an item or warehouse over a period?",
        permissions: [PERMISSIONS.VIEW_INVENTORY],
      },
      {
        title: "Stock adjustments",
        href: "/reports/inventory/adjustments",
        description: "Which stock adjustments were made over a period, and why?",
        permissions: [PERMISSIONS.VIEW_INVENTORY],
      },
      {
        title: "Low stock",
        href: "/reports/inventory/low-stock",
        description: "Which items are at or below their reorder level?",
        permissions: [PERMISSIONS.VIEW_INVENTORY],
      },
    ],
  },
  {
    key: "tax",
    label: "Tax",
    entries: [
      {
        title: "GST summary",
        href: "/tax/summary",
        description: "What are the output and input tax figures for a period, in one view?",
        permissions: [PERMISSIONS.VIEW_RETURNS],
      },
      {
        title: "GSTR-1 summary",
        href: "/tax/gstr-1",
        description: "What outward supplies make up the period's GSTR-1 figures?",
        permissions: [PERMISSIONS.VIEW_RETURNS],
      },
      {
        title: "GSTR-3B summary",
        href: "/tax/gstr-3b",
        description: "What are the period's outward liability and input tax credit figures?",
        permissions: [PERMISSIONS.VIEW_RETURNS],
      },
      {
        title: "Tax registers",
        href: "/tax/registers",
        description: "Which documents carry output and input tax in a period, one by one?",
        permissions: [PERMISSIONS.VIEW_RETURNS],
      },
    ],
  },
  {
    key: "projects",
    label: "Projects",
    entries: [
      {
        title: "Project profitability",
        href: "/reports/projects/profitability",
        description: "Which projects earn more than their labour and expense cost?",
        permissions: [PERMISSIONS.VIEW_ALL_TIMESHEETS],
      },
    ],
  },
  {
    key: "accountant",
    label: "Accountant",
    entries: [
      {
        title: "General ledger",
        href: "/accounting/general-ledger",
        description: "Every posted line on an account, with its running balance.",
        permissions: [PERMISSIONS.VIEW_ACCOUNTING, PERMISSIONS.VIEW_TRANSACTIONS],
      },
      {
        title: "Trial balance",
        href: "/accounting/trial-balance",
        description: "Do total debits equal total credits, account by account?",
        permissions: [PERMISSIONS.VIEW_ACCOUNTING, PERMISSIONS.VIEW_REPORTS],
      },
      {
        title: "Journal report",
        href: "/reports/journals",
        description: "Which journals were posted over a period, by account, source or number?",
        permissions: [PERMISSIONS.VIEW_TRANSACTIONS],
      },
    ],
  },
];

/** The catalogue as this role can use it — empty groups are dropped. */
export function reportGroupsForRole(role: Role | string | null | undefined): ReportGroup[] {
  return REPORT_GROUPS.map((group) => ({
    ...group,
    entries: group.entries.filter((entry) => roleHasAll(role, entry.permissions)),
  })).filter((group) => group.entries.length > 0);
}

/** The catalogue entry for a route, so a report page's title matches its link. */
export function reportEntry(href: string): ReportEntry {
  for (const group of REPORT_GROUPS) {
    const entry = group.entries.find((candidate) => candidate.href === href);
    if (entry) return entry;
  }
  throw new Error(`No report catalogue entry for ${href}`);
}
