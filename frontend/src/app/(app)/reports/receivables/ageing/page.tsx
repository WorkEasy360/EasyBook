import type { Metadata } from "next";
import { serverApi, tryServer } from "@/lib/api/server";
import { csvExportHref } from "@/lib/api/capabilities";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { todayInZone } from "@/lib/datetime";
import { dateParamOf, type RawSearchParams } from "@/lib/list-query";
import { AgeingView } from "@/features/reports/ageing-view";
import { reportEntry } from "@/features/reports/catalogue";
import { invoiceReportColumns } from "@/features/reports/document-columns";
import { asOfPresets, reportTimestamp } from "@/features/reports/period";
import {
  AsOfControls,
  PeriodCaption,
  ReportError,
  ReportForbidden,
  ReportShell,
} from "@/features/reports/report-shell";
import type { InvoiceAgeingRow, ReceivablesAgeing } from "@/types/api/reports";

const PATH = "/reports/receivables/ageing";
const ENTRY = reportEntry(PATH);

export const metadata: Metadata = { title: ENTRY.title };

/**
 * Unpaid posted invoices bucketed by days past their due date, as of a date.
 * Bucket boundaries, days overdue and every total are the engine's
 * (reports/selectors/receivables.py).
 */
export default async function ReceivablesAgeingPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  // The view requires VIEW_INVOICES, not VIEW_REPORTS.
  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_INVOICES)) {
    return <ReportForbidden title={ENTRY.title} resource="receivables ageing" />;
  }

  const today = todayInZone(session.timeZone);
  const asOf = dateParamOf(params, "as_of_date") ?? today;

  const result = await tryServer(() =>
    serverApi.get<ReceivablesAgeing>("reports/receivables/ageing", { query: { as_of_date: asOf } }),
  );

  return (
    <ReportShell
      title={ENTRY.title}
      description={ENTRY.description}
      // The ageing payload is bucketed, not flat — no CSV on the backend.
      csvHref={csvExportHref("receivables/ageing", { as_of_date: asOf })}
      generatedAt={result.ok ? reportTimestamp() : null}
      timeZone={session.timeZone}
    >
      <AsOfControls
        path={PATH}
        asOf={asOf}
        presets={asOfPresets(today, session.organization.fiscal_year_start_month)}
        required
      />
      <PeriodCaption asOf={result.ok ? result.data.as_of : asOf} />

      {!result.ok ? (
        <ReportError error={result.error} />
      ) : (
        <AgeingView
          report={result.data}
          columns={invoiceReportColumns<InvoiceAgeingRow>({ daysOverdue: (row) => row.days_overdue })}
          getRowId={(row) => row.invoice_id}
          getRowHref={(row) => `/sales/invoices/${row.invoice_id}`}
          documentNoun="invoice"
          emptyDescription="Every posted invoice has been paid in full."
          pathname={PATH}
        />
      )}
    </ReportShell>
  );
}
