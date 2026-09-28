import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { DetailList, PageBody, Section } from "@/components/ui/detail";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable, type Column } from "@/components/ui/data-table";
import { LinkButton } from "@/components/ui/link-button";
import { Money, Quantity } from "@/components/ui/money";
import { PrintButton } from "@/components/ui/print-button";
import { BILL_STATUS, GOODS_RECEIPT_STATUS, StatusBadge } from "@/components/ui/status-badge";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { DocumentAction } from "@/features/shared/document-action";
import { PartyCard } from "@/features/documents/document-view";
import { ConvertToBillButton } from "@/features/purchases/convert-to-bill-dialog";
import { billedByReceiptLine, exceeds } from "@/features/purchases/quantities";
import { serverApi, tryServer } from "@/lib/api/server";
import { recordsById } from "@/lib/api/lookups";
import { wholeList } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import { formatDate, formatDateTime } from "@/lib/datetime";
import type { Item } from "@/types/api/items";
import type { Warehouse } from "@/types/api/inventory";
import type { Bill, GoodsReceipt, GoodsReceiptLine, PurchaseOrder, Vendor } from "@/types/api/purchases";

export const metadata: Metadata = { title: "Goods receipt" };

export default async function GoodsReceiptDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.VIEW_GOODS_RECEIPTS)) {
    return (
      <>
        <PageHeader title="Goods receipt" />
        <ForbiddenState resource="goods receipts" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<GoodsReceipt>(`purchases/goods-receipts/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Goods receipt" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  const receipt = result.data;
  const selfHref = `/purchases/goods-receipts/${receipt.id}`;
  const isDraft = receipt.status === "draft";
  const isReceived = receipt.status === "received";

  const [vendor, warehouse, items, order, bills] = await Promise.all([
    roleHasPermission(role, PERMISSIONS.VIEW_VENDORS)
      ? tryServer(() => serverApi.get<Vendor>(`purchases/vendors/${receipt.vendor}`))
      : Promise.resolve(null),
    tryServer(() => serverApi.get<Warehouse>(`inventory/warehouses/${receipt.warehouse}`)),
    recordsById<Item>("items", receipt.lines.map((line) => line.item)),
    receipt.source_purchase_order && roleHasPermission(role, PERMISSIONS.VIEW_PURCHASE_ORDERS)
      ? tryServer(() => serverApi.get<PurchaseOrder>(`purchases/orders/${receipt.source_purchase_order}`))
      : Promise.resolve(null),
    // Bills cannot be filtered by receipt, only by vendor: one page of the
    // vendor's bills is scanned for lines that bill this receipt's lines.
    isReceived && roleHasPermission(role, PERMISSIONS.VIEW_BILLS)
      ? tryServer(() => serverApi.list<Bill>("purchases/bills", { query: { vendor: receipt.vendor, page_size: 200 } }))
      : Promise.resolve(null),
  ]);

  const receiptLineIds = new Set(receipt.lines.map((line) => line.id));
  const billsForReceipt = bills?.ok
    ? bills.data.results.filter((bill) => bill.lines.some((line) => line.source_goods_receipt_line && receiptLineIds.has(line.source_goods_receipt_line)))
    : [];

  // Whether to OFFER "Convert to bill" (see billedByReceiptLine). Display
  // logic only — the backend refuses a fully billed receipt regardless.
  const billedByLine = billedByReceiptLine(billsForReceipt);
  const hasUnbilled = receipt.lines.some((line) => exceeds(line.quantity, billedByLine.get(line.id)));

  const vendorName = vendor?.ok ? vendor.data.display_name : null;
  const warehouseName = warehouse.ok ? warehouse.data.name : "the receipt's warehouse";
  const canManage = roleHasPermission(role, PERMISSIONS.MANAGE_GOODS_RECEIPTS);

  const columns: Column<GoodsReceiptLine>[] = [
    { key: "line_number", header: "#", width: "2.5rem", cell: (line) => line.line_number },
    {
      key: "item",
      header: "Item",
      cell: (line) => {
        const item = items.get(line.item);
        return (
          <div className="min-w-0">
            <Link href={`/items/${line.item}`} className="font-medium text-brand-700 hover:underline">
              {line.description || item?.name || "Item"}
            </Link>
            {item ? <p className="tabular text-xs text-ink-500">{item.sku}</p> : null}
          </div>
        );
      },
    },
    { key: "quantity", header: "Quantity", numeric: true, cell: (line) => <Quantity value={line.quantity} /> },
    { key: "unit_cost", header: "Unit cost", numeric: true, cell: (line) => <Money value={line.unit_cost} /> },
    {
      key: "source",
      header: "Order line",
      hideBelow: "md",
      cell: (line) => (line.source_order_line ? "Linked" : <span className="text-ink-400">—</span>),
    },
    ...(isReceived && bills?.ok
      ? [
          {
            key: "billed",
            header: "Billed",
            numeric: true,
            cell: (line: GoodsReceiptLine) => <Quantity value={billedByLine.get(line.id) ?? "0"} />,
          } satisfies Column<GoodsReceiptLine>,
        ]
      : []),
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
        title={receipt.receipt_number}
        breadcrumbs={[{ label: "Goods receipts", href: "/purchases/goods-receipts" }, { label: receipt.receipt_number }]}
        meta={<StatusBadge status={receipt.status} map={GOODS_RECEIPT_STATUS} />}
        description={vendorName ? `Received from ${vendorName}` : undefined}
        actions={
          <>
            <PrintButton />
            {isDraft && canManage ? <LinkButton href={`${selfHref}/edit`}>Edit</LinkButton> : null}
            {isDraft && canManage ? (
              <DocumentAction
                resource="purchases/goods-receipts"
                id={receipt.id}
                action="cancel"
                label="Cancel receipt"
                variant="danger"
                confirmTitle={`Cancel ${receipt.receipt_number}?`}
                confirmMessage={
                  <p>
                    The draft is marked cancelled. No stock has moved yet, so nothing is reversed, and the quantities
                    no longer count against the purchase order. This cannot be undone.
                  </p>
                }
                successTitle="Goods receipt cancelled"
              />
            ) : null}
            {isReceived && hasUnbilled && roleHasPermission(role, PERMISSIONS.CREATE_BILL) ? (
              <ConvertToBillButton
                receiptId={receipt.id}
                receiptNumber={receipt.receipt_number}
                defaultPayableAccountId={vendor?.ok ? vendor.data.default_payable_account : null}
                paymentTermsDays={vendor?.ok ? vendor.data.payment_terms_days : null}
              />
            ) : null}
            {isDraft && canManage ? (
              <DocumentAction
                resource="purchases/goods-receipts"
                id={receipt.id}
                action="receive"
                label="Receive goods"
                variant="primary"
                confirmTitle={`Receive ${receipt.receipt_number}?`}
                confirmMessage={
                  <>
                    <p>
                      {receipt.lines.length} {receipt.lines.length === 1 ? "line" : "lines"} will be added to stock in{" "}
                      {warehouseName}, dated {formatDate(receipt.receipt_date)}, at the unit costs shown.
                      {receipt.source_purchase_order ? " The purchase order's received quantities update too." : ""}
                    </p>
                    <p className="mt-2">
                      No journal is posted: the bill for these goods records the payable. A received receipt cannot
                      be edited or cancelled — goods sent back are returned through a vendor credit.
                    </p>
                  </>
                }
                successTitle="Goods received"
              />
            ) : null}
          </>
        }
      />

      <PageBody className="print-document">
        <div className="grid gap-4 lg:grid-cols-3">
          <PartyCard
            title="Vendor"
            href={`/purchases/vendors/${receipt.vendor}`}
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
                  { label: "Receipt date", value: formatDate(receipt.receipt_date) },
                  { label: "Warehouse", value: warehouse.ok ? warehouse.data.name : "Configured" },
                  { label: "Vendor delivery note", value: receipt.vendor_document_number || "—" },
                  {
                    label: "Purchase order",
                    value: receipt.source_purchase_order ? (
                      <Link href={`/purchases/orders/${receipt.source_purchase_order}`} className="text-brand-700 hover:underline">
                        {order?.ok ? order.data.order_number : "View order"}
                      </Link>
                    ) : (
                      "None"
                    ),
                  },
                  {
                    label: "Received",
                    value: receipt.received_at
                      ? formatDateTime(receipt.received_at, { timeZone: session.timeZone })
                      : isDraft
                        ? "Not yet — no stock has moved"
                        : "—",
                  },
                  ...(receipt.notes ? [{ label: "Notes", value: <span className="whitespace-pre-line">{receipt.notes}</span>, span: true }] : []),
                ]}
              />
            </CardBody>
          </Card>
        </div>

        <Section
          title="Lines"
          {...(isReceived ? { description: "Received quantities are in stock. Billed counts every bill except voided ones, drafts included." } : {})}
        >
          <DataTable
            caption={`Lines of ${receipt.receipt_number}`}
            columns={columns}
            data={wholeList(receipt.lines)}
            getRowId={(line) => line.id}
            emptyTitle="No lines"
            page={1}
            pageSize={Math.max(receipt.lines.length, 1)}
            buildPageHref={() => selfHref}
          />
        </Section>

        {bills ? (
          <Section title="Bills for this receipt">
            <DataTable
              caption={`Bills for ${receipt.receipt_number}`}
              columns={billColumns}
              data={bills.ok ? wholeList(billsForReceipt) : undefined}
              error={bills.ok ? null : (bills.error as ApiError)}
              getRowId={(row) => row.id}
              getRowHref={(row) => `/purchases/bills/${row.id}`}
              emptyTitle="Not billed yet"
              emptyDescription="Convert this receipt to a bill once the vendor's invoice arrives."
              page={1}
              pageSize={Math.max(billsForReceipt.length, 1)}
              buildPageHref={() => selfHref}
            />
          </Section>
        ) : null}
      </PageBody>
    </>
  );
}
