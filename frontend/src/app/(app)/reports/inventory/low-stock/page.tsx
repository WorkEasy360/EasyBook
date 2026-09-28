import type { Metadata } from "next";
import { DataTable, type Column } from "@/components/ui/data-table";
import { FilterBar } from "@/components/ui/filter-bar";
import { Quantity } from "@/components/ui/money";
import { serverApi, tryServer } from "@/lib/api/server";
import { csvExportHref } from "@/lib/api/capabilities";
import type { ApiError } from "@/lib/api/errors";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { wholeList, type RawSearchParams } from "@/lib/list-query";
import { reportEntry } from "@/features/reports/catalogue";
import { hrefWith, reportTimestamp, uuidParamOf } from "@/features/reports/period";
import { ReportForbidden, ReportShell } from "@/features/reports/report-shell";
import type { LowStockItemRow, Warehouse } from "@/types/api/inventory";
import type { RowsEnvelope } from "@/types/api/reports";

const PATH = "/reports/inventory/low-stock";
const ENTRY = reportEntry(PATH);

export const metadata: Metadata = { title: ENTRY.title };

/**
 * Items at or below their reorder level, now. The endpoint has no date — only
 * an optional warehouse — so no period control is offered.
 */
export default async function LowStockPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_INVENTORY)) {
    return <ReportForbidden title={ENTRY.title} resource="the low stock report" />;
  }

  const warehouseId = uuidParamOf(params, "warehouse");
  const query = { warehouse: warehouseId };

  const [result, warehouses] = await Promise.all([
    tryServer(() => serverApi.get<RowsEnvelope<LowStockItemRow>>("reports/inventory/low-stock", { query })),
    tryServer(() => serverApi.list<Warehouse>("inventory/warehouses", { query: { page_size: 200 } })),
  ]);
  const rows = result.ok ? result.data.rows : [];
  const warehouseList = warehouses.ok ? warehouses.data.results : [];
  const selectedWarehouse = warehouseList.find((row) => row.id === warehouseId);

  const columns: Column<LowStockItemRow>[] = [
    { key: "item_name", header: "Item", cell: (row) => row.item_name },
    {
      key: "item_sku",
      header: "SKU",
      hideBelow: "sm",
      cell: (row) => <span className="tabular text-ink-600">{row.item_sku}</span>,
    },
    {
      key: "on_hand",
      header: "On hand",
      numeric: true,
      cell: (row) => <Quantity value={row.on_hand} className="text-danger-600" />,
    },
    {
      key: "reorder_level",
      header: "Reorder level",
      numeric: true,
      cell: (row) => <Quantity value={row.reorder_level} />,
    },
  ];

  return (
    <ReportShell
      title={ENTRY.title}
      description={
        selectedWarehouse
          ? `Current stock in ${selectedWarehouse.name} — this report has no date filter.`
          : "Current stock across all warehouses — this report has no date filter."
      }
      csvHref={csvExportHref("inventory/low-stock", query)}
      generatedAt={result.ok ? reportTimestamp() : null}
      timeZone={session.timeZone}
    >
      {warehouseList.length > 1 ? (
        <FilterBar
          groups={[
            {
              key: "warehouse",
              label: "Warehouse",
              value: warehouseId ?? "",
              allLabel: "All warehouses",
              options: warehouseList.map((row) => ({ value: row.id, label: row.name })),
            },
          ]}
          buildFilterHref={(key, value) => hrefWith(PATH, { [key]: value ?? undefined })}
          clearHref={PATH}
          activeFilterCount={warehouseId ? 1 : 0}
        />
      ) : null}

      <DataTable
        caption="Items at or below reorder level"
        columns={columns}
        data={result.ok ? wholeList(rows) : undefined}
        error={result.ok ? null : (result.error as ApiError)}
        getRowId={(row) => row.item_id}
        getRowHref={(row) => `/items/${row.item_id}`}
        emptyTitle="Nothing below reorder level"
        emptyDescription="Set a reorder level on an item to be warned here."
        page={1}
        pageSize={Math.max(rows.length, 1)}
        buildPageHref={() => PATH}
      />
    </ReportShell>
  );
}
