import type { Metadata } from "next";
import { DataTable, type Column } from "@/components/ui/data-table";
import { Money, Quantity } from "@/components/ui/money";
import { serverApi, tryServer } from "@/lib/api/server";
import { csvExportHref } from "@/lib/api/capabilities";
import type { ApiError } from "@/lib/api/errors";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { todayInZone } from "@/lib/datetime";
import { wholeList, type RawSearchParams } from "@/lib/list-query";
import { reportEntry } from "@/features/reports/catalogue";
import { isReversedRange, rangePresets, reportTimestamp, resolveReportRange } from "@/features/reports/period";
import {
  PeriodCaption,
  RangeControls,
  ReportForbidden,
  ReportShell,
  ReversedRangeNotice,
} from "@/features/reports/report-shell";
import type { PeriodRowsEnvelope, PurchasesByItemRow } from "@/types/api/reports";

const PATH = "/reports/purchases/by-item";
const ENTRY = reportEntry(PATH);

export const metadata: Metadata = { title: ENTRY.title };

/** Posted bill lines grouped by item, keyed on bill date. No grand total on this endpoint. */
export default async function PurchasesByItemPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_BILLS)) {
    return <ReportForbidden title={ENTRY.title} resource="purchases by item" />;
  }

  const fiscalStart = session.organization.fiscal_year_start_month;
  const range = resolveReportRange(params, session.timeZone, fiscalStart);

  const result = await tryServer(() =>
    serverApi.get<PeriodRowsEnvelope<PurchasesByItemRow>>("reports/purchases/by-item", { query: range }),
  );
  const rows = result.ok ? result.data.rows : [];

  const columns: Column<PurchasesByItemRow>[] = [
    { key: "item_name", header: "Item", cell: (row) => row.item_name },
    {
      key: "item_sku",
      header: "SKU",
      hideBelow: "sm",
      cell: (row) => <span className="tabular text-ink-600">{row.item_sku}</span>,
    },
    { key: "quantity", header: "Quantity", numeric: true, cell: (row) => <Quantity value={row.quantity} /> },
    {
      key: "taxable_value",
      header: "Taxable value",
      numeric: true,
      hideBelow: "md",
      cell: (row) => <Money value={row.taxable_value} />,
    },
    { key: "tax_total", header: "Tax", numeric: true, hideBelow: "md", cell: (row) => <Money value={row.tax_total} /> },
    {
      key: "total_purchases",
      header: "Total purchases",
      numeric: true,
      cell: (row) => <Money value={row.total_purchases} strong />,
    },
  ];

  return (
    <ReportShell
      title={ENTRY.title}
      description={ENTRY.description}
      csvHref={csvExportHref("purchases/by-item", range)}
      generatedAt={result.ok ? reportTimestamp() : null}
      timeZone={session.timeZone}
    >
      <RangeControls path={PATH} range={range} presets={rangePresets(todayInZone(session.timeZone), fiscalStart)} />
      <PeriodCaption from={range.from_date} to={range.to_date} />
      {isReversedRange(range) ? <ReversedRangeNotice /> : null}
      <DataTable
        caption="Purchases by item"
        columns={columns}
        data={result.ok ? wholeList(rows) : undefined}
        error={result.ok ? null : (result.error as ApiError)}
        getRowId={(row) => row.item_id}
        getRowHref={(row) => `/items/${row.item_id}`}
        emptyTitle="No items purchased in this period"
        emptyDescription="Lines on posted bills dated in the period appear here."
        page={1}
        pageSize={Math.max(rows.length, 1)}
        buildPageHref={() => PATH}
        note="Sorted by total purchases, highest first. Draft and void bills are excluded."
      />
    </ReportShell>
  );
}
