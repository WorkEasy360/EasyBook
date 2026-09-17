import type { Metadata } from "next";
import { DataTable } from "@/components/ui/data-table";
import { serverApi, tryServer } from "@/lib/api/server";
import { csvExportHref } from "@/lib/api/capabilities";
import type { ApiError } from "@/lib/api/errors";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { wholeList } from "@/lib/list-query";
import { reportEntry } from "@/features/reports/catalogue";
import { billReportColumns } from "@/features/reports/document-columns";
import { reportTimestamp } from "@/features/reports/period";
import { ReportForbidden, ReportShell } from "@/features/reports/report-shell";
import type { BillReportRow, RowsEnvelope } from "@/types/api/reports";

const PATH = "/reports/payables/outstanding-bills";
const ENTRY = reportEntry(PATH);

export const metadata: Metadata = { title: ENTRY.title };

/** Posted bills with an amount due, now. No date filter and no total on this endpoint. */
export default async function OutstandingBillsPage() {
  const session = await requireSession();

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_BILLS)) {
    return <ReportForbidden title={ENTRY.title} resource="outstanding bills" />;
  }

  const result = await tryServer(() =>
    serverApi.get<RowsEnvelope<BillReportRow>>("reports/payables/outstanding-bills"),
  );

  return (
    <ReportShell
      title={ENTRY.title}
      description="Current position — this report has no date filter."
      csvHref={csvExportHref("payables/outstanding-bills")}
      generatedAt={result.ok ? reportTimestamp() : null}
      timeZone={session.timeZone}
    >
      <DataTable
        caption="Outstanding bills"
        columns={billReportColumns()}
        data={result.ok ? wholeList(result.data.rows) : undefined}
        error={result.ok ? null : (result.error as ApiError)}
        getRowId={(row) => row.bill_id}
        getRowHref={(row) => `/purchases/bills/${row.bill_id}`}
        emptyTitle="No outstanding bills"
        emptyDescription="Every posted bill has been paid in full."
        page={1}
        pageSize={Math.max(result.ok ? result.data.rows.length : 0, 1)}
        buildPageHref={() => PATH}
      />
    </ReportShell>
  );
}
