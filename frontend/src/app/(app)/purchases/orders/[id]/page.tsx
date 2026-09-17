import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { DetailList, PageBody, Section } from "@/components/ui/detail";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable, type Column } from "@/components/ui/data-table";
import { LinkButton } from "@/components/ui/link-button";
import { Money } from "@/components/ui/money";
import { PrintButton } from "@/components/ui/print-button";
import {
  BILL_STATUS,
  GOODS_RECEIPT_STATUS,
  PURCHASE_ORDER_STATUS,
  StatusBadge,
} from "@/components/ui/status-badge";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { DocumentAction } from "@/features/shared/document-action";
import { DocumentTotalsCard, PartyCard, PricedLinesTable } from "@/features/documents/document-view";
import {
  MatchError,
  MatchToleranceForm,
  PurchaseOrderMatchPanel,
  toleranceQuery,
} from "@/features/purchases/three-way-match";
import { outstandingOrderQuantity, isPositive } from "@/features/purchases/quantities";
import { serverApi, tryServer } from "@/lib/api/server";
import { recordsById } from "@/lib/api/lookups";
import { wholeList, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import { formatDate, formatDateTime } from "@/lib/datetime";
import type { Item } from "@/types/api/items";
import type { Warehouse } from "@/types/api/inventory";
import type { Bill, GoodsReceipt, PurchaseOrder, PurchaseOrderMatch, Vendor } from "@/types/api/purchases";

export const metadata: Metadata = { title: "Purchase order" };

export default async function PurchaseOrderDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<RawSearchParams>;
}) {
  const session = await requireSession();
  const { id } = await params;
  const search = await searchParams;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.VIEW_PURCHASE_ORDERS)) {
    return (
      <>
        <PageHeader title="Purchase order" />
        <ForbiddenState resource="purchase orders" />
      </>
    );
  }

  const tolerances = toleranceQuery(search);
  const [result, matchResult] = await Promise.all([
    tryServer(() => serverApi.get<PurchaseOrder>(`purchases/orders/${id}`)),
    tryServer(() => serverApi.get<PurchaseOrderMatch>(`purchases/orders/${id}/match`, { query: { ...tolerances } })),
  ]);

  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Purchase order" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  const order = result.data;
  const selfHref = `/purchases/orders/${order.id}`;
  const status = order.status;

  // Neither receipts nor bills can be filtered by purchase order — only by
  // vendor. One page of the vendor's documents is scanned for this order's.
  const [vendor, items, warehouse, receipts, bills] = await Promise.all([
    roleHasPermission(role, PERMISSIONS.VIEW_VENDORS)
      ? tryServer(() => serverApi.get<Vendor>(`purchases/vendors/${order.vendor}`))
      : Promise.resolve(null),
    recordsById<Item>("items", order.lines.map((line) => line.item)),
    order.warehouse
      ? tryServer(() => serverApi.get<Warehouse>(`inventory/warehouses/${order.warehouse}`))
      : Promise.resolve(null),
    status !== "draft" && roleHasPermission(role, PERMISSIONS.VIEW_GOODS_RECEIPTS)
      ? tryServer(() => serverApi.list<GoodsReceipt>("purchases/goods-receipts", { query: { vendor: order.vendor, page_size: 200 } }))
      : Promise.resolve(null),
    status !== "draft" && roleHasPermission(role, PERMISSIONS.VIEW_BILLS)
      ? tryServer(() => serverApi.list<Bill>("purchases/bills", { query: { vendor: order.vendor, page_size: 200 } }))
      : Promise.resolve(null),
  ]);

  const vendorName = vendor?.ok ? vendor.data.display_name : null;
  const orderReceipts = receipts?.ok ? receipts.data.results.filter((row) => row.source_purchase_order === order.id) : [];
  const orderBills = bills?.ok ? bills.data.results.filter((row) => row.source_purchase_order === order.id) : [];

  // Whether anything is still outstanding comes from the match endpoint's
  // server-side received/billed quantities; it only decides which shortcuts
  // to offer. The backend's over_receipt / over_billing guards still apply.
  const hasOutstanding = matchResult.ok
    ? matchResult.data.lines.some((line) =>
        isPositive(outstandingOrderQuantity(line.ordered_quantity, line.received_quantity, line.billed_quantity)),
      )
    : true;

  const isDraft = status === "draft";
  const receivable = status === "approved" || status === "partially_received";
  const canManage = roleHasPermission(role, PERMISSIONS.MANAGE_PURCHASE_ORDERS);

  const receiptColumns: Column<GoodsReceipt>[] = [
    { key: "number", header: "Receipt", cell: (row) => <span className="tabular">{row.receipt_number}</span> },
    { key: "date", header: "Date", cell: (row) => formatDate(row.receipt_date) },
    {
      key: "vendor_document_number",
      header: "Delivery note",
      hideBelow: "sm",
      cell: (row) => row.vendor_document_number || <span className="text-ink-400">—</span>,
    },
    { key: "status", header: "Status", cell: (row) => <StatusBadge status={row.status} map={GOODS_RECEIPT_STATUS} size="sm" /> },
  ];

  const billColumns: Column<Bill>[] = [
    {
      key: "number",
      header: "Bill",
      cell: (row) => (row.bill_number ? <span className="tabular">{row.bill_number}</span> : <span className="italic">Draft</span>),
    },
    { key: "date", header: "Date", cell: (row) => formatDate(row.bill_date) },
    { key: "status", header: "Status", cell: (row) => <StatusBadge status={row.status} map={BILL_STATUS} size="sm" /> },
    { key: "total", header: "Total", numeric: true, cell: (row) => <Money value={row.total} currency={row.currency} /> },
  ];

  return (
    <>
      <PageHeader
        title={order.order_number}
        breadcrumbs={[{ label: "Purchase orders", href: "/purchases/orders" }, { label: order.order_number }]}
        meta={<StatusBadge status={status} map={PURCHASE_ORDER_STATUS} />}
        description={vendorName ? `Ordered from ${vendorName}` : undefined}
        actions={
          <>
            <PrintButton />
            {isDraft && canManage ? <LinkButton href={`${selfHref}/edit`}>Edit</LinkButton> : null}
            {(isDraft || status === "approved") && canManage ? (
              <DocumentAction
                resource="purchases/orders"
                id={order.id}
                action="cancel"
                label="Cancel order"
                variant="danger"
                confirmTitle={`Cancel ${order.order_number}?`}
                confirmMessage={
                  <p>
                    The order is marked cancelled and nothing more can be received or edited against it. A purchase
                    order posts nothing to the ledger or stock, so there is nothing to reverse. This cannot be undone.
                  </p>
                }
                successTitle="Purchase order cancelled"
              />
            ) : null}
            {(status === "approved" || status === "partially_received" || status === "received") && canManage ? (
              <DocumentAction
                resource="purchases/orders"
                id={order.id}
                action="close"
                label="Close order"
                confirmTitle={`Close ${order.order_number}?`}
                confirmMessage={
                  <>
                    <p>
                      Closing short-closes the order: any quantity not yet received is no longer expected. Goods
                      receipts and bills already recorded are not changed.
                    </p>
                    <p className="mt-2">A closed order cannot be reopened.</p>
                  </>
                }
                successTitle="Purchase order closed"
              />
            ) : null}
            {receivable && hasOutstanding && roleHasPermission(role, PERMISSIONS.MANAGE_GOODS_RECEIPTS) ? (
              <LinkButton href={`/purchases/goods-receipts/new?order=${order.id}`}>Receive goods</LinkButton>
            ) : null}
            {(receivable || status === "received") && hasOutstanding && roleHasPermission(role, PERMISSIONS.CREATE_BILL) ? (
              <LinkButton href={`/purchases/bills/new?order=${order.id}`}>Create bill</LinkButton>
            ) : null}
            {isDraft && canManage ? (
              <DocumentAction
                resource="purchases/orders"
                id={order.id}
                action="approve"
                label="Approve order"
                variant="primary"
                confirmTitle={`Approve ${order.order_number}?`}
                confirmMessage={
                  <>
                    <p>
                      Approving commits to buying {order.lines.length} {order.lines.length === 1 ? "line" : "lines"} for{" "}
                      <Money value={order.total} currency={order.currency} /> and locks the order against editing.
                    </p>
                    <p className="mt-2">
                      Nothing is posted to the ledger or stock. Goods can then be received against it.
                    </p>
                  </>
                }
                successTitle="Purchase order approved"
              />
            ) : null}
          </>
        }
      />

      <PageBody className="print-document">
        <div className="grid gap-4 lg:grid-cols-3">
          <PartyCard
            title="Vendor"
            href={`/purchases/vendors/${order.vendor}`}
            name={vendorName}
            code={vendor?.ok ? vendor.data.vendor_code : null}
            gstin={vendor?.ok ? vendor.data.gstin : null}
            address={vendor?.ok ? vendor.data.billing_address : null}
          />
          <Card className="lg:col-span-2">
            <CardHeader title="Details" />
            <CardBody>
              <DetailList
                items={[
                  { label: "Order date", value: formatDate(order.order_date) },
                  { label: "Expected", value: order.expected_date ? formatDate(order.expected_date) : "—" },
                  { label: "Reference", value: order.reference || "—" },
                  { label: "Currency", value: order.currency },
                  {
                    label: "Deliver to",
                    value: warehouse?.ok ? warehouse.data.name : order.warehouse ? "Configured" : "Not specified",
                  },
                  { label: "Created", value: formatDateTime(order.created_at, { timeZone: session.timeZone }) },
                ]}
              />
            </CardBody>
          </Card>
        </div>

        <Section title="Lines">
          <PricedLinesTable
            lines={order.lines}
            items={items}
            currency={order.currency}
            caption={`Lines of ${order.order_number}`}
            selfHref={selfHref}
          />
        </Section>

        <div className="grid gap-4 lg:grid-cols-5">
          <div className="flex flex-col gap-4 lg:col-span-3">
            {order.notes || order.terms ? (
              <Card>
                <CardHeader title="Notes and terms" />
                <CardBody>
                  <DetailList
                    columns={1}
                    items={[
                      ...(order.notes ? [{ label: "Notes", value: <span className="whitespace-pre-line">{order.notes}</span> }] : []),
                      ...(order.terms ? [{ label: "Terms", value: <span className="whitespace-pre-line">{order.terms}</span> }] : []),
                    ]}
                  />
                </CardBody>
              </Card>
            ) : null}
          </div>
          <div className="lg:col-span-2">
            <DocumentTotalsCard
              totals={order}
              currency={order.currency}
              {...(isDraft ? { footnote: "Calculated by the accounting engine when the draft was saved." } : {})}
            />
          </div>
        </div>

        <Section
          title="Three-way match"
          description="Ordered against received and billed, per line. Informational: it never blocks a bill."
          actions={<MatchToleranceForm action={selfHref} query={tolerances} />}
        >
          {matchResult.ok ? (
            <PurchaseOrderMatchPanel match={matchResult.data} currency={order.currency} selfHref={selfHref} />
          ) : (
            <MatchError error={matchResult.error} />
          )}
        </Section>

        {receipts ? (
          <Section
            title="Goods receipts"
            description="Receipts raised against this order. Bill received goods from the receipt itself (Convert to bill), so posting does not receive the stock twice."
          >
            <DataTable
              caption={`Goods receipts for ${order.order_number}`}
              columns={receiptColumns}
              data={receipts.ok ? wholeList(orderReceipts) : undefined}
              error={receipts.ok ? null : (receipts.error as ApiError)}
              getRowId={(row) => row.id}
              getRowHref={(row) => `/purchases/goods-receipts/${row.id}`}
              emptyTitle="Nothing received yet"
              page={1}
              pageSize={Math.max(orderReceipts.length, 1)}
              buildPageHref={() => selfHref}
            />
          </Section>
        ) : null}

        {bills ? (
          <Section title="Bills" description="Bills raised against this order, including drafts.">
            <DataTable
              caption={`Bills for ${order.order_number}`}
              columns={billColumns}
              data={bills.ok ? wholeList(orderBills) : undefined}
              error={bills.ok ? null : (bills.error as ApiError)}
              getRowId={(row) => row.id}
              getRowHref={(row) => `/purchases/bills/${row.id}`}
              emptyTitle="Not billed yet"
              page={1}
              pageSize={Math.max(orderBills.length, 1)}
              buildPageHref={() => selfHref}
            />
          </Section>
        ) : null}
      </PageBody>
    </>
  );
}
