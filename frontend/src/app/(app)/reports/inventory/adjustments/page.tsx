import type { Metadata } from "next";
import { DataTable, type Column } from "@/components/ui/data-table";
import { STOCK_ADJUSTMENT_STATUS, StatusBadge, statusOptions } from "@/components/ui/status-badge";
import { serverApi, tryServer } from "@/lib/api/server";
import { csvExportHref } from "@/lib/api/capabilities";
import type { ApiError } from "@/lib/api/errors";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { formatDate, todayInZone } from "@/lib/datetime";
import { paramOf, parseListQuery, type RawSearchParams } from "@/lib/list-query";
import { reportEntry } from "@/features/reports/catalogue";
import {
  isReversedRange,
  pagedHrefBuilder,
  rangePresets,
  reportTimestamp,
  resolveReportRange,
  uuidParamOf,
} from "@/features/reports/period";
import {
  FilterSelect,
  PeriodCaption,
  RangeControls,
  ReportForbidden,
  ReportShell,
  ReversedRangeNotice,
} from "@/features/reports/report-shell";
import {
  ADJUSTMENT_REASON_LABELS,
  type AdjustmentReason,
  type StockAdjustment,
  type Warehouse,
} from "@/types/api/inventory";

const PATH = "/reports/inventory/adjustments";
const ENTRY = reportEntry(PATH);

export const metadata: Metadata = { title: ENTRY.title };

/**
 * Stock adjustments over a period (by adjustment date), filterable by status
 * and warehouse — paginated by the API. Each row opens the adjustment, whose
 * page shows the lines and the journal it posted.
 */
export default async function StockAdjustmentsReportPage({
  searchParams,
}: {
  searchParams: Promise<RawSearchParams>;
}) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_INVENTORY)) {
    return <ReportForbidden title={ENTRY.title} resource="stock adjustments" />;
  }

  const fiscalStart = session.organization.fiscal_year_start_month;
  const range = resolveReportRange(params, session.timeZone, fiscalStart);
  const warehouseId = uuidParamOf(params, "warehouse");
  const rawStatus = paramOf(params, "status");
  // Only a status the backend defines is forwarded.
  const status = rawStatus && rawStatus in STOCK_ADJUSTMENT_STATUS ? rawStatus : undefined;
  const { page, pageSize, apiParams } = parseListQuery(PATH, "reports/inventory/adjustments", params);
  const filters = { ...range, status, warehouse: warehouseId };

  const [result, warehouses] = await Promise.all([
    tryServer(() =>
      serverApi.list<StockAdjustment>("reports/inventory/adjustments", {
        query: { ...filters, page: apiParams.page, page_size: apiParams.page_size },
      }),
    ),
    tryServer(() => serverApi.list<Warehouse>("inventory/warehouses", { query: { page_size: 200 } })),
  ]);
  const warehouseList = warehouses.ok ? warehouses.data.results : [];
  const warehouseName = new Map(warehouseList.map((row) => [row.id, row.name]));

  const columns: Column<StockAdjustment>[] = [
    {
      key: "adjustment_date",
      header: "Date",
      cell: (row) => <span className="tabular whitespace-nowrap">{formatDate(row.adjustment_date)}</span>,
    },
    {
      key: "warehouse",
      header: "Warehouse",
      cell: (row) => warehouseName.get(row.warehouse) ?? <span className="text-ink-400">Warehouse</span>,
    },
    {
      key: "reason",
      header: "Reason",
      cell: (row) => ADJUSTMENT_REASON_LABELS[row.reason as AdjustmentReason] ?? row.reason,
    },
    {
      key: "lines",
      header: "Lines",
      numeric: true,
      hideBelow: "sm",
      cell: (row) => <span className="tabular">{row.lines.length}</span>,
    },
    {
      key: "memo",
      header: "Memo",
      hideBelow: "md",
      cell: (row) => <span className="text-ink-600">{row.memo || "—"}</span>,
    },
    {
      key: "status",
      header: "Status",
      cell: (row) => <StatusBadge status={row.status} map={STOCK_ADJUSTMENT_STATUS} size="sm" />,
    },
  ];

  return (
    <ReportShell
      title={ENTRY.title}
      description={ENTRY.description}
      // Paginated transaction detail: not wired for CSV — null here.
      csvHref={csvExportHref("inventory/adjustments", filters)}
      generatedAt={result.ok ? reportTimestamp() : null}
      timeZone={session.timeZone}
    >
      <RangeControls
        path={PATH}
        range={range}
        presets={rangePresets(todayInZone(session.timeZone), fiscalStart)}
        preserve={{ status, warehouse: warehouseId }}
      >
        <FilterSelect
          name="status"
          label="Status"
          value={status}
          allLabel="All statuses"
          options={statusOptions(STOCK_ADJUSTMENT_STATUS)}
        />
        <FilterSelect
          name="warehouse"
          label="Warehouse"
          value={warehouseId}
          allLabel="All warehouses"
          options={warehouseList.map((row) => ({ value: row.id, label: row.name }))}
        />
      </RangeControls>
      <PeriodCaption from={range.from_date} to={range.to_date} />
      {isReversedRange(range) ? <ReversedRangeNotice /> : null}

      <DataTable
        caption="Stock adjustments"
        columns={columns}
        data={result.ok ? result.data : undefined}
        error={result.ok ? null : (result.error as ApiError)}
        getRowId={(row) => row.id}
        getRowHref={(row) => `/inventory/adjustments/${row.id}`}
        emptyTitle="No stock adjustments in this period"
        emptyDescription="Adjustments dated in the period appear here, whatever their status."
        page={page}
        pageSize={pageSize}
        buildPageHref={pagedHrefBuilder(PATH, filters, pageSize)}
        note="Sorted by adjustment date, oldest first."
      />
    </ReportShell>
  );
}
