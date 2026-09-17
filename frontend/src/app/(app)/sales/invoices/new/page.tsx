import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { InvoiceForm, type InvoiceFormInitial } from "@/features/sales/invoice-form";
import { serverApi, tryServer } from "@/lib/api/server";
import { recordsById } from "@/lib/api/lookups";
import { paramOf, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { referenceOf } from "@/lib/api/errors";
import type { Item } from "@/types/api/items";
import type { Warehouse } from "@/types/api/inventory";
import type { DeliveryChallan, Invoice } from "@/types/api/sales";

export const metadata: Metadata = { title: "New invoice" };

const CRUMBS = [{ label: "Invoices", href: "/sales/invoices" }, { label: "New" }];

/**
 * `?customer=<id>` preselects the customer (from a customer page).
 * `?delivery=<id>` bills a delivery challan: its lines arrive linked through
 * `source_delivery_challan_line_id`, which is what stops the backend issuing
 * the same stock a second time on posting.
 */
export default async function NewInvoicePage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.CREATE_INVOICE)) {
    return (
      <>
        <PageHeader title="New invoice" breadcrumbs={CRUMBS} />
        <ForbiddenState resource="creating invoices" />
      </>
    );
  }

  const deliveryId = paramOf(params, "delivery");

  // The accounts on the most recent invoice are the best default available:
  // the backend has no organization-level default receivable account.
  const [latest, delivery, warehouses] = await Promise.all([
    tryServer(() => serverApi.list<Invoice>("sales/invoices", { query: { page_size: 1 } })),
    deliveryId ? tryServer(() => serverApi.get<DeliveryChallan>(`sales/deliveries/${deliveryId}`)) : Promise.resolve(null),
    tryServer(() => serverApi.list<Warehouse>("inventory/warehouses", { query: { page_size: 200 } })),
  ]);

  if (delivery && !delivery.ok) {
    return (
      <>
        <PageHeader title="New invoice" breadcrumbs={CRUMBS} />
        <ErrorState
          title="Could not load the delivery challan to bill"
          message={delivery.error.message}
          reference={referenceOf(delivery.error)}
        />
      </>
    );
  }

  const previous = latest.ok ? latest.data.results[0] : undefined;
  // Posting a tracked product with no delivery needs a warehouse
  // (warehouse_required); the organization's default is the natural start.
  const defaultWarehouse = warehouses.ok
    ? warehouses.data.results.find((row) => row.is_default && row.is_active)
    : undefined;
  const initial: InvoiceFormInitial = {
    ...(previous
      ? {
          receivable_account_id: previous.receivable_account,
          tax_payable_account_id: previous.tax_payable_account,
        }
      : {}),
    warehouse_id: previous?.warehouse ?? defaultWarehouse?.id ?? null,
  };

  const customerId = delivery?.ok ? delivery.data.customer : paramOf(params, "customer");
  if (customerId) initial.customer_id = customerId;

  if (delivery?.ok) {
    const challan = delivery.data;
    const items = await recordsById<Item>("items", challan.lines.map((line) => line.item));
    initial.warehouse_id = challan.warehouse;
    initial.reference = challan.challan_number;
    initial.lines = challan.lines.map((line) => ({
      item_id: line.item,
      description: line.description,
      quantity: line.quantity,
      // A challan carries no prices; start from the item's list price.
      unit_price: items.get(line.item)?.sales_price ?? "",
      discount_percent: "",
      tax_rate: "",
      source_delivery_challan_line_id: line.id,
    }));
  }

  return (
    <>
      <PageHeader
        title="New invoice"
        breadcrumbs={CRUMBS}
        description={
          delivery?.ok
            ? `Billing delivery challan ${delivery.data.challan_number}. Check prices and tax before saving.`
            : "Saved as a draft. Nothing is recorded in the ledger until the invoice is posted."
        }
      />
      <PageBody className="max-w-6xl">
        <InvoiceForm initial={initial} rememberedFrom={previous?.invoice_number || null} />
      </PageBody>
    </>
  );
}
