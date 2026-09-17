import type { Metadata } from "next";
import { DataTable } from "@/components/ui/data-table";
import { serverApi, tryServer } from "@/lib/api/server";
import { csvExportHref } from "@/lib/api/capabilities";
import type { ApiError } from "@/lib/api/errors";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { wholeList } from "@/lib/list-query";
import { reportEntry } from "@/features/reports/catalogue";
import { invoiceReportColumns } from "@/features/reports/document-columns";
import { reportTimestamp } from "@/features/reports/period";
import { ReportForbidden, ReportShell } from "@/features/reports/report-shell";
import type { InvoiceReportRow, RowsEnvelope } from "@/types/api/reports";

const PATH = "/reports/receivables/outstanding-invoices";
const ENTRY = reportEntry(PATH);

export const metadata: Metadata = { title: ENTRY.title };

/** Posted invoices with an amount due, now. No date filter and no total on this endpoint. */
export default async function OutstandingInvoicesPage() {
  const session = await requireSession();

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_INVOICES)) {
    return <ReportForbidden title={ENTRY.title} resource="outstanding invoices" />;
  }

  const result = await tryServer(() =>
    serverApi.get<RowsEnvelope<InvoiceReportRow>>("reports/receivables/outstanding-invoices"),
  );

  return (
    <ReportShell
      title={ENTRY.title}
      description="Current position — this report has no date filter."
      csvHref={csvExportHref("receivables/outstanding-invoices")}
      generatedAt={result.ok ? reportTimestamp() : null}
      timeZone={session.timeZone}
    >
      <DataTable
        caption="Outstanding invoices"
        columns={invoiceReportColumns()}
        data={result.ok ? wholeList(result.data.rows) : undefined}
        error={result.ok ? null : (result.error as ApiError)}
        getRowId={(row) => row.invoice_id}
        getRowHref={(row) => `/sales/invoices/${row.invoice_id}`}
        emptyTitle="No outstanding invoices"
        emptyDescription="Every posted invoice has been paid in full."
        page={1}
        pageSize={Math.max(result.ok ? result.data.rows.length : 0, 1)}
        buildPageHref={() => PATH}
      />
    </ReportShell>
  );
}
