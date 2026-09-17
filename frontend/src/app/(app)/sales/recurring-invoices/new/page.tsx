import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ForbiddenState } from "@/components/ui/states";
import { RecurringInvoiceForm, type RecurringInvoiceFormInitial } from "@/features/sales/recurring-invoice-form";
import { serverApi, tryServer } from "@/lib/api/server";
import { paramOf, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import type { Warehouse } from "@/types/api/inventory";
import type { Invoice } from "@/types/api/sales";

export const metadata: Metadata = { title: "New recurring invoice" };

const CRUMBS = [{ label: "Recurring invoices", href: "/sales/recurring-invoices" }, { label: "New" }];

/** `?customer=<id>` preselects the customer (from a customer page). */
export default async function NewRecurringInvoicePage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.MANAGE_RECURRING_INVOICES)) {
    return (
      <>
        <PageHeader title="New recurring invoice" breadcrumbs={CRUMBS} />
        <ForbiddenState resource="creating recurring invoices" />
      </>
    );
  }

  // The accounts on the most recent invoice are the best default available:
  // the backend has no organization-level default receivable account.
  // Generated invoices with tracked products need a warehouse
  // (warehouse_required), so the organization's default backs up the
  // remembered one.
  const [latest, warehouses] = await Promise.all([
    roleHasPermission(role, PERMISSIONS.VIEW_INVOICES)
      ? tryServer(() => serverApi.list<Invoice>("sales/invoices", { query: { page_size: 1 } }))
      : Promise.resolve(null),
    roleHasPermission(role, PERMISSIONS.VIEW_INVENTORY)
      ? tryServer(() => serverApi.list<Warehouse>("inventory/warehouses", { query: { page_size: 200, is_active: "true" } }))
      : Promise.resolve(null),
  ]);
  const previous = latest?.ok ? latest.data.results[0] : undefined;
  const defaultWarehouse = warehouses?.ok ? warehouses.data.results.find((row) => row.is_default) : undefined;

  const initial: RecurringInvoiceFormInitial = {
    warehouse_id: previous?.warehouse ?? defaultWarehouse?.id ?? null,
    ...(previous
      ? {
          receivable_account_id: previous.receivable_account,
          tax_payable_account_id: previous.tax_payable_account,
          rememberedFrom: previous.invoice_number || null,
        }
      : {}),
  };
  const customerId = paramOf(params, "customer");
  if (customerId) initial.customer_id = customerId;

  return (
    <>
      <PageHeader
        title="New recurring invoice"
        breadcrumbs={CRUMBS}
        description="The template itself posts nothing. On each scheduled date a draft invoice is created from it for review."
      />
      <PageBody className="max-w-6xl">
        <RecurringInvoiceForm initial={initial} />
      </PageBody>
    </>
  );
}
