import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { EmptyState, ErrorState, ForbiddenState } from "@/components/ui/states";
import { DeliveryForm, type DeliveryFormInitial } from "@/features/sales/delivery-form";
import { fulfillmentByOrderLine, trimQuantity, type LineFulfillment } from "@/features/sales/fulfillment";
import { serverApi, tryServer } from "@/lib/api/server";
import { recordsById } from "@/lib/api/lookups";
import { paramOf, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { referenceOf } from "@/lib/api/errors";
import { money } from "@/lib/money";
import type { Item } from "@/types/api/items";
import type { Warehouse } from "@/types/api/inventory";
import type { DeliveryChallan, SalesOrder } from "@/types/api/sales";

export const metadata: Metadata = { title: "New delivery challan" };

const CRUMBS = [{ label: "Delivery challans", href: "/sales/deliveries" }, { label: "New" }];

/**
 * `?order=<id>` delivers against a confirmed sales order: the customer is the
 * order's, and each tracked-product line arrives linked through
 * `source_order_line_id` with what is LEFT to deliver.
 *
 * "Left" is derived here from the order's dispatched/delivered challans
 * (features/sales/fulfillment.ts) because the API exposes no fulfilled
 * quantity. When that cannot be derived completely — the challan list could
 * not be read in full — lines fall back to the ordered quantity and the page
 * says so. The backend refuses any over-delivery either way (over_fulfillment).
 *
 * `?customer=<id>` preselects the customer for a challan without an order.
 */
export default async function NewDeliveryPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.MANAGE_DELIVERIES)) {
    return (
      <>
        <PageHeader title="New delivery challan" breadcrumbs={CRUMBS} />
        <ForbiddenState resource="creating delivery challans" />
      </>
    );
  }

  const orderId = paramOf(params, "order");

  const [orderResult, warehouses] = await Promise.all([
    orderId ? tryServer(() => serverApi.get<SalesOrder>(`sales/orders/${orderId}`)) : Promise.resolve(null),
    roleHasPermission(role, PERMISSIONS.VIEW_INVENTORY)
      ? tryServer(() => serverApi.list<Warehouse>("inventory/warehouses", { query: { page_size: 200, is_active: "true" } }))
      : Promise.resolve(null),
  ]);
  const defaultWarehouse = warehouses?.ok ? warehouses.data.results.find((row) => row.is_default) : undefined;

  if (orderResult && !orderResult.ok) {
    return (
      <>
        <PageHeader title="New delivery challan" breadcrumbs={CRUMBS} />
        <ErrorState
          title="Could not load the sales order to deliver"
          message={orderResult.error.message}
          reference={referenceOf(orderResult.error)}
        />
      </>
    );
  }

  const initial: DeliveryFormInitial = { warehouse_id: defaultWarehouse?.id ?? null };
  const customerParam = paramOf(params, "customer");
  if (customerParam) initial.customer_id = customerParam;

  if (!orderResult) {
    return (
      <>
        <PageHeader
          title="New delivery challan"
          breadcrumbs={CRUMBS}
          description="Saved as a draft with its challan number. No stock moves until it is dispatched."
        />
        <PageBody className="max-w-5xl">
          <DeliveryForm initial={initial} />
        </PageBody>
      </>
    );
  }

  const order = orderResult.data;
  const orderCrumbs = [
    { label: "Sales orders", href: "/sales/orders" },
    { label: order.order_number, href: `/sales/orders/${order.id}` },
    { label: "New delivery challan" },
  ];

  // create_delivery_challan: only CONFIRMED or PARTIALLY_FULFILLED orders
  // accept a challan (sales_order_not_confirmed).
  if (order.status !== "confirmed" && order.status !== "partially_fulfilled") {
    return (
      <>
        <PageHeader title="New delivery challan" breadcrumbs={orderCrumbs} />
        <EmptyState
          title={`${order.order_number} cannot be delivered against`}
          description="Only a confirmed or partially fulfilled sales order accepts delivery challans."
          action={{ label: `Back to ${order.order_number}`, href: `/sales/orders/${order.id}` }}
        />
      </>
    );
  }

  const [items, challans] = await Promise.all([
    recordsById<Item>("items", order.lines.map((line) => line.item)),
    roleHasPermission(role, PERMISSIONS.VIEW_DELIVERIES)
      ? tryServer(() =>
          serverApi.list<DeliveryChallan>("sales/deliveries", { query: { customer: order.customer, page_size: 200 } }),
        )
      : Promise.resolve(null),
  ]);

  const derived = Boolean(challans?.ok && challans.data.next === null);
  const progress = derived && challans?.ok ? fulfillmentByOrderLine(order, challans.data.results) : null;

  // Only tracked products can be delivered (item_not_deliverable). An item
  // that could not be loaded is kept and left to the backend to judge.
  const deliverable = order.lines.filter((line) => {
    const item = items.get(line.item);
    return !item || (item.item_type === "product" && item.track_inventory);
  });
  const skipped = order.lines.length - deliverable.length;

  const lines = deliverable
    .map((line) => ({ line, left: progress?.get(line.id)?.remaining ?? line.quantity }))
    .filter(({ left }) => money(left).gt(0))
    .map(({ line, left }) => ({
      item_id: line.item,
      description: line.description,
      quantity: trimQuantity(left),
      source_order_line_id: line.id,
    }));

  if (lines.length === 0) {
    return (
      <>
        <PageHeader title="New delivery challan" breadcrumbs={orderCrumbs} />
        <EmptyState
          title="Nothing left to deliver"
          description={
            deliverable.length === 0
              ? `${order.order_number} has no lines for inventory-tracked products; service lines are not delivered.`
              : `Every tracked-product line on ${order.order_number} has already been dispatched.`
          }
          action={{ label: `Back to ${order.order_number}`, href: `/sales/orders/${order.id}` }}
        />
      </>
    );
  }

  initial.customer_id = order.customer;
  initial.source_sales_order_id = order.id;
  initial.lines = lines;

  const notes = [
    progress
      ? "Quantities are what is left after this order's dispatched challans."
      : "Quantities are the ordered quantities: what has already been dispatched could not be worked out here, so check them.",
    skipped > 0 ? `${skipped} service or untracked ${skipped === 1 ? "line was" : "lines were"} left out.` : null,
  ].filter(Boolean);

  return (
    <>
      <PageHeader title="New delivery challan" breadcrumbs={orderCrumbs} description={notes.join(" ")} />
      <PageBody className="max-w-5xl">
        <DeliveryForm
          initial={initial}
          orderLabel={order.order_number}
          fulfillment={progress ? (Object.fromEntries(progress) as Record<string, LineFulfillment>) : {}}
        />
      </PageBody>
    </>
  );
}
