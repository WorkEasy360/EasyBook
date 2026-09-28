import { appHref } from "@/lib/routes";
import type { Metadata } from "next";
import Link from "next/link";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { DataTable, type Column } from "@/components/ui/data-table";
import { DateFilterForm } from "@/components/ui/date-filter-form";
import { Money } from "@/components/ui/money";
import { PrintButton } from "@/components/ui/print-button";
import { StatCard, StatGrid } from "@/components/ui/stat-card";
import { ACCOUNT_TYPE_LABELS } from "@/components/ui/status-badge";
import { EmptyState, ErrorState, ForbiddenState } from "@/components/ui/states";
import { LedgerAccountSwitcher } from "@/features/accounting/ledger-account-switcher";
import { journalSourceOf } from "@/features/accounting/journal-source";
import { serverApi, tryServer } from "@/lib/api/server";
import { DEFAULT_PAGE_SIZE, dateParamOf, paramOf, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import { formatDate } from "@/lib/datetime";
import { isDebitNormal, type GeneralLedgerEntry, type GeneralLedgerReport } from "@/types/api/accounting";

export const metadata: Metadata = { title: "General ledger" };

const PATH = "/accounting/general-ledger";
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * One account's posted lines over a period — reports/general-ledger/.
 *
 * The report is PAGINATED and computes the running balance over the whole
 * period before slicing out a page, so every figure on every page is the
 * engine's. Opening and closing balances come from the same response. There
 * is no CSV export for this report (reports/exports/csv.py), so none is
 * offered; Print covers the page on screen.
 */
export default async function GeneralLedgerPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  // reports/api/views.py :: GeneralLedgerReportView.required_permission
  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_TRANSACTIONS)) {
    return (
      <>
        <PageHeader title="General ledger" />
        <ForbiddenState resource="the general ledger" />
      </>
    );
  }

  const rawAccount = paramOf(params, "account");
  const accountId = rawAccount && UUID_PATTERN.test(rawAccount) ? rawAccount : null;
  const fromDate = dateParamOf(params, "from_date");
  const toDate = dateParamOf(params, "to_date");
  const rawPage = Number(paramOf(params, "page") ?? "1");
  const page = Number.isFinite(rawPage) && rawPage >= 1 ? Math.floor(rawPage) : 1;

  const report = accountId
    ? await tryServer(() =>
        serverApi.get<GeneralLedgerReport>("reports/general-ledger", {
          query: { account: accountId, from_date: fromDate, to_date: toDate, page: page > 1 ? page : undefined },
        }),
      )
    : null;

  function href(overrides: Record<string, string | undefined>): string {
    const next = new URLSearchParams();
    const values = { account: accountId ?? undefined, from_date: fromDate, to_date: toDate, ...overrides };
    for (const [key, value] of Object.entries(values)) {
      if (value) next.set(key, value);
    }
    const query = next.toString();
    return query ? `${PATH}?${query}` : PATH;
  }

  const account = report?.ok ? report.data.account : null;
  const normalSide = account ? (isDebitNormal(account.account_type) ? "debit" : "credit") : null;
  // Rows carry no id of their own; a journal can put two lines on one account.
  const rows = report?.ok ? report.data.results.map((row, position) => ({ ...row, position })) : [];

  const columns: Column<GeneralLedgerEntry & { position: number }>[] = [
    {
      key: "posting_date",
      header: "Date",
      cell: (row) => <span className="tabular whitespace-nowrap">{formatDate(row.posting_date)}</span>,
    },
    {
      key: "journal",
      header: "Journal",
      cell: (row) => (
        <Link href={`/accounting/journals/${row.journal_entry_id}`} className="tabular text-brand-700 hover:underline">
          {row.journal_number || "Journal"}
        </Link>
      ),
    },
    {
      key: "source",
      header: "Source",
      hideBelow: "md",
      cell: (row) => {
        const source = journalSourceOf(row.source_type, row.source_id);
        if (!source) return <span className="text-ink-500">Manual</span>;
        return source.href ? (
          <Link href={appHref(source.href)} className="text-brand-700 hover:underline">
            {source.label}
          </Link>
        ) : (
          source.label
        );
      },
    },
    {
      key: "description",
      header: "Description",
      hideBelow: "lg",
      cell: (row) => row.description || <span className="text-ink-400">—</span>,
    },
    { key: "debit", header: "Debit", numeric: true, cell: (row) => <Money value={row.debit} hideSymbol /> },
    { key: "credit", header: "Credit", numeric: true, cell: (row) => <Money value={row.credit} hideSymbol /> },
    {
      key: "running_balance",
      header: "Balance",
      numeric: true,
      cell: (row) => <Money value={row.running_balance} accounting strong />,
    },
  ];

  return (
    <>
      <PageHeader
        title="General ledger"
        description={
          account
            ? `${account.code} · ${account.name}${fromDate || toDate ? ` — ${fromDate ? formatDate(fromDate) : "start"} to ${toDate ? formatDate(toDate) : "today"}` : ""}`
            : "Choose an account to see its posted lines and running balance."
        }
        actions={account ? <PrintButton /> : null}
      />
      <PageBody className="print-document">
        <div className="flex flex-col gap-3">
          <LedgerAccountSwitcher path={PATH} accountId={accountId} preserve={{ from_date: fromDate, to_date: toDate }} />
          {accountId ? (
            <DateFilterForm
              action={PATH}
              fields={[
                { name: "from_date", label: "From", value: fromDate },
                { name: "to_date", label: "To", value: toDate },
              ]}
              preserve={{ account: accountId }}
              {...(fromDate || toDate ? { clearHref: href({ from_date: undefined, to_date: undefined }) } : {})}
            />
          ) : null}
        </div>

        {!accountId ? (
          <div className="rounded-lg border border-ink-200 bg-white">
            <EmptyState
              title="No account chosen"
              description="The general ledger is read one account at a time. Pick an account above."
              action={{ label: "Browse the chart of accounts", href: "/accounting/accounts" }}
            />
          </div>
        ) : report && !report.ok ? (
          report.error instanceof ApiError && report.error.isNotFound ? (
            <ErrorState title="Account not found" message="That account does not exist in this organization." />
          ) : (
            <ErrorState message={report.error.message} reference={referenceOf(report.error)} />
          )
        ) : report?.ok && account ? (
          <>
            <StatGrid columns={3}>
              <StatCard
                label={fromDate ? `Opening · ${formatDate(fromDate)}` : "Opening balance"}
                value={<Money value={report.data.opening_balance} accounting />}
              />
              <StatCard
                label={toDate ? `Closing · ${formatDate(toDate)}` : "Closing balance"}
                value={<Money value={report.data.closing_balance} accounting />}
                hint={`Positive = ${normalSide} balance`}
              />
              <StatCard
                label="Account"
                value={
                  <Link href={`/accounting/accounts/${account.id}`} className="text-base text-brand-700 hover:underline">
                    {account.code} · {account.name}
                  </Link>
                }
                hint={`${ACCOUNT_TYPE_LABELS[account.account_type] ?? account.account_type} · ${report.data.count} postings`}
              />
            </StatGrid>

            <DataTable
              caption={`General ledger for ${account.code} ${account.name}`}
              columns={columns}
              data={{ ...report.data, results: rows }}
              getRowId={(row) => `${page}-${row.position}`}
              emptyTitle={fromDate || toDate ? "No postings in this period" : "No postings on this account yet"}
              emptyDescription="Only posted journals appear in the ledger."
              page={page}
              pageSize={DEFAULT_PAGE_SIZE}
              buildPageHref={(next) => href({ page: next > 1 ? String(next) : undefined })}
              note={
                <>
                  Oldest first. Balances are the engine&apos;s running balance for the whole period, signed so that
                  a positive figure is a {normalSide} balance.
                </>
              }
            />
          </>
        ) : null}
      </PageBody>
    </>
  );
}
