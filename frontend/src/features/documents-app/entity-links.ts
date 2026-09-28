import { PERMISSIONS, type Permission } from "@/lib/authz/permissions";
import { formatDate } from "@/lib/datetime";
import type { LinkedEntityType } from "@/types/api/documents";

/**
 * What a document can be linked to, and how each kind is looked up and shown.
 *
 * The type list and labels are documents/models/link.py :: LinkedEntityType.
 * `resource` is the API path the backend resolves the id through
 * (documents/services/links.py :: _resolvers); `href` is the app route from
 * src/lib/navigation.ts. Label fields were checked against each module's
 * serializer dump — they are read loosely here (a record is a plain object)
 * so this module does not couple to other modules' type files.
 *
 * e-Invoice and e-Way Bill links can exist (the backend resolves them) but the
 * compliance module has no REST API, so they cannot be picked, fetched or
 * opened from the UI — they are shown as text.
 *
 * Links are navigable in ONE direction only: document → record.
 * BACKEND CONTRACT BLOCKER: there is no endpoint to list the documents linked
 * to a record (e.g. `GET documents/?entity_type=invoice&entity_id=<id>`, or
 * `GET documents/links/?entity_type&entity_id` returning the documents).
 * DocumentListView filters only by document_type/upload_status and the
 * search endpoint only by q/document_type/folder_id/tags, although the
 * DocumentLink (organization, entity_type, entity_id) index exists. Until it
 * does, no "Attachments" panel can be shown on an invoice, bill or expense
 * page, and features/documents-attachments/ is deliberately not built.
 */

export interface LinkableEntity {
  label: string;
  /** API resource to GET the record from; absent when no API exists. */
  resource?: string;
  /** Permission needed to read that resource. */
  permission?: Permission;
  href?: (id: string) => string;
  /** A short name for one record of this kind. */
  describe?: (record: Record<string, unknown>) => string;
}

function text(record: Record<string, unknown>, key: string): string {
  const value = record[key];
  return typeof value === "string" ? value.trim() : "";
}

export const LINKABLE_ENTITIES: Record<LinkedEntityType, LinkableEntity> = {
  invoice: {
    label: "Invoice",
    resource: "sales/invoices",
    permission: PERMISSIONS.VIEW_INVOICES,
    href: (id) => `/sales/invoices/${id}`,
    describe: (r) => text(r, "invoice_number") || `Draft invoice dated ${formatDate(text(r, "invoice_date"))}`,
  },
  sales_order: {
    label: "Sales order",
    resource: "sales/orders",
    permission: PERMISSIONS.VIEW_ORDERS,
    href: (id) => `/sales/orders/${id}`,
    describe: (r) => text(r, "order_number") || `Draft order dated ${formatDate(text(r, "order_date"))}`,
  },
  customer: {
    label: "Customer",
    resource: "sales/customers",
    permission: PERMISSIONS.VIEW_CUSTOMERS,
    href: (id) => `/sales/customers/${id}`,
    describe: (r) => text(r, "display_name") || "Customer",
  },
  bill: {
    label: "Bill",
    resource: "purchases/bills",
    permission: PERMISSIONS.VIEW_BILLS,
    href: (id) => `/purchases/bills/${id}`,
    describe: (r) =>
      text(r, "bill_number") || text(r, "vendor_bill_number") || `Draft bill dated ${formatDate(text(r, "bill_date"))}`,
  },
  purchase_order: {
    label: "Purchase order",
    resource: "purchases/orders",
    permission: PERMISSIONS.VIEW_PURCHASE_ORDERS,
    href: (id) => `/purchases/orders/${id}`,
    describe: (r) => text(r, "order_number") || `Draft order dated ${formatDate(text(r, "order_date"))}`,
  },
  vendor: {
    label: "Vendor",
    resource: "purchases/vendors",
    permission: PERMISSIONS.VIEW_VENDORS,
    href: (id) => `/purchases/vendors/${id}`,
    describe: (r) => text(r, "display_name") || "Vendor",
  },
  expense: {
    label: "Expense",
    resource: "purchases/expenses",
    permission: PERMISSIONS.VIEW_EXPENSES,
    href: (id) => `/purchases/expenses/${id}`,
    describe: (r) =>
      [text(r, "expense_number") || "Draft expense", text(r, "description")].filter(Boolean).join(" · "),
  },
  bank_transaction: {
    label: "Bank transaction",
    resource: "bank-transactions",
    permission: PERMISSIONS.VIEW_BANK_TRANSACTIONS,
    href: (id) => `/banking/transactions/${id}`,
    describe: (r) =>
      [formatDate(text(r, "transaction_date")), text(r, "description") || text(r, "counterparty_name")]
        .filter((part) => part && part !== "—")
        .join(" · ") || "Bank transaction",
  },
  project: {
    label: "Project",
    resource: "projects",
    permission: PERMISSIONS.VIEW_PROJECTS,
    href: (id) => `/projects/${id}`,
    describe: (r) => [text(r, "name"), text(r, "project_code")].filter(Boolean).join(" · ") || "Project",
  },
  journal_entry: {
    label: "Journal entry",
    resource: "accounting/journals",
    permission: PERMISSIONS.VIEW_ACCOUNTING,
    href: (id) => `/accounting/journals/${id}`,
    describe: (r) => text(r, "journal_number") || `Draft journal dated ${formatDate(text(r, "posting_date"))}`,
  },
  einvoice: { label: "e-Invoice" },
  ewaybill: { label: "e-Way Bill" },
};

/** Entity types a user can pick in the "Link a record" dialog. */
export function pickableEntityTypes(): LinkedEntityType[] {
  return (Object.keys(LINKABLE_ENTITIES) as LinkedEntityType[]).filter((type) => LINKABLE_ENTITIES[type].resource);
}

export function entityLabel(type: string): string {
  return LINKABLE_ENTITIES[type as LinkedEntityType]?.label ?? type;
}
