import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { DetailList, PageBody, Section } from "@/components/ui/detail";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable, type Column } from "@/components/ui/data-table";
import { LinkButton } from "@/components/ui/link-button";
import { Quantity } from "@/components/ui/money";
import { PrintButton } from "@/components/ui/print-button";
import { DELIVERY_STATUS, SALES_ORDER_STATUS, StatusBadge } from "@/components/ui/status-badge";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { DocumentAction } from "@/features/shared/document-action";
import { DocumentTotalsCard, PartyCard, PricedLinesTable } from "@/features/documents/document-view";
import { fulfillmentByOrderLine } from "@/features/sales/fulfillment";
import { serverApi, tryServer } from "@/lib/api/server";
import { recordsById } from "@/lib/api/lookups";
import { wholeList } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import { formatDate } from "@/lib/datetime";
import type { Item } from "@/types/api/items";
import type { Customer, DeliveryChallan, SalesOrder, SalesOrderLine } from "@/types/api/sales";

export const metadata: Metadata = { title: "Sales order" };

const DELIVERABLE = new Set(["confirmed", "partially_fulfilled"]);

export default async function SalesOrderDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.VIEW_ORDERS)) {
    return (
      <>
        <PageHeader title="Sales order" />
        <ForbiddenState resource="sales orders" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<SalesOrder>(`sales/orders/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Sales order" />
        <ErrorState title="Could not load the sales order" message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  const order = result.data;
  const canManage = roleHasPermission(role, PERMISSIONS.MANAGE_ORDERS);
  const isDraft = order.status === "draft";

  // Challans point at the order through `source_sales_order`, but the list
  // only filters by customer — one page of the customer's challans is scanned.
  const [customer, items, challans] = await Promise.all([
    roleHasPermission(role, PERMISSIONS.VIEW_CUSTOMERS)
      ? tryServer(() => serverApi.get<Customer>(`sales/customers/${order.customer}`))
      : Promise.resolve(null),
    recordsById<Item>("items", order.lines.map((line) => line.item)),
    !isDraft && roleHasPermission(role, PERMISSIONS.VIEW_DELIVERIES)
      ? tryServer(() =>
          serverApi.list<DeliveryChallan>("sales/deliveries", { query: { customer: order.customer, page_size: 200 } }),
        )
      : Promise.resolve(null),
  ]);

  const customerName = customer?.ok ? customer.data.display_name : null;
  const related = challans?.ok ? challans.data.results.filter((challan) => challan.source_sales_order === order.id) : [];
  // Only shown when every challan of the customer was on the page scanned;
  // a partial sum would understate what has gone out.
  const complete = challans?.ok ? challans.data.next === null : false;
  const progress = complete ? fulfillmentByOrderLine(order, related) : null;

  const dispatchedColumn: Column<SalesOrderLine>[] = progress
    ? [
        {
          key: "dispatched",
          header: "Dispatched",
          numeric: true,
          cell: (line) => <Quantity value={progress.get(line.id)?.fulfilled ?? "0"} />,
        },
      ]
    : [];

  const challanColumns: Column<DeliveryChallan>[] = [
    { key: "number", header: "Challan", cell: (row) => <span className="tabular">{row.challan_number}</span> },
    { key: "date", header: "Date", cell: (row) => formatDate(row.challan_date) },
    { key: "status", header: "Status", cell: (row) => <StatusBadge status={row.status} map={DELIVERY_STATUS} size="sm" /> },
    { key: "lines", header: "Lines", numeric: true, hideBelow: "sm", cell: (row) => row.lines.length },
  ];

  return (
    <>
      <PageHeader
        title={order.order_number}
        breadcrumbs={[{ label: "Sales orders", href: "/sales/orders" }, { label: order.order_number }]}
        meta={<StatusBadge status={order.status} map={SALES_ORDER_STATUS} />}
        description={customerName ? `Ordered by ${customerName}` : undefined}
        actions={
          <>
            <PrintButton />
            {canManage && isDraft ? <LinkButton href={`/sales/orders/${order.id}/edit`}>Edit</LinkButton> : null}
            {/* sales/services/sales_orders.py: only DRAFT and CONFIRMED orders can be cancelled. */}
            {canManage && (isDraft || order.status === "confirmed") ? (
              <DocumentAction
                resource="sales/orders"
                id={order.id}
                action="cancel"
                label="Cancel order"
                variant="danger"
                confirmTitle={`Cancel ${order.order_number}?`}
                confirmMessage={
                  <p>
                    The order stays on record as cancelled and no further challans can be raised against it. An order
                    posts nothing and has moved no stock, so nothing is reversed. This cannot be undone.
                  </p>
                }
                successTitle="Sales order cancelled"
              />
            ) : null}
            {DELIVERABLE.has(order.status) && roleHasPermission(role, PERMISSIONS.MANAGE_DELIVERIES) ? (
              <LinkButton href={`/sales/deliveries/new?order=${order.id}`} variant={order.status === "confirmed" ? "secondary" : "primary"}>
                Create delivery challan
              </LinkButton>
            ) : null}
            {canManage && isDraft ? (
              <DocumentAction
                resource="sales/orders"
                id={order.id}
                action="confirm"
                label="Confirm order"
                variant="primary"
                confirmTitle={`Confirm ${order.order_number}?`}
                confirmMessage={
                  <p>
                    Confirming locks the order against editing and allows delivery challans against it. Nothing is
                    posted to the ledger and no stock moves until a challan is dispatched.
                  </p>
                }
                successTitle="Sales order confirmed"
              />
            ) : null}
          </>
        }
      />

      <PageBody className="print-document">
        <div className="grid gap-4 lg:grid-cols-3">
          <PartyCard
            title="Customer"
            href={`/sales/customers/${order.customer}`}
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
                  { label: "Order date", value: formatDate(order.order_date) },
                  { label: "Currency", value: order.currency },
                  ...(order.source_quote
                    ? [
                        {
                          label: "From quote",
                          value: (
                            <Link href={`/sales/quotes/${order.source_quote}`} className="text-brand-700 hover:underline">
                              View quote
                            </Link>
                          ),
                        },
                      ]
                    : []),
                ]}
              />
            </CardBody>
          </Card>
        </div>

        <Section
          title="Lines"
          {...(progress
            ? { description: "Dispatched quantities are summed from this order's dispatched and delivered challans below." }
            : {})}
        >
          <PricedLinesTable
            lines={order.lines}
            items={items}
            currency={order.currency}
            caption={`Lines of ${order.order_number}`}
            selfHref={`/sales/orders/${order.id}`}
            extraColumns={dispatchedColumn}
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
            <DocumentTotalsCard totals={order} currency={order.currency} footnote="Calculated by the accounting engine when the order was saved." />
          </div>
        </div>

        {challans ? (
          <Section title="Delivery challans" description="Challans raised against this order.">
            <DataTable
              caption={`Delivery challans for ${order.order_number}`}
              columns={challanColumns}
              data={challans.ok ? wholeList(related) : undefined}
              error={challans.ok ? null : (challans.error as ApiError)}
              getRowId={(row) => row.id}
              getRowHref={(row) => `/sales/deliveries/${row.id}`}
              emptyTitle="No delivery challans yet"
              {...(DELIVERABLE.has(order.status) && roleHasPermission(role, PERMISSIONS.MANAGE_DELIVERIES)
                ? { emptyAction: { label: "Create delivery challan", href: `/sales/deliveries/new?order=${order.id}` } }
                : {})}
              page={1}
              pageSize={Math.max(related.length, 1)}
              buildPageHref={() => `/sales/orders/${order.id}`}
            />
          </Section>
        ) : null}
      </PageBody>
    </>
  );
}
