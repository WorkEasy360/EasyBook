import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { DataTable, type Column } from "@/components/ui/data-table";
import { FilterBar, SortNote } from "@/components/ui/filter-bar";
import { LinkButton } from "@/components/ui/link-button";
import { Money } from "@/components/ui/money";
import { PURCHASE_ORDER_STATUS, StatusBadge, statusOptions } from "@/components/ui/status-badge";
import { ForbiddenState } from "@/components/ui/states";
import { serverApi, tryServer } from "@/lib/api/server";
import { capabilitiesFor } from "@/lib/api/capabilities";
import { indexList } from "@/lib/api/lookups";
import { parseListQuery, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { formatDate } from "@/lib/datetime";
import type { ApiError } from "@/lib/api/errors";
import type { PurchaseOrder, Vendor } from "@/types/api/purchases";

export const metadata: Metadata = { title: "Purchase orders" };

const RESOURCE = "purchases/orders";

export default async function PurchaseOrdersPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_PURCHASE_ORDERS)) {
    return (
      <>
        <PageHeader title="Purchase orders" />
        <ForbiddenState resource="purchase orders" />
      </>
    );
  }

  const canCreate = roleHasPermission(session.role, PERMISSIONS.MANAGE_PURCHASE_ORDERS);
  const query = parseListQuery("/purchases/orders", RESOURCE, params);
  const result = await tryServer(() => serverApi.list<PurchaseOrder>(RESOURCE, { query: query.apiParams }));

  const vendorFilter = query.filters["vendor"];
  const vendorIds = result.ok ? result.data.results.map((row) => row.vendor) : [];
  if (vendorFilter) vendorIds.push(vendorFilter);
  const vendors = roleHasPermission(session.role, PERMISSIONS.VIEW_VENDORS)
    ? await indexList<Vendor>("purchases/vendors", vendorIds)
    : new Map<string, Vendor>();

  const columns: Column<PurchaseOrder>[] = [
    { key: "order_number", header: "Order", cell: (row) => <span className="tabular">{row.order_number}</span> },
    {
      key: "vendor",
      header: "Vendor",
      cell: (row) => vendors.get(row.vendor)?.display_name ?? <span className="text-ink-400">Vendor</span>,
    },
    {
      key: "order_date",
      header: "Date",
      hideBelow: "sm",
      cell: (row) => <span className="tabular whitespace-nowrap">{formatDate(row.order_date)}</span>,
    },
    {
      key: "expected_date",
      header: "Expected",
      hideBelow: "md",
      cell: (row) =>
        row.expected_date ? (
          <span className="tabular whitespace-nowrap">{formatDate(row.expected_date)}</span>
        ) : (
          <span className="text-ink-400">—</span>
        ),
    },
    { key: "status", header: "Status", cell: (row) => <StatusBadge status={row.status} map={PURCHASE_ORDER_STATUS} size="sm" /> },
    { key: "total", header: "Total", numeric: true, cell: (row) => <Money value={row.total} currency={row.currency} strong /> },
  ];

  return (
    <>
      <PageHeader
        title="Purchase orders"
        description="What you have agreed to buy. Orders post nothing; receipts and bills are matched against them."
        actions={
          canCreate ? (
            <LinkButton href="/purchases/orders/new" variant="primary">
              New purchase order
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
              options: statusOptions(PURCHASE_ORDER_STATUS),
            },
            ...(vendorFilter
              ? [
                  {
                    key: "vendor",
                    label: "Vendor",
                    value: vendorFilter,
                    options: [{ value: vendorFilter, label: vendors.get(vendorFilter)?.display_name ?? "Selected vendor" }],
                  },
                ]
              : []),
          ]}
          buildFilterHref={query.buildFilterHref}
          clearHref={query.clearHref}
          activeFilterCount={query.activeFilterCount}
        />
        <DataTable
          caption="Purchase orders"
          columns={columns}
          data={result.ok ? result.data : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          getRowId={(row) => row.id}
          getRowHref={(row) => `/purchases/orders/${row.id}`}
          emptyTitle={query.activeFilterCount > 0 ? "No purchase orders match these filters" : "No purchase orders yet"}
          emptyDescription="Raise a purchase order to agree quantities and prices with a vendor."
          {...(canCreate ? { emptyAction: { label: "New purchase order", href: "/purchases/orders/new" } } : {})}
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={query.buildPageHref}
          note={<SortNote description={capabilitiesFor(RESOURCE).defaultOrder ?? "newest first"} />}
        />
      </PageBody>
    </>
  );
}
