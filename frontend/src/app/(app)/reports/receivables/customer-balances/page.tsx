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
import type { CustomerBalanceRow, RowsEnvelope } from "@/types/api/reports";

const PATH = "/reports/receivables/customer-balances";
const ENTRY = reportEntry(PATH);

export const metadata: Metadata = { title: ENTRY.title };

/**
 * What each customer owes now: the sum of amount due on their unpaid posted
 * invoices, computed by the engine. The endpoint takes no date and returns no
 * grand total, so this page shows neither.
 */
export default async function CustomerBalancesPage() {
  const session = await requireSession();

  // The view requires VIEW_CUSTOMERS.
  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_CUSTOMERS)) {
    return <ReportForbidden title={ENTRY.title} resource="customer balances" />;
  }

  const result = await tryServer(() =>
    serverApi.get<RowsEnvelope<CustomerBalanceRow>>("reports/receivables/customer-balances"),
  );

  const columns: Column<CustomerBalanceRow>[] = [
    { key: "customer_name", header: "Customer", cell: (row) => row.customer_name },
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
      csvHref={csvExportHref("receivables/customer-balances")}
      generatedAt={result.ok ? reportTimestamp() : null}
      timeZone={session.timeZone}
    >
      <DataTable
        caption="Customer balances"
        columns={columns}
        data={result.ok ? wholeList(result.data.rows) : undefined}
        error={result.ok ? null : (result.error as ApiError)}
        getRowId={(row) => row.customer_id}
        getRowHref={(row) => `/sales/customers/${row.customer_id}`}
        emptyTitle="No customer owes anything"
        emptyDescription="Balances appear once a posted invoice has an amount due."
        page={1}
        pageSize={Math.max(result.ok ? result.data.rows.length : 0, 1)}
        buildPageHref={() => PATH}
        note={
          <>
            Sorted by customer name. For how long balances have been outstanding, see{" "}
            <Link href="/reports/receivables/ageing" className="font-medium text-brand-700 hover:underline">
              receivables ageing
            </Link>
            .
          </>
        }
      />
    </ReportShell>
  );
}
