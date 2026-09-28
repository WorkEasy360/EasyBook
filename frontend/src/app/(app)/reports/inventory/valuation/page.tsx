import type { Metadata } from "next";
import { DataTable, TableFooterRow, type Column } from "@/components/ui/data-table";
import { Money, Quantity } from "@/components/ui/money";
import { serverApi, tryServer } from "@/lib/api/server";
import { csvExportHref } from "@/lib/api/capabilities";
import type { ApiError } from "@/lib/api/errors";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { todayInZone } from "@/lib/datetime";
import { dateParamOf, wholeList, type RawSearchParams } from "@/lib/list-query";
import { reportEntry } from "@/features/reports/catalogue";
import { asOfPresets, reportTimestamp, uuidParamOf } from "@/features/reports/period";
import {
  AsOfControls,
  FilterSelect,
  PeriodCaption,
  ReportForbidden,
  ReportShell,
} from "@/features/reports/report-shell";
import type { InventoryPositionRow, InventoryValuation, Warehouse } from "@/types/api/inventory";

const PATH = "/reports/inventory/valuation";
const ENTRY = reportEntry(PATH);

export const metadata: Metadata = { title: ENTRY.title };

/**
 * Stock value per item and warehouse at moving average cost, optionally as of
 * a past date. The total is the report's own `total_value` — never a sum of
 * the rows on screen.
 */
export default async function InventoryValuationPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_INVENTORY)) {
    return <ReportForbidden title={ENTRY.title} resource="inventory valuation" />;
  }

  // No as-of date means "now": the endpoint values every movement to date.
  const asOf = dateParamOf(params, "as_of_date");
  const warehouseId = uuidParamOf(params, "warehouse");
  const query = { as_of_date: asOf, warehouse: warehouseId };

  const [result, warehouses] = await Promise.all([
    tryServer(() => serverApi.get<InventoryValuation>("reports/inventory/valuation", { query })),
    tryServer(() => serverApi.list<Warehouse>("inventory/warehouses", { query: { page_size: 200 } })),
  ]);
  const rows = result.ok ? result.data.rows : [];
  const warehouseList = warehouses.ok ? warehouses.data.results : [];

  const columns: Column<InventoryPositionRow>[] = [
    { key: "item_name", header: "Item", cell: (row) => row.item_name },
    {
      key: "item_sku",
      header: "SKU",
      hideBelow: "sm",
      cell: (row) => <span className="tabular text-ink-600">{row.item_sku}</span>,
    },
    { key: "warehouse_name", header: "Warehouse", hideBelow: "md", cell: (row) => row.warehouse_name },
    {
      key: "quantity_on_hand",
      header: "On hand",
      numeric: true,
      cell: (row) => <Quantity value={row.quantity_on_hand} />,
    },
    {
      key: "average_cost",
      header: "Avg. cost",
      numeric: true,
      hideBelow: "lg",
      cell: (row) => <Money value={row.average_cost} />,
    },
    {
      key: "inventory_value",
      header: "Value",
      numeric: true,
      cell: (row) => <Money value={row.inventory_value} />,
    },
  ];

  return (
    <ReportShell
      title={ENTRY.title}
      description={ENTRY.description}
      // Not wired for CSV on the backend (the stock summary is) — null here.
      csvHref={csvExportHref("inventory/valuation", query)}
      generatedAt={result.ok ? reportTimestamp() : null}
      timeZone={session.timeZone}
    >
      <AsOfControls
        path={PATH}
        asOf={asOf}
        presets={asOfPresets(todayInZone(session.timeZone), session.organization.fiscal_year_start_month)}
        preserve={{ warehouse: warehouseId }}
      >
        {warehouseList.length > 1 ? (
          <FilterSelect
            name="warehouse"
            label="Warehouse"
            value={warehouseId}
            allLabel="All warehouses"
            options={warehouseList.map((row) => ({ value: row.id, label: row.name }))}
          />
        ) : null}
      </AsOfControls>
      <PeriodCaption asOf={asOf} emptyText="Current position" />

      <DataTable
        caption="Inventory valuation"
        columns={columns}
        data={result.ok ? wholeList(rows) : undefined}
        error={result.ok ? null : (result.error as ApiError)}
        getRowId={(row) => `${row.item_id}-${row.warehouse_id}`}
        getRowHref={(row) => `/items/${row.item_id}`}
        emptyTitle="No stock to value"
        emptyDescription="Stock appears once a receipt, bill or stock adjustment has been posted."
        page={1}
        pageSize={Math.max(rows.length, 1)}
        buildPageHref={() => PATH}
        footer={
          result.ok && rows.length > 0 ? (
            <TableFooterRow
              label="Total value"
              columnCount={columns.length}
              values={[{ key: "value", node: <Money value={result.data.total_value} strong /> }]}
            />
          ) : undefined
        }
        note="Valued at moving average cost. Sorted by item, then warehouse."
      />
    </ReportShell>
  );
}
