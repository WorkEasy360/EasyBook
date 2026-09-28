import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { EmptyState, ErrorState, ForbiddenState } from "@/components/ui/states";
import { GoodsReceiptForm, type GoodsReceiptFormInitial } from "@/features/purchases/goods-receipt-form";
import { isPositive, outstandingOrderQuantity } from "@/features/purchases/quantities";
import { serverApi, tryServer } from "@/lib/api/server";
import { recordsById } from "@/lib/api/lookups";
import { paramOf, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { referenceOf } from "@/lib/api/errors";
import type { Item } from "@/types/api/items";
import type { PurchaseOrder, PurchaseOrderMatch } from "@/types/api/purchases";

export const metadata: Metadata = { title: "New goods receipt" };

const CRUMBS = [{ label: "Goods receipts", href: "/purchases/goods-receipts" }, { label: "New" }];

/**
 * `?order=<id>` receives against a purchase order: vendor and warehouse come
 * from the order, and each line arrives linked through
 * `source_order_line_id`, which is what lets the backend enforce
 * over_receipt and move the order to partially received / received.
 *
 * Quantities start at what the match endpoint says is still outstanding
 * (see features/purchases/quantities.ts :: outstandingOrderQuantity), and only
 * inventory-tracked items are offered — the only kind a receipt accepts.
 */
export default async function NewGoodsReceiptPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.MANAGE_GOODS_RECEIPTS)) {
    return (
      <>
        <PageHeader title="New goods receipt" breadcrumbs={CRUMBS} />
        <ForbiddenState resource="recording goods receipts" />
      </>
    );
  }

  const orderId = paramOf(params, "order");
  if (!orderId) {
    return (
      <>
        <PageHeader
          title="New goods receipt"
          breadcrumbs={CRUMBS}
          description="Saved as a draft. Stock is added only when the receipt is marked received."
        />
        <PageBody className="max-w-5xl">
          <GoodsReceiptForm />
        </PageBody>
      </>
    );
  }

  const [orderResult, matchResult] = await Promise.all([
    tryServer(() => serverApi.get<PurchaseOrder>(`purchases/orders/${orderId}`)),
    tryServer(() => serverApi.get<PurchaseOrderMatch>(`purchases/orders/${orderId}/match`)),
  ]);

  if (!orderResult.ok || !matchResult.ok) {
    const error = !orderResult.ok ? orderResult.error : !matchResult.ok ? matchResult.error : null;
    return (
      <>
        <PageHeader title="New goods receipt" breadcrumbs={CRUMBS} />
        <ErrorState
          title="Could not load the purchase order to receive"
          message={error?.message ?? "The purchase order could not be loaded."}
          reference={referenceOf(error)}
        />
      </>
    );
  }

  const order = orderResult.data;
  const orderHref = `/purchases/orders/${order.id}`;

  // services/goods_receipts.py :: purchase_order_not_approved.
  if (order.status !== "approved" && order.status !== "partially_received") {
    return (
      <>
        <PageHeader title="New goods receipt" breadcrumbs={CRUMBS} />
        <EmptyState
          title={`${order.order_number} cannot be received against`}
          description="Goods can only be received against an approved or partially received purchase order."
          action={{ label: `Back to ${order.order_number}`, href: orderHref }}
        />
      </>
    );
  }

  const items = await recordsById<Item>("items", order.lines.map((line) => line.item));
  const received = new Map(matchResult.data.lines.map((line) => [line.line_id, line]));

  const lines: NonNullable<GoodsReceiptFormInitial["lines"]> = [];
  let skippedUntracked = 0;
  for (const line of order.lines) {
    const item = items.get(line.item);
    // A service or untracked product has nothing to receive (item_not_receivable).
    if (!item || item.item_type !== "product" || !item.track_inventory) {
      skippedUntracked += 1;
      continue;
    }
    const match = received.get(line.id);
    const outstanding = outstandingOrderQuantity(line.quantity, match?.received_quantity, match?.billed_quantity);
    if (!isPositive(outstanding)) continue;
    lines.push({
      item_id: line.item,
      description: line.description,
      quantity: outstanding,
      // Blank: the backend takes the ordered unit price for a linked line.
      unit_cost: "",
      source_order_line_id: line.id,
    });
  }

  if (lines.length === 0) {
    return (
      <>
        <PageHeader title="New goods receipt" breadcrumbs={CRUMBS} />
        <EmptyState
          title="Nothing left to receive"
          description={`Every stocked line on ${order.order_number} has already been received or billed.`}
          action={{ label: `Back to ${order.order_number}`, href: orderHref }}
        />
      </>
    );
  }

  const initial: GoodsReceiptFormInitial = {
    vendor_id: order.vendor,
    source_purchase_order_id: order.id,
    order_number: order.order_number,
    lines,
    ...(order.warehouse ? { warehouse_id: order.warehouse } : {}),
  };

  return (
    <>
      <PageHeader
        title="New goods receipt"
        breadcrumbs={CRUMBS}
        description={
          skippedUntracked > 0
            ? `Receiving ${order.order_number}. ${skippedUntracked} line(s) for services or untracked items are billed directly and are not listed.`
            : `Receiving ${order.order_number}. Adjust quantities to what actually arrived.`
        }
      />
      <PageBody className="max-w-5xl">
        <GoodsReceiptForm initial={initial} />
      </PageBody>
    </>
  );
}
