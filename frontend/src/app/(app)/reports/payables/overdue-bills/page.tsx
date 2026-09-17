import type { Metadata } from "next";
import { DataTable } from "@/components/ui/data-table";
import { serverApi, tryServer } from "@/lib/api/server";
import { csvExportHref } from "@/lib/api/capabilities";
import type { ApiError } from "@/lib/api/errors";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { todayInZone } from "@/lib/datetime";
import { dateParamOf, wholeList, type RawSearchParams } from "@/lib/list-query";
import { reportEntry } from "@/features/reports/catalogue";
import { billReportColumns } from "@/features/reports/document-columns";
import { asOfPresets, reportTimestamp } from "@/features/reports/period";
import {
  AsOfControls,
  PeriodCaption,
  ReportForbidden,
  ReportShell,
} from "@/features/reports/report-shell";
import type { BillReportRow, RowsEnvelope } from "@/types/api/reports";

const PATH = "/reports/payables/overdue-bills";
const ENTRY = reportEntry(PATH);

export const metadata: Metadata = { title: ENTRY.title };

/** Unpaid posted bills whose due date is before the as-of date. */
export default async function OverdueBillsPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_BILLS)) {
    return <ReportForbidden title={ENTRY.title} resource="overdue bills" />;
  }

  const today = todayInZone(session.timeZone);
  // Sent explicitly so "overdue" is judged in the organization's timezone.
  const asOf = dateParamOf(params, "as_of_date") ?? today;
  const query = { as_of_date: asOf };

  const result = await tryServer(() =>
    serverApi.get<RowsEnvelope<BillReportRow>>("reports/payables/overdue-bills", { query }),
  );

  return (
    <ReportShell
      title={ENTRY.title}
      description={ENTRY.description}
      csvHref={csvExportHref("payables/overdue-bills", query)}
      generatedAt={result.ok ? reportTimestamp() : null}
      timeZone={session.timeZone}
    >
      <AsOfControls
        path={PATH}
        asOf={asOf}
        presets={asOfPresets(today, session.organization.fiscal_year_start_month)}
        required
      />
      <PeriodCaption asOf={asOf} />
      <DataTable
        caption="Overdue bills"
        columns={billReportColumns()}
        data={result.ok ? wholeList(result.data.rows) : undefined}
        error={result.ok ? null : (result.error as ApiError)}
        getRowId={(row) => row.bill_id}
        getRowHref={(row) => `/purchases/bills/${row.bill_id}`}
        emptyTitle="Nothing overdue"
        emptyDescription="No unpaid bill was past its due date on this date."
        page={1}
        pageSize={Math.max(result.ok ? result.data.rows.length : 0, 1)}
        buildPageHref={() => PATH}
      />
    </ReportShell>
  );
}
