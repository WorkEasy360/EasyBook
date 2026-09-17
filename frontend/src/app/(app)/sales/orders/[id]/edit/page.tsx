import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { SalesOrderForm } from "@/features/sales/sales-order-form";
import { serverApi, tryServer } from "@/lib/api/server";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import type { SalesOrder } from "@/types/api/sales";

export const metadata: Metadata = { title: "Edit sales order" };

export default async function EditSalesOrderPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;

  if (!roleHasPermission(session.role, PERMISSIONS.MANAGE_ORDERS)) {
    return (
      <>
        <PageHeader title="Edit sales order" />
        <ForbiddenState resource="editing sales orders" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<SalesOrder>(`sales/orders/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Edit sales order" />
        <ErrorState title="Could not load the sales order" message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  // Only drafts are editable (sales_order_not_draft): a confirmed order may
  // already have challans delivering against its lines.
  if (result.data.status !== "draft") redirect(`/sales/orders/${id}`);

  const order = result.data;
  return (
    <>
      <PageHeader
        title={`Edit ${order.order_number}`}
        breadcrumbs={[
          { label: "Sales orders", href: "/sales/orders" },
          { label: order.order_number, href: `/sales/orders/${id}` },
          { label: "Edit" },
        ]}
      />
      <PageBody className="max-w-6xl">
        <SalesOrderForm order={order} />
      </PageBody>
    </>
  );
}
