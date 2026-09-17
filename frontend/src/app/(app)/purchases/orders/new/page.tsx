import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ForbiddenState } from "@/components/ui/states";
import { PurchaseOrderForm, type PurchaseOrderFormInitial } from "@/features/purchases/purchase-order-form";
import { serverApi, tryServer } from "@/lib/api/server";
import { paramOf, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import type { PurchaseOrder } from "@/types/api/purchases";

export const metadata: Metadata = { title: "New purchase order" };

const CRUMBS = [{ label: "Purchase orders", href: "/purchases/orders" }, { label: "New" }];

/** `?vendor=<id>` preselects the vendor (from a vendor page). */
export default async function NewPurchaseOrderPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.MANAGE_PURCHASE_ORDERS)) {
    return (
      <>
        <PageHeader title="New purchase order" breadcrumbs={CRUMBS} />
        <ForbiddenState resource="creating purchase orders" />
      </>
    );
  }

  // The warehouse on the most recent order is the best default available:
  // the backend has no organization-level receiving warehouse setting.
  const latest = await tryServer(() => serverApi.list<PurchaseOrder>("purchases/orders", { query: { page_size: 1 } }));
  const previous = latest.ok ? latest.data.results[0] : undefined;

  const initial: PurchaseOrderFormInitial = {};
  if (previous?.warehouse) initial.warehouse_id = previous.warehouse;
  const vendorId = paramOf(params, "vendor");
  if (vendorId) initial.vendor_id = vendorId;

  return (
    <>
      <PageHeader
        title="New purchase order"
        breadcrumbs={CRUMBS}
        description="Saved as a draft. Approve it to start receiving goods against it."
      />
      <PageBody className="max-w-6xl">
        <PurchaseOrderForm initial={initial} rememberedFrom={previous?.warehouse ? previous.order_number : null} />
      </PageBody>
    </>
  );
}
