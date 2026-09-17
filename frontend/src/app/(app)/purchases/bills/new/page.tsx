import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { EmptyState, ErrorState, ForbiddenState } from "@/components/ui/states";
import { BillForm, type BillFormInitial } from "@/features/purchases/bill-form";
import { isPositive, outstandingOrderQuantity } from "@/features/purchases/quantities";
import { serverApi, tryServer } from "@/lib/api/server";
import { paramOf, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { referenceOf } from "@/lib/api/errors";
import { addDays, todayInZone } from "@/lib/datetime";
import type { Bill, PurchaseOrder, PurchaseOrderMatch, Vendor } from "@/types/api/purchases";

export const metadata: Metadata = { title: "New bill" };

const CRUMBS = [{ label: "Bills", href: "/purchases/bills" }, { label: "New" }];

/**
 * `?vendor=<id>` preselects the vendor. `?order=<id>` bills a purchase order:
 * lines arrive at the ordered price, linked through `source_order_line_id`
 * for the three-way match, and the bill carries `source_purchase_order_id`.
 *
 * Only what is neither received nor billed is prefilled
 * (quantities.ts :: outstandingOrderQuantity). Goods already received on a
 * goods receipt are billed from that receipt ("Convert to bill"), because a
 * bill line linked only to the ORDER receives its stock again on posting —
 * the backend's no-double-receipt guard keys on the receipt line.
 */
export default async function NewBillPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.CREATE_BILL)) {
    return (
      <>
        <PageHeader title="New bill" breadcrumbs={CRUMBS} />
        <ForbiddenState resource="creating bills" />
      </>
    );
  }

  const orderId = paramOf(params, "order");
  const today = todayInZone(session.timeZone);

  // The accounts on the most recent bill are the best default available: the
  // backend has no organization-level default payable account.
  const [latest, orderResult, matchResult] = await Promise.all([
    tryServer(() => serverApi.list<Bill>("purchases/bills", { query: { page_size: 1 } })),
    orderId ? tryServer(() => serverApi.get<PurchaseOrder>(`purchases/orders/${orderId}`)) : Promise.resolve(null),
    orderId ? tryServer(() => serverApi.get<PurchaseOrderMatch>(`purchases/orders/${orderId}/match`)) : Promise.resolve(null),
  ]);

  if ((orderResult && !orderResult.ok) || (matchResult && !matchResult.ok)) {
    const error = orderResult && !orderResult.ok ? orderResult.error : matchResult && !matchResult.ok ? matchResult.error : null;
    return (
      <>
        <PageHeader title="New bill" breadcrumbs={CRUMBS} />
        <ErrorState
          title="Could not load the purchase order to bill"
          message={error?.message ?? "The purchase order could not be loaded."}
          reference={referenceOf(error)}
        />
      </>
    );
  }

  const previous = latest.ok ? latest.data.results[0] : undefined;
  const initial: BillFormInitial = previous
    ? {
        payable_account_id: previous.payable_account,
        tax_recoverable_account_id: previous.tax_recoverable_account,
        price_variance_account_id: previous.price_variance_account,
        warehouse_id: previous.warehouse,
      }
    : {};

  const order = orderResult?.ok ? orderResult.data : null;
  const vendorId = order ? order.vendor : paramOf(params, "vendor");

  if (order && matchResult?.ok) {
    const orderHref = `/purchases/orders/${order.id}`;
    if (order.status === "draft" || order.status === "cancelled" || order.status === "closed") {
      return (
        <>
          <PageHeader title="New bill" breadcrumbs={CRUMBS} />
          <EmptyState
            title={`${order.order_number} is ${order.status}`}
            description="Bill an approved purchase order. Draft orders are approved first; cancelled and closed orders take no further bills."
            action={{ label: `Back to ${order.order_number}`, href: orderHref }}
          />
        </>
      );
    }

    const matched = new Map(matchResult.data.lines.map((line) => [line.line_id, line]));
    initial.lines = order.lines.flatMap((line) => {
      const match = matched.get(line.id);
      const outstanding = outstandingOrderQuantity(line.quantity, match?.received_quantity, match?.billed_quantity);
      if (!isPositive(outstanding)) return [];
      return [
        {
          item_id: line.item,
          description: line.description,
          quantity: outstanding,
          unit_price: line.unit_price,
          discount_percent: line.discount_percent,
          tax_rate: line.tax_rate,
          expense_account_id: null,
          source_order_line_id: line.id,
          source_goods_receipt_line_id: null,
        },
      ];
    });

    if (initial.lines.length === 0) {
      return (
        <>
          <PageHeader title="New bill" breadcrumbs={CRUMBS} />
          <EmptyState
            title="Nothing left to bill directly"
            description={`Everything on ${order.order_number} has been received or billed. Bill received goods from their goods receipt.`}
            action={{ label: `Back to ${order.order_number}`, href: orderHref }}
          />
        </>
      );
    }

    initial.source_purchase_order_id = order.id;
    initial.order_number = order.order_number;
    initial.reference = order.order_number;
    if (order.warehouse) initial.warehouse_id = order.warehouse;
  }

  if (vendorId) {
    initial.vendor_id = vendorId;
    // The vendor's own defaults beat whatever the last bill used.
    const vendor = await tryServer(() => serverApi.get<Vendor>(`purchases/vendors/${vendorId}`));
    if (vendor.ok) {
      if (vendor.data.default_payable_account) initial.payable_account_id = vendor.data.default_payable_account;
      initial.due_date = addDays(today, vendor.data.payment_terms_days) ?? today;
    }
  }

  return (
    <>
      <PageHeader
        title="New bill"
        breadcrumbs={CRUMBS}
        description={
          order
            ? `Billing ${order.order_number} at the ordered prices. Check them and the tax against the vendor's invoice before saving.`
            : "Saved as a draft. Nothing is recorded in the ledger until the bill is posted."
        }
      />
      <PageBody className="max-w-6xl">
        <BillForm initial={initial} rememberedFrom={previous?.bill_number || null} />
      </PageBody>
    </>
  );
}
