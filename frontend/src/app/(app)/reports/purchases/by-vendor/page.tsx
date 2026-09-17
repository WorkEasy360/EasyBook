import type { Metadata } from "next";
import { DataTable, type Column } from "@/components/ui/data-table";
import { Money } from "@/components/ui/money";
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
import type { PeriodRowsEnvelope, PurchasesByVendorRow } from "@/types/api/reports";

const PATH = "/reports/purchases/by-vendor";
const ENTRY = reportEntry(PATH);

export const metadata: Metadata = { title: ENTRY.title };

/** Posted bills grouped by vendor, keyed on bill date. Expenses are not bills and are not included. */
export default async function PurchasesByVendorPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_BILLS)) {
    return <ReportForbidden title={ENTRY.title} resource="purchases by vendor" />;
  }

  const fiscalStart = session.organization.fiscal_year_start_month;
  const range = resolveReportRange(params, session.timeZone, fiscalStart);

  const result = await tryServer(() =>
    serverApi.get<PeriodRowsEnvelope<PurchasesByVendorRow>>("reports/purchases/by-vendor", { query: range }),
  );
  const rows = result.ok ? result.data.rows : [];

  const columns: Column<PurchasesByVendorRow>[] = [
    { key: "vendor_name", header: "Vendor", cell: (row) => row.vendor_name },
    {
      key: "bill_count",
      header: "Bills",
      numeric: true,
      hideBelow: "sm",
      cell: (row) => <span className="tabular">{row.bill_count}</span>,
    },
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
      csvHref={csvExportHref("purchases/by-vendor", range)}
      generatedAt={result.ok ? reportTimestamp() : null}
      timeZone={session.timeZone}
    >
      <RangeControls path={PATH} range={range} presets={rangePresets(todayInZone(session.timeZone), fiscalStart)} />
      <PeriodCaption from={range.from_date} to={range.to_date} />
      {isReversedRange(range) ? <ReversedRangeNotice /> : null}
      <DataTable
        caption="Purchases by vendor"
        columns={columns}
        data={result.ok ? wholeList(rows) : undefined}
        error={result.ok ? null : (result.error as ApiError)}
        getRowId={(row) => row.vendor_id}
        getRowHref={(row) => `/purchases/vendors/${row.vendor_id}`}
        emptyTitle="No purchases in this period"
        emptyDescription="Posted bills dated in the period appear here."
        page={1}
        pageSize={Math.max(rows.length, 1)}
        buildPageHref={() => PATH}
        note="Sorted by total purchases, highest first. Draft and void bills are excluded."
      />
    </ReportShell>
  );
}
