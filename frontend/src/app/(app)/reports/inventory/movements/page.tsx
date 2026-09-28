import { appHref } from "@/lib/routes";
import type { Metadata } from "next";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { DataTable, type Column } from "@/components/ui/data-table";
import { Money, Quantity } from "@/components/ui/money";
import { serverApi, tryServer } from "@/lib/api/server";
import { csvExportHref } from "@/lib/api/capabilities";
import type { ApiError } from "@/lib/api/errors";
import { recordsById } from "@/lib/api/lookups";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { formatInstantAsDate, todayInZone } from "@/lib/datetime";
import { parseListQuery, type RawSearchParams } from "@/lib/list-query";
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
import { resolveSource } from "@/features/reports/source-routes";
import type { Item } from "@/types/api/items";
import { MOVEMENT_TYPE_LABELS, type MovementType, type StockMovement, type Warehouse } from "@/types/api/inventory";

const PATH = "/reports/inventory/movements";
const ENTRY = reportEntry(PATH);

export const metadata: Metadata = { title: ENTRY.title };

const INBOUND: ReadonlySet<MovementType> = new Set(["opening", "receipt", "adjustment_in", "transfer_in"]);

/**
 * The stock ledger for a period, filtered by item and warehouse — paginated
 * by the API. Note the params differ from /inventory/movements: this report
 * takes `item`/`warehouse` and a date range (reports/api/views.py).
 */
export default async function InventoryMovementsReportPage({
  searchParams,
}: {
  searchParams: Promise<RawSearchParams>;
}) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_INVENTORY)) {
    return <ReportForbidden title={ENTRY.title} resource="stock movements" />;
  }

  const fiscalStart = session.organization.fiscal_year_start_month;
  const range = resolveReportRange(params, session.timeZone, fiscalStart);
  const itemId = uuidParamOf(params, "item");
  const warehouseId = uuidParamOf(params, "warehouse");
  const { page, pageSize, apiParams } = parseListQuery(PATH, "reports/inventory/movements", params);
  const filters = { ...range, item: itemId, warehouse: warehouseId };

  const canViewItems = roleHasPermission(session.role, PERMISSIONS.VIEW_ITEMS);
  const [result, warehouses, itemPage] = await Promise.all([
    tryServer(() =>
      serverApi.list<StockMovement>("reports/inventory/movements", {
        query: { ...filters, page: apiParams.page, page_size: apiParams.page_size },
      }),
    ),
    tryServer(() => serverApi.list<Warehouse>("inventory/warehouses", { query: { page_size: 200 } })),
    canViewItems ? tryServer(() => serverApi.list<Item>("items", { query: { page_size: 200 } })) : Promise.resolve(null),
  ]);

  // Movements carry item ids only. One page of items (which also feeds the
  // item filter) plus a backfill for any id not on it names every row.
  const items = new Map<string, Item>(itemPage?.ok ? itemPage.data.results.map((item) => [item.id, item]) : []);
  const missing = [...(result.ok ? result.data.results.map((row) => row.item) : []), itemId].filter(
    (id): id is string => Boolean(id) && !items.has(id as string),
  );
  if (canViewItems && missing.length > 0) {
    for (const [id, item] of await recordsById<Item>("items", missing)) items.set(id, item);
  }
  const warehouseList = warehouses.ok ? warehouses.data.results : [];
  const warehouseName = new Map(warehouseList.map((row) => [row.id, row.name]));

  const columns: Column<StockMovement>[] = [
    {
      key: "movement_date",
      header: "Date",
      cell: (row) => (
        <span className="tabular whitespace-nowrap">
          {formatInstantAsDate(row.movement_date, { timeZone: session.timeZone })}
        </span>
      ),
    },
    {
      key: "item",
      header: "Item",
      cell: (row) => (
        <Link href={`/items/${row.item}`} className="text-brand-700 hover:underline">
          {items.get(row.item)?.name ?? "Item"}
        </Link>
      ),
    },
    {
      key: "warehouse",
      header: "Warehouse",
      hideBelow: "md",
      cell: (row) => warehouseName.get(row.warehouse) ?? <span className="text-ink-400">Warehouse</span>,
    },
    {
      key: "movement_type",
      header: "Type",
      cell: (row) => (
        <Badge tone={INBOUND.has(row.movement_type) ? "success" : "warning"} size="sm">
          {MOVEMENT_TYPE_LABELS[row.movement_type] ?? row.movement_type}
        </Badge>
      ),
    },
    {
      key: "quantity",
      header: "Quantity",
      numeric: true,
      // The ledger stores a positive quantity and a direction; the sign is
      // presentation of that direction, not arithmetic.
      cell: (row) => (
        <span className={INBOUND.has(row.movement_type) ? "text-success-700" : "text-danger-600"}>
          <span aria-hidden="true">{INBOUND.has(row.movement_type) ? "+" : "−"}</span>
          <span className="sr-only">{INBOUND.has(row.movement_type) ? "in " : "out "}</span>
          <Quantity value={row.quantity} />
        </span>
      ),
    },
    {
      key: "unit_cost",
      header: "Unit cost",
      numeric: true,
      hideBelow: "lg",
      cell: (row) => <Money value={row.unit_cost} />,
    },
    {
      key: "source",
      header: "Source",
      hideBelow: "sm",
      cell: (row) => {
        const source = resolveSource(row.source_type, row.source_id);
        return source.href ? (
          <Link href={appHref(source.href)} className="text-brand-700 hover:underline">
            {source.label}
          </Link>
        ) : (
          <span className="text-ink-600">{source.label}</span>
        );
      },
    },
  ];

  const itemOptions = [...items.values()]
    .sort((a, b) => a.name.localeCompare(b.name))
    .map((item) => ({ value: item.id, label: item.sku ? `${item.name} (${item.sku})` : item.name }));

  return (
    <ReportShell
      title={ENTRY.title}
      description={ENTRY.description}
      // Paginated transaction detail: not wired for CSV — null here.
      csvHref={csvExportHref("inventory/movements", filters)}
      generatedAt={result.ok ? reportTimestamp() : null}
      timeZone={session.timeZone}
    >
      <RangeControls
        path={PATH}
        range={range}
        presets={rangePresets(todayInZone(session.timeZone), fiscalStart)}
        preserve={{ item: itemId, warehouse: warehouseId }}
        // Without item access there is no item select; keep an item filter.
        formPreserve={itemOptions.length > 0 ? {} : { item: itemId }}
      >
        {itemOptions.length > 0 ? (
          <FilterSelect name="item" label="Item" value={itemId} allLabel="All items" options={itemOptions} />
        ) : null}
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
        caption="Stock movements"
        columns={columns}
        data={result.ok ? result.data : undefined}
        error={result.ok ? null : (result.error as ApiError)}
        getRowId={(row) => row.id}
        emptyTitle="No stock movements in this period"
        emptyDescription="Movements are written when receipts, deliveries, bills and adjustments are posted."
        page={page}
        pageSize={pageSize}
        buildPageHref={pagedHrefBuilder(PATH, filters, pageSize)}
        note="Sorted by movement date, oldest first."
      />
    </ReportShell>
  );
}
