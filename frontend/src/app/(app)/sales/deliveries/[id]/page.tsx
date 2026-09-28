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
import { DELIVERY_STATUS, INVOICE_STATUS, StatusBadge } from "@/components/ui/status-badge";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { DocumentAction } from "@/features/shared/document-action";
import { PartyCard } from "@/features/documents/document-view";
import { serverApi, tryServer } from "@/lib/api/server";
import { recordsById } from "@/lib/api/lookups";
import { wholeList } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import { formatDate, formatDateTime } from "@/lib/datetime";
import type { Item } from "@/types/api/items";
import type { Warehouse } from "@/types/api/inventory";
import type { Customer, DeliveryChallan, DeliveryChallanLine, Invoice, SalesOrder } from "@/types/api/sales";

export const metadata: Metadata = { title: "Delivery challan" };

export default async function DeliveryDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.VIEW_DELIVERIES)) {
    return (
      <>
        <PageHeader title="Delivery challan" />
        <ForbiddenState resource="delivery challans" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<DeliveryChallan>(`sales/deliveries/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Delivery challan" />
        <ErrorState title="Could not load the delivery challan" message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  const challan = result.data;
  const canManage = roleHasPermission(role, PERMISSIONS.MANAGE_DELIVERIES);
  const isDraft = challan.status === "draft";
  const isOut = challan.status === "dispatched" || challan.status === "delivered";

  // Invoices link to challan LINES (source_delivery_challan_line) and the
  // list only filters by customer, so one page of the customer's invoices is
  // scanned for lines billing this challan.
  const [customer, items, warehouse, order, invoices] = await Promise.all([
    roleHasPermission(role, PERMISSIONS.VIEW_CUSTOMERS)
      ? tryServer(() => serverApi.get<Customer>(`sales/customers/${challan.customer}`))
      : Promise.resolve(null),
    recordsById<Item>("items", challan.lines.map((line) => line.item)),
    roleHasPermission(role, PERMISSIONS.VIEW_INVENTORY)
      ? tryServer(() => serverApi.get<Warehouse>(`inventory/warehouses/${challan.warehouse}`))
      : Promise.resolve(null),
    challan.source_sales_order && roleHasPermission(role, PERMISSIONS.VIEW_ORDERS)
      ? tryServer(() => serverApi.get<SalesOrder>(`sales/orders/${challan.source_sales_order}`))
      : Promise.resolve(null),
    isOut && roleHasPermission(role, PERMISSIONS.VIEW_INVOICES)
      ? tryServer(() => serverApi.list<Invoice>("sales/invoices", { query: { customer: challan.customer, page_size: 200 } }))
      : Promise.resolve(null),
  ]);

  const customerName = customer?.ok ? customer.data.display_name : null;
  const warehouseName = warehouse?.ok ? warehouse.data.name : "the warehouse";
  const lineIds = new Set(challan.lines.map((line) => line.id));
  const billing = invoices?.ok
    ? invoices.data.results.filter((invoice) =>
        invoice.lines.some((line) => line.source_delivery_challan_line && lineIds.has(line.source_delivery_challan_line)),
      )
    : [];
  const lineCount = `${challan.lines.length} ${challan.lines.length === 1 ? "line" : "lines"}`;

  const lineColumns: Column<DeliveryChallanLine>[] = [
    { key: "line_number", header: "#", width: "2.5rem", cell: (line) => line.line_number },
    {
      key: "item",
      header: "Item",
      cell: (line) => {
        const item = items.get(line.item);
        return (
          <div className="min-w-0">
            <Link href={`/items/${line.item}`} className="font-medium text-brand-700 hover:underline">
              {item?.name ?? "Item"}
            </Link>
            <p className="text-xs text-ink-500">
              {item ? <span className="tabular">{item.sku}</span> : null}
              {line.description && line.description !== item?.name ? <span> · {line.description}</span> : null}
            </p>
          </div>
        );
      },
    },
    {
      key: "source",
      header: "Order line",
      hideBelow: "sm",
      cell: (line) => (line.source_order_line ? "Linked" : <span className="text-ink-400">—</span>),
    },
    { key: "quantity", header: "Quantity", numeric: true, cell: (line) => <Quantity value={line.quantity} /> },
  ];

  const invoiceColumns: Column<Invoice>[] = [
    {
      key: "number",
      header: "Invoice",
      cell: (row) => (row.invoice_number ? <span className="tabular">{row.invoice_number}</span> : "Draft invoice"),
    },
    { key: "date", header: "Date", cell: (row) => formatDate(row.invoice_date) },
    { key: "status", header: "Status", cell: (row) => <StatusBadge status={row.status} map={INVOICE_STATUS} size="sm" /> },
    { key: "total", header: "Total", numeric: true, cell: (row) => <Money value={row.total} currency={row.currency} /> },
  ];

  return (
    <>
      <PageHeader
        title={challan.challan_number}
        breadcrumbs={[{ label: "Delivery challans", href: "/sales/deliveries" }, { label: challan.challan_number }]}
        meta={<StatusBadge status={challan.status} map={DELIVERY_STATUS} />}
        description={customerName ? `Delivered to ${customerName}` : undefined}
        actions={
          <>
            <PrintButton />
            {canManage && isDraft ? <LinkButton href={`/sales/deliveries/${challan.id}/edit`}>Edit</LinkButton> : null}
            {/* cancel_delivery: only a DRAFT challan, before any stock has moved. */}
            {canManage && isDraft ? (
              <DocumentAction
                resource="sales/deliveries"
                id={challan.id}
                action="cancel"
                label="Cancel challan"
                variant="danger"
                confirmTitle={`Cancel ${challan.challan_number}?`}
                confirmMessage={
                  <p>
                    The draft is kept on record as cancelled. It has not been dispatched, so no stock has left{" "}
                    {warehouseName} and nothing is reversed. This cannot be undone.
                  </p>
                }
                successTitle="Delivery challan cancelled"
              />
            ) : null}
            {isOut && roleHasPermission(role, PERMISSIONS.CREATE_INVOICE) ? (
              <LinkButton href={`/sales/invoices/new?delivery=${challan.id}`} variant={billing.length > 0 ? "secondary" : "primary"}>
                Create invoice
              </LinkButton>
            ) : null}
            {canManage && challan.status === "dispatched" ? (
              <DocumentAction
                resource="sales/deliveries"
                id={challan.id}
                action="deliver"
                label="Mark delivered"
                confirmTitle={`Mark ${challan.challan_number} delivered?`}
                confirmMessage={
                  <p>
                    Records that the goods reached the customer. Stock and the ledger do not change — the stock already
                    left {warehouseName} when the challan was dispatched.
                  </p>
                }
                successTitle="Challan marked delivered"
              />
            ) : null}
            {canManage && isDraft ? (
              <DocumentAction
                resource="sales/deliveries"
                id={challan.id}
                action="dispatch"
                label="Dispatch"
                variant="primary"
                confirmTitle={`Dispatch ${challan.challan_number}?`}
                confirmMessage={
                  <>
                    <p>
                      Issues the {lineCount} out of {warehouseName} into the stock ledger, each valued at the item&apos;s
                      current weighted-average cost there. Unless the organization allows negative stock, dispatch is
                      refused when there is not enough on hand.
                    </p>
                    <p className="mt-2">
                      No journal is posted now: cost of goods sold is booked when the invoice billing this challan is
                      posted, and that invoice does not issue the stock again.
                      {challan.source_sales_order ? " The sales order's fulfilment status is updated." : ""}
                    </p>
                    <p className="mt-2">A dispatched challan cannot be edited or cancelled.</p>
                  </>
                }
                successTitle="Challan dispatched"
              />
            ) : null}
          </>
        }
      />

      <PageBody className="print-document">
        <div className="grid gap-4 lg:grid-cols-3">
          <PartyCard
            title="Customer"
            href={`/sales/customers/${challan.customer}`}
            name={customerName}
            code={customer?.ok ? customer.data.customer_code : null}
            gstin={customer?.ok ? customer.data.gstin : null}
            address={customer?.ok ? customer.data.billing_address : null}
          />
          <Card className="lg:col-span-2">
            <CardHeader title="Details" />
            <CardBody>
              <DetailList
                items={[
                  { label: "Challan date", value: formatDate(challan.challan_date) },
                  { label: "Warehouse", value: warehouse?.ok ? warehouse.data.name : "Configured" },
                  {
                    label: "Sales order",
                    value: challan.source_sales_order ? (
                      <Link href={`/sales/orders/${challan.source_sales_order}`} className="tabular text-brand-700 hover:underline">
                        {order?.ok ? order.data.order_number : "View order"}
                      </Link>
                    ) : (
                      "None"
                    ),
                  },
                  {
                    label: "Dispatched",
                    value: challan.dispatched_at
                      ? formatDateTime(challan.dispatched_at, { timeZone: session.timeZone })
                      : "Not yet — no stock has moved",
                  },
                  ...(challan.delivered_at
                    ? [{ label: "Delivered", value: formatDateTime(challan.delivered_at, { timeZone: session.timeZone }) }]
                    : []),
                  ...(challan.notes
                    ? [{ label: "Notes", value: <span className="whitespace-pre-line">{challan.notes}</span>, span: true }]
                    : []),
                ]}
              />
            </CardBody>
          </Card>
        </div>

        <Section title="Lines" description="A challan records quantities only; prices are set on the invoice.">
          <DataTable
            caption={`Lines of ${challan.challan_number}`}
            columns={lineColumns}
            data={wholeList(challan.lines)}
            getRowId={(line) => line.id}
            emptyTitle="No lines"
            page={1}
            pageSize={Math.max(challan.lines.length, 1)}
            buildPageHref={() => `/sales/deliveries/${challan.id}`}
          />
        </Section>

        {invoices ? (
          <Section title="Invoices" description="Invoices with lines billing this challan.">
            <DataTable
              caption={`Invoices billing ${challan.challan_number}`}
              columns={invoiceColumns}
              data={invoices.ok ? wholeList(billing) : undefined}
              error={invoices.ok ? null : (invoices.error as ApiError)}
              getRowId={(row) => row.id}
              getRowHref={(row) => `/sales/invoices/${row.id}`}
              emptyTitle="Not invoiced yet"
              {...(roleHasPermission(role, PERMISSIONS.CREATE_INVOICE)
                ? { emptyAction: { label: "Create invoice", href: `/sales/invoices/new?delivery=${challan.id}` } }
                : {})}
              page={1}
              pageSize={Math.max(billing.length, 1)}
              buildPageHref={() => `/sales/deliveries/${challan.id}`}
            />
          </Section>
        ) : null}
      </PageBody>
    </>
  );
}
