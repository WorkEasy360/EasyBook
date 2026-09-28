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
import type { PeriodRowsEnvelope, SalesByCustomerRow } from "@/types/api/reports";

const PATH = "/reports/sales/by-customer";
const ENTRY = reportEntry(PATH);

export const metadata: Metadata = { title: ENTRY.title };

/**
 * Posted (non-draft, non-void) invoices grouped by customer, keyed on invoice
 * date. The endpoint returns per-customer figures only — no grand total — so
 * none is shown.
 */
export default async function SalesByCustomerPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_INVOICES)) {
    return <ReportForbidden title={ENTRY.title} resource="sales by customer" />;
  }

  const fiscalStart = session.organization.fiscal_year_start_month;
  const range = resolveReportRange(params, session.timeZone, fiscalStart);

  const result = await tryServer(() =>
    serverApi.get<PeriodRowsEnvelope<SalesByCustomerRow>>("reports/sales/by-customer", { query: range }),
  );
  const rows = result.ok ? result.data.rows : [];

  const columns: Column<SalesByCustomerRow>[] = [
    { key: "customer_name", header: "Customer", cell: (row) => row.customer_name },
    {
      key: "invoice_count",
      header: "Invoices",
      numeric: true,
      hideBelow: "sm",
      cell: (row) => <span className="tabular">{row.invoice_count}</span>,
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
      key: "total_sales",
      header: "Total sales",
      numeric: true,
      cell: (row) => <Money value={row.total_sales} strong />,
    },
  ];

  return (
    <ReportShell
      title={ENTRY.title}
      description={ENTRY.description}
      csvHref={csvExportHref("sales/by-customer", range)}
      generatedAt={result.ok ? reportTimestamp() : null}
      timeZone={session.timeZone}
    >
      <RangeControls path={PATH} range={range} presets={rangePresets(todayInZone(session.timeZone), fiscalStart)} />
      <PeriodCaption from={range.from_date} to={range.to_date} />
      {isReversedRange(range) ? <ReversedRangeNotice /> : null}
      <DataTable
        caption="Sales by customer"
        columns={columns}
        data={result.ok ? wholeList(rows) : undefined}
        error={result.ok ? null : (result.error as ApiError)}
        getRowId={(row) => row.customer_id}
        getRowHref={(row) => `/sales/customers/${row.customer_id}`}
        emptyTitle="No sales in this period"
        emptyDescription="Posted invoices dated in the period appear here."
        page={1}
        pageSize={Math.max(rows.length, 1)}
        buildPageHref={() => PATH}
        note="Sorted by total sales, highest first. Draft and void invoices are excluded."
      />
    </ReportShell>
  );
}
