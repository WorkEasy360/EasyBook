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
import {
  hrefWith,
  isReversedRange,
  rangePresets,
  reportTimestamp,
  resolveReportRange,
} from "@/features/reports/period";
import {
  PeriodCaption,
  RangeControls,
  ReportForbidden,
  ReportShell,
  ReversedRangeNotice,
} from "@/features/reports/report-shell";
import type { ExpenseCategoryRow, PeriodRowsEnvelope } from "@/types/api/reports";

const PATH = "/reports/expenses/by-category";
const ENTRY = reportEntry(PATH);

export const metadata: Metadata = { title: ENTRY.title };

/**
 * Posted expenses grouped by expense account — the "category" IS the expense
 * account; the backend has no separate category field. Bills are not
 * included. Each row drills into that account's ledger for the same period.
 */
export default async function ExpensesByCategoryPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_EXPENSES)) {
    return <ReportForbidden title={ENTRY.title} resource="expenses by category" />;
  }

  const fiscalStart = session.organization.fiscal_year_start_month;
  const range = resolveReportRange(params, session.timeZone, fiscalStart);

  const result = await tryServer(() =>
    serverApi.get<PeriodRowsEnvelope<ExpenseCategoryRow>>("reports/expenses/by-category", { query: range }),
  );
  const rows = result.ok ? result.data.rows : [];

  const columns: Column<ExpenseCategoryRow>[] = [
    {
      key: "account_name",
      header: "Expense account",
      cell: (row) => (
        <>
          <span className="tabular mr-2 text-ink-500">{row.account_code}</span>
          {row.account_name}
        </>
      ),
    },
    {
      key: "expense_count",
      header: "Expenses",
      numeric: true,
      hideBelow: "sm",
      cell: (row) => <span className="tabular">{row.expense_count}</span>,
    },
    { key: "tax_total", header: "Tax", numeric: true, hideBelow: "md", cell: (row) => <Money value={row.tax_total} /> },
    {
      key: "total_amount",
      header: "Total",
      numeric: true,
      cell: (row) => <Money value={row.total_amount} strong />,
    },
  ];

  return (
    <ReportShell
      title={ENTRY.title}
      description={ENTRY.description}
      csvHref={csvExportHref("expenses/by-category", range)}
      generatedAt={result.ok ? reportTimestamp() : null}
      timeZone={session.timeZone}
    >
      <RangeControls path={PATH} range={range} presets={rangePresets(todayInZone(session.timeZone), fiscalStart)} />
      <PeriodCaption from={range.from_date} to={range.to_date} />
      {isReversedRange(range) ? <ReversedRangeNotice /> : null}
      <DataTable
        caption="Expenses by category"
        columns={columns}
        data={result.ok ? wholeList(rows) : undefined}
        error={result.ok ? null : (result.error as ApiError)}
        getRowId={(row) => row.account_id}
        getRowHref={(row) =>
          hrefWith("/accounting/general-ledger", {
            account: row.account_id,
            from_date: range.from_date,
            to_date: range.to_date,
          })
        }
        emptyTitle="No expenses in this period"
        emptyDescription="Posted expenses dated in the period appear here."
        page={1}
        pageSize={Math.max(rows.length, 1)}
        buildPageHref={() => PATH}
        note="Sorted by total, highest first. Draft and void expenses are excluded; bills are not expenses here."
      />
    </ReportShell>
  );
}
