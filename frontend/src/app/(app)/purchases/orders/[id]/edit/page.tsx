import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { PurchaseOrderForm } from "@/features/purchases/purchase-order-form";
import { serverApi, tryServer } from "@/lib/api/server";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import type { PurchaseOrder } from "@/types/api/purchases";

export const metadata: Metadata = { title: "Edit purchase order" };

export default async function EditPurchaseOrderPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;

  if (!roleHasPermission(session.role, PERMISSIONS.MANAGE_PURCHASE_ORDERS)) {
    return (
      <>
        <PageHeader title="Edit purchase order" />
        <ForbiddenState resource="editing purchase orders" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<PurchaseOrder>(`purchases/orders/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Edit purchase order" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  // Only drafts are editable (purchase_order_not_draft). An approved order is
  // changed by cancelling or closing it, never by editing.
  if (result.data.status !== "draft") redirect(`/purchases/orders/${id}`);

  const order = result.data;

  return (
    <>
      <PageHeader
        title={`Edit ${order.order_number}`}
        breadcrumbs={[
          { label: "Purchase orders", href: "/purchases/orders" },
          { label: order.order_number, href: `/purchases/orders/${id}` },
          { label: "Edit" },
        ]}
      />
      <PageBody className="max-w-6xl">
        <PurchaseOrderForm order={order} />
      </PageBody>
    </>
  );
}
