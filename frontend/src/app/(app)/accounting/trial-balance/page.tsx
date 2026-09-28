import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { Badge } from "@/components/ui/badge";
import { DataTable, TableFooterRow, type Column } from "@/components/ui/data-table";
import { DateFilterForm } from "@/components/ui/date-filter-form";
import { Money } from "@/components/ui/money";
import { PrintButton } from "@/components/ui/print-button";
import { StatCard, StatGrid } from "@/components/ui/stat-card";
import { ACCOUNT_TYPE_LABELS } from "@/components/ui/status-badge";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { serverApi, tryServer } from "@/lib/api/server";
import { dateParamOf, wholeList, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { referenceOf } from "@/lib/api/errors";
import { formatDate, todayInZone } from "@/lib/datetime";
import type { TrialBalance, TrialBalanceRow } from "@/types/api/accounting";

export const metadata: Metadata = { title: "Trial balance" };

const PATH = "/accounting/trial-balance";

/**
 * Trial balance — reports/trial-balance/ (accounting/selectors.py ::
 * get_trial_balance, reused verbatim by the reports app).
 *
 * `is_balanced` is the engine's verdict on closing debits == closing credits
 * and is shown as stated; this page never compares the totals itself. The
 * as-of date is always sent: omitted, the backend falls back to the SERVER's
 * date, which is not the organization's "today" near midnight in its zone.
 */
export default async function TrialBalancePage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  // Gated on VIEW_REPORTS (TrialBalanceView.required_permission).
  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_REPORTS)) {
    return (
      <>
        <PageHeader title="Trial balance" />
        <ForbiddenState resource="the trial balance" />
      </>
    );
  }

  const today = todayInZone(session.timeZone);
  const asOfParam = dateParamOf(params, "as_of_date");
  const asOf = asOfParam ?? today;
  const fromDate = dateParamOf(params, "from_date");

  const result = await tryServer(() =>
    serverApi.get<TrialBalance>("reports/trial-balance", { query: { as_of_date: asOf, from_date: fromDate } }),
  );

  // No column hides on narrow screens: the totals footer spans by position,
  // and the table scrolls horizontally instead.
  const columns: Column<TrialBalanceRow>[] = [
    { key: "code", header: "Code", cell: (row) => <span className="tabular">{row.account.code}</span> },
    { key: "name", header: "Account", cell: (row) => row.account.name },
    {
      key: "type",
      header: "Type",
      cell: (row) => ACCOUNT_TYPE_LABELS[row.account.account_type] ?? row.account.account_type,
    },
    {
      key: "opening_balance",
      header: "Opening",
      headerLabel: "Opening balance",
      numeric: true,
      cell: (row) => <Money value={row.opening_balance} accounting hideSymbol />,
    },
    {
      key: "period_debit",
      header: "Period Dr",
      headerLabel: "Period debit",
      numeric: true,
      cell: (row) => <Money value={row.period_debit} hideSymbol />,
    },
    {
      key: "period_credit",
      header: "Period Cr",
      headerLabel: "Period credit",
      numeric: true,
      cell: (row) => <Money value={row.period_credit} hideSymbol />,
    },
    {
      key: "closing_debit",
      header: "Closing Dr",
      headerLabel: "Closing debit",
      numeric: true,
      cell: (row) => <Money value={row.closing_debit} hideSymbol />,
    },
    {
      key: "closing_credit",
      header: "Closing Cr",
      headerLabel: "Closing credit",
      numeric: true,
      cell: (row) => <Money value={row.closing_credit} hideSymbol />,
    },
  ];

  const period = fromDate ? `${formatDate(fromDate)} to ${formatDate(asOf)}` : `up to ${formatDate(asOf)}`;

  return (
    <>
      <PageHeader
        title="Trial balance"
        description={`Every account's movement and closing position, ${period}. Figures in ${session.currency}.`}
        meta={
          result.ok ? (
            result.data.is_balanced ? (
              <Badge tone="success" marker>
                Balanced
              </Badge>
            ) : (
              <Badge tone="danger" marker>
                Out of balance
              </Badge>
            )
          ) : undefined
        }
        actions={<PrintButton />}
      />
      <PageBody className="print-document">
        <DateFilterForm
          action={PATH}
          fields={[
            { name: "from_date", label: "Period from", value: fromDate },
            { name: "as_of_date", label: "As of", value: asOfParam ?? today },
          ]}
          {...(fromDate || asOfParam ? { clearHref: PATH } : {})}
        />

        {result.ok ? (
          <>
            <StatGrid columns={3}>
              <StatCard
                label="Ledger check"
                value={result.data.is_balanced ? "Balanced" : "Out of balance"}
                tone={result.data.is_balanced ? "positive" : "negative"}
                hint={
                  result.data.is_balanced
                    ? "The engine reports closing debits equal closing credits."
                    : "The engine reports closing debits and credits differ. Investigate before relying on any report."
                }
              />
              <StatCard
                label="Closing debits"
                value={<Money value={result.data.total_closing_debit} />}
                hint={`As of ${formatDate(result.data.as_of_date)}`}
              />
              <StatCard
                label="Closing credits"
                value={<Money value={result.data.total_closing_credit} />}
                hint={`As of ${formatDate(result.data.as_of_date)}`}
              />
            </StatGrid>

            <DataTable
              caption={`Trial balance ${period}`}
              columns={columns}
              data={wholeList(result.data.rows)}
              getRowId={(row) => row.account.id}
              getRowHref={(row) => `/accounting/accounts/${row.account.id}`}
              emptyTitle="No accounts"
              emptyDescription="Set up the chart of accounts to see a trial balance."
              emptyAction={{ label: "Chart of accounts", href: "/accounting/accounts" }}
              page={1}
              pageSize={Math.max(result.data.rows.length, 1)}
              buildPageHref={() => PATH}
              footer={
                result.data.rows.length > 0 ? (
                  <TableFooterRow
                    label="Totals"
                    columnCount={columns.length}
                    values={[
                      { key: "period_debit", node: <Money value={result.data.total_period_debit} strong hideSymbol /> },
                      { key: "period_credit", node: <Money value={result.data.total_period_credit} strong hideSymbol /> },
                      { key: "closing_debit", node: <Money value={result.data.total_closing_debit} strong hideSymbol /> },
                      { key: "closing_credit", node: <Money value={result.data.total_closing_credit} strong hideSymbol /> },
                    ]}
                  />
                ) : undefined
              }
              note="Opening balances are signed in each account's normal direction. All totals are the engine's."
            />
          </>
        ) : (
          <ErrorState
            title="Could not load the trial balance"
            message={result.error.message}
            reference={referenceOf(result.error)}
          />
        )}
      </PageBody>
    </>
  );
}
