import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { DataTable, type Column } from "@/components/ui/data-table";
import { FilterBar, SortNote } from "@/components/ui/filter-bar";
import { LinkButton } from "@/components/ui/link-button";
import { Money } from "@/components/ui/money";
import { SALES_ORDER_STATUS, StatusBadge, statusOptions } from "@/components/ui/status-badge";
import { ForbiddenState } from "@/components/ui/states";
import { customerFilterGroups } from "@/features/sales/list-helpers";
import { serverApi, tryServer } from "@/lib/api/server";
import { indexList } from "@/lib/api/lookups";
import { capabilitiesFor } from "@/lib/api/capabilities";
import { parseListQuery, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { formatDate } from "@/lib/datetime";
import type { ApiError } from "@/lib/api/errors";
import type { Customer, SalesOrder } from "@/types/api/sales";

export const metadata: Metadata = { title: "Sales orders" };

const RESOURCE = "sales/orders";

export default async function SalesOrdersPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_ORDERS)) {
    return (
      <>
        <PageHeader title="Sales orders" />
        <ForbiddenState resource="sales orders" />
      </>
    );
  }

  const canManage = roleHasPermission(session.role, PERMISSIONS.MANAGE_ORDERS);
  const query = parseListQuery("/sales/orders", RESOURCE, params);
  const result = await tryServer(() => serverApi.list<SalesOrder>(RESOURCE, { query: query.apiParams }));

  const customerIds = result.ok ? result.data.results.map((row) => row.customer) : [];
  if (query.filters["customer"]) customerIds.push(query.filters["customer"]);
  const customers = roleHasPermission(session.role, PERMISSIONS.VIEW_CUSTOMERS)
    ? await indexList<Customer>("sales/customers", customerIds)
    : new Map<string, Customer>();

  const columns: Column<SalesOrder>[] = [
    { key: "order_number", header: "Order", cell: (row) => <span className="tabular">{row.order_number}</span> },
    {
      key: "customer",
      header: "Customer",
      cell: (row) => customers.get(row.customer)?.display_name ?? <span className="text-ink-400">Customer</span>,
    },
    {
      key: "order_date",
      header: "Date",
      hideBelow: "sm",
      cell: (row) => <span className="tabular whitespace-nowrap">{formatDate(row.order_date)}</span>,
    },
    {
      key: "status",
      header: "Status",
      cell: (row) => <StatusBadge status={row.status} map={SALES_ORDER_STATUS} size="sm" />,
    },
    {
      key: "total",
      header: "Total",
      numeric: true,
      cell: (row) => <Money value={row.total} currency={row.currency} strong />,
    },
  ];

  return (
    <>
      <PageHeader
        title="Sales orders"
        description="Confirming an order posts nothing. Stock leaves when a delivery challan against it is dispatched."
        actions={
          canManage ? (
            <LinkButton href="/sales/orders/new" variant="primary">
              New sales order
            </LinkButton>
          ) : null
        }
      />
      <PageBody>
        <FilterBar
          groups={[
            {
              key: "status",
              label: "Status",
              value: query.filters["status"] ?? "",
              options: statusOptions(SALES_ORDER_STATUS),
            },
            ...customerFilterGroups(query, customers),
          ]}
          buildFilterHref={query.buildFilterHref}
          clearHref={query.clearHref}
          activeFilterCount={query.activeFilterCount}
        />
        <DataTable
          caption="Sales orders"
          columns={columns}
          data={result.ok ? result.data : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          getRowId={(row) => row.id}
          getRowHref={(row) => `/sales/orders/${row.id}`}
          emptyTitle={query.activeFilterCount > 0 ? "No sales orders match these filters" : "No sales orders yet"}
          emptyDescription="Create an order directly, or convert an accepted quote."
          {...(canManage ? { emptyAction: { label: "New sales order", href: "/sales/orders/new" } } : {})}
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={query.buildPageHref}
          note={<SortNote description={capabilitiesFor(RESOURCE).defaultOrder ?? "newest first"} />}
        />
      </PageBody>
    </>
  );
}
