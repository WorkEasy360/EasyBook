import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { DataTable, type Column } from "@/components/ui/data-table";
import { FilterBar, SortNote } from "@/components/ui/filter-bar";
import { LinkButton } from "@/components/ui/link-button";
import { StatusBadge, STOCK_ADJUSTMENT_STATUS, statusOptions } from "@/components/ui/status-badge";
import { ForbiddenState } from "@/components/ui/states";
import { serverApi, tryServer } from "@/lib/api/server";
import { parseListQuery, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { formatDate } from "@/lib/datetime";
import type { ApiError } from "@/lib/api/errors";
import { ADJUSTMENT_REASON_LABELS, type StockAdjustment, type Warehouse } from "@/types/api/inventory";

export const metadata: Metadata = { title: "Stock adjustments" };

export default async function AdjustmentsPage({
  searchParams,
}: {
  searchParams: Promise<RawSearchParams>;
}) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_INVENTORY)) {
    return (
      <>
        <PageHeader title="Stock adjustments" />
        <ForbiddenState resource="stock adjustments" />
      </>
    );
  }

  const canAdjust = roleHasPermission(session.role, PERMISSIONS.ADJUST_INVENTORY);
  const query = parseListQuery("/inventory/adjustments", "inventory/adjustments", params);

  const [adjustments, warehouses] = await Promise.all([
    tryServer(() => serverApi.list<StockAdjustment>("inventory/adjustments", { query: query.apiParams })),
    tryServer(() => serverApi.list<Warehouse>("inventory/warehouses", { query: { page_size: 200 } })),
  ]);

  const warehouseName = new Map(
    (warehouses.ok ? warehouses.data.results : []).map((row) => [row.id, row.name]),
  );

  const columns: Column<StockAdjustment>[] = [
    {
      key: "adjustment_date",
      header: "Date",
      cell: (row) => <span className="tabular whitespace-nowrap">{formatDate(row.adjustment_date)}</span>,
    },
    {
      key: "reason",
      header: "Reason",
      cell: (row) => ADJUSTMENT_REASON_LABELS[row.reason] ?? row.reason,
    },
    {
      key: "warehouse",
      header: "Warehouse",
      hideBelow: "sm",
      cell: (row) => warehouseName.get(row.warehouse) ?? "—",
    },
    {
      key: "lines",
      header: "Lines",
      numeric: true,
      hideBelow: "md",
      cell: (row) => row.lines.length,
    },
    {
      key: "memo",
      header: "Memo",
      hideBelow: "lg",
      cell: (row) =>
        row.memo ? (
          <span className="line-clamp-1 max-w-xs text-ink-600">{row.memo}</span>
        ) : (
          <span className="text-ink-400">—</span>
        ),
    },
    {
      key: "status",
      header: "Status",
      cell: (row) => <StatusBadge status={row.status} map={STOCK_ADJUSTMENT_STATUS} size="sm" />,
    },
  ];

  return (
    <>
      <PageHeader
        title="Stock adjustments"
        description="Corrections to stock on hand. A draft changes nothing until it is posted."
        actions={
          canAdjust ? (
            <LinkButton href="/inventory/adjustments/new" variant="primary">
              New adjustment
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
              options: statusOptions(STOCK_ADJUSTMENT_STATUS),
            },
          ]}
          buildFilterHref={query.buildFilterHref}
          clearHref={query.clearHref}
          activeFilterCount={query.activeFilterCount}
        />
        <DataTable
          caption="Stock adjustments"
          columns={columns}
          data={adjustments.ok ? adjustments.data : undefined}
          error={adjustments.ok ? null : (adjustments.error as ApiError)}
          getRowId={(row) => row.id}
          getRowHref={(row) => `/inventory/adjustments/${row.id}`}
          emptyTitle={query.activeFilterCount > 0 ? "No adjustments match this filter" : "No stock adjustments yet"}
          emptyDescription="Record a physical count, damage or shrinkage as an adjustment."
          {...(canAdjust
            ? { emptyAction: { label: "New adjustment", href: "/inventory/adjustments/new" } }
            : {})}
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={query.buildPageHref}
          note={<SortNote description="adjustment date, newest first" />}
        />
      </PageBody>
    </>
  );
}
