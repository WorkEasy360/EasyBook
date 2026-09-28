import type { Metadata } from "next";
import Link from "next/link";
import { DataTable, type Column } from "@/components/ui/data-table";
import { Money } from "@/components/ui/money";
import { serverApi, tryServer } from "@/lib/api/server";
import { csvExportHref } from "@/lib/api/capabilities";
import type { ApiError } from "@/lib/api/errors";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { wholeList } from "@/lib/list-query";
import { reportEntry } from "@/features/reports/catalogue";
import { reportTimestamp } from "@/features/reports/period";
import { ReportForbidden, ReportShell } from "@/features/reports/report-shell";
import type { RowsEnvelope, VendorBalanceRow } from "@/types/api/reports";

const PATH = "/reports/payables/vendor-balances";
const ENTRY = reportEntry(PATH);

export const metadata: Metadata = { title: ENTRY.title };

/**
 * What is owed to each vendor now: amount due on unpaid posted bills, computed
 * by the engine. No date parameter and no grand total exist on this endpoint.
 */
export default async function VendorBalancesPage() {
  const session = await requireSession();

  // The view requires VIEW_VENDORS.
  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_VENDORS)) {
    return <ReportForbidden title={ENTRY.title} resource="vendor balances" />;
  }

  const result = await tryServer(() =>
    serverApi.get<RowsEnvelope<VendorBalanceRow>>("reports/payables/vendor-balances"),
  );

  const columns: Column<VendorBalanceRow>[] = [
    { key: "vendor_name", header: "Vendor", cell: (row) => row.vendor_name },
    {
      key: "balance",
      header: "Balance due",
      numeric: true,
      cell: (row) => <Money value={row.balance} strong />,
    },
  ];

  return (
    <ReportShell
      title={ENTRY.title}
      description="Current balances — this report has no date filter."
      csvHref={csvExportHref("payables/vendor-balances")}
      generatedAt={result.ok ? reportTimestamp() : null}
      timeZone={session.timeZone}
    >
      <DataTable
        caption="Vendor balances"
        columns={columns}
        data={result.ok ? wholeList(result.data.rows) : undefined}
        error={result.ok ? null : (result.error as ApiError)}
        getRowId={(row) => row.vendor_id}
        getRowHref={(row) => `/purchases/vendors/${row.vendor_id}`}
        emptyTitle="Nothing is owed to vendors"
        emptyDescription="Balances appear once a posted bill has an amount due."
        page={1}
        pageSize={Math.max(result.ok ? result.data.rows.length : 0, 1)}
        buildPageHref={() => PATH}
        note={
          <>
            Sorted by vendor name. For how long balances have been outstanding, see{" "}
            <Link href="/reports/payables/ageing" className="font-medium text-brand-700 hover:underline">
              payables ageing
            </Link>
            .
          </>
        }
      />
    </ReportShell>
  );
}
