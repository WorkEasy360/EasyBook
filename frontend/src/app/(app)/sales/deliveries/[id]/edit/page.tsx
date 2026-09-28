import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { DeliveryForm } from "@/features/sales/delivery-form";
import { fulfillmentByOrderLine, type LineFulfillment } from "@/features/sales/fulfillment";
import { serverApi, tryServer } from "@/lib/api/server";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import type { DeliveryChallan, SalesOrder } from "@/types/api/sales";

export const metadata: Metadata = { title: "Edit delivery challan" };

export default async function EditDeliveryPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.MANAGE_DELIVERIES)) {
    return (
      <>
        <PageHeader title="Edit delivery challan" />
        <ForbiddenState resource="editing delivery challans" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<DeliveryChallan>(`sales/deliveries/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Edit delivery challan" />
        <ErrorState title="Could not load the delivery challan" message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  // Only drafts are editable (delivery_not_draft): once dispatched, the stock
  // has left the warehouse.
  if (result.data.status !== "draft") redirect(`/sales/deliveries/${id}`);

  const challan = result.data;
  const [order, challans] = challan.source_sales_order
    ? await Promise.all([
        roleHasPermission(role, PERMISSIONS.VIEW_ORDERS)
          ? tryServer(() => serverApi.get<SalesOrder>(`sales/orders/${challan.source_sales_order}`))
          : Promise.resolve(null),
        tryServer(() =>
          serverApi.list<DeliveryChallan>("sales/deliveries", { query: { customer: challan.customer, page_size: 200 } }),
        ),
      ])
    : [null, null];

  // Hints only; this draft's own lines are excluded so they do not count against themselves.
  const progress =
    order?.ok && challans?.ok && challans.data.next === null
      ? (Object.fromEntries(fulfillmentByOrderLine(order.data, challans.data.results, challan.id)) as Record<string, LineFulfillment>)
      : {};

  return (
    <>
      <PageHeader
        title={`Edit ${challan.challan_number}`}
        breadcrumbs={[
          { label: "Delivery challans", href: "/sales/deliveries" },
          { label: challan.challan_number, href: `/sales/deliveries/${id}` },
          { label: "Edit" },
        ]}
      />
      <PageBody className="max-w-5xl">
        <DeliveryForm challan={challan} orderLabel={order?.ok ? order.data.order_number : null} fulfillment={progress} />
      </PageBody>
    </>
  );
}
