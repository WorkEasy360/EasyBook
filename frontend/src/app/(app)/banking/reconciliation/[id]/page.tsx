import { appHref } from "@/lib/routes";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { DetailList, PageBody, Section } from "@/components/ui/detail";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable, type Column } from "@/components/ui/data-table";
import { Money } from "@/components/ui/money";
import { StatCard, StatGrid } from "@/components/ui/stat-card";
import { BANK_RECONCILIATION_STATUS, StatusBadge } from "@/components/ui/status-badge";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { DocumentAction } from "@/features/shared/document-action";
import { transactionColumns } from "@/features/banking/transaction-columns";
import { serverApi, tryServer } from "@/lib/api/server";
import { wholeList } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import { formatDate, formatDateTime } from "@/lib/datetime";
import type {
  BankAccount,
  BankReconciliation,
  BankTransaction,
  ReconciliationSummary,
} from "@/types/api/banking";

export const metadata: Metadata = { title: "Reconciliation" };

const PERIOD_PAGE = 200;

/**
 * One reconciliation: the server's verdict and the lines behind it.
 *
 * GET bank-reconciliations/{id}/summary/ is the same computation
 * `complete_reconciliation` runs (banking/selectors.py ::
 * get_reconciliation_summary), so this page shows exactly what will block
 * completion. `can_complete` alone enables the Complete button — there is no
 * override, by design: a period closed over an unexplained difference would
 * certify nothing.
 */
export default async function ReconciliationDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.VIEW_BANK_TRANSACTIONS)) {
    return (
      <>
        <PageHeader title="Reconciliation" />
        <ForbiddenState resource="bank reconciliations" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<BankReconciliation>(`bank-reconciliations/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Reconciliation" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  const reconciliation = result.data;
  const [summary, account, lines] = await Promise.all([
    tryServer(() => serverApi.get<ReconciliationSummary>(`bank-reconciliations/${id}/summary`)),
    tryServer(() => serverApi.get<BankAccount>(`bank-accounts/${reconciliation.bank_account}`)),
    tryServer(() =>
      serverApi.list<BankTransaction>("bank-transactions", {
        query: {
          bank_account: reconciliation.bank_account,
          from_date: reconciliation.statement_start_date,
          to_date: reconciliation.statement_end_date,
          page_size: PERIOD_PAGE,
        },
      }),
    ),
  ]);

  const bankAccount = account.ok ? account.data : null;
  const currency = bankAccount ? { currency: bankAccount.currency } : {};
  const canReconcile = roleHasPermission(role, PERMISSIONS.RECONCILE_BANK);
  const inProgress = reconciliation.status === "in_progress";
  const completed = reconciliation.status === "completed";
  const period = `${formatDate(reconciliation.statement_start_date)} – ${formatDate(reconciliation.statement_end_date)}`;
  const title = bankAccount ? `${bankAccount.name} · ${period}` : `Reconciliation · ${period}`;
  const selfHref = `/banking/reconciliation/${reconciliation.id}`;
  const openLinesHref = `/banking/transactions?bank_account=${reconciliation.bank_account}&status=unmatched&from_date=${reconciliation.statement_start_date}&to_date=${reconciliation.statement_end_date}`;
  const s = summary.ok ? summary.data : null;

  // What the page says about each line's effect on the cleared balance —
  // selectors.get_cleared_balance counts MATCHED lines only, and
  // get_statement_balance leaves EXCLUDED lines out.
  const clearedColumn: Column<BankTransaction> = {
    key: "cleared",
    header: "In cleared balance",
    hideBelow: "md",
    cell: (row) =>
      row.status === "matched" ? (
        "Yes"
      ) : row.status === "excluded" ? (
        <span className="text-ink-500">Excluded from the statement</span>
      ) : (
        <span className="font-medium text-warning-700">No — still open</span>
      ),
  };
  const baseColumns = transactionColumns({ accounts: bankAccount ? new Map([[bankAccount.id, bankAccount]]) : undefined, showAccount: false });
  const statusIndex = baseColumns.findIndex((column) => column.key === "status");
  const lineColumns = [...baseColumns.slice(0, statusIndex + 1), clearedColumn, ...baseColumns.slice(statusIndex + 1)];

  return (
    <>
      <PageHeader
        title={title}
        breadcrumbs={[{ label: "Reconciliation", href: "/banking/reconciliation" }, { label: period }]}
        meta={<StatusBadge status={reconciliation.status} map={BANK_RECONCILIATION_STATUS} />}
        description={bankAccount ? `Statement period for ${bankAccount.name}` : undefined}
        actions={
          canReconcile ? (
            <>
              {inProgress ? (
                <DocumentAction
                  resource="bank-reconciliations"
                  id={reconciliation.id}
                  action="abandon"
                  label="Abandon"
                  variant="secondary"
                  confirmTitle="Abandon this reconciliation?"
                  confirmMessage={
                    <p>
                      Nothing is certified and no line is locked, so the period can be started again later. The attempt stays on
                      record, marked abandoned.
                    </p>
                  }
                  reason={{ label: "Reason", hint: "Optional. Added to the reconciliation's notes." }}
                  successTitle="Reconciliation abandoned"
                />
              ) : null}
              {completed ? (
                <DocumentAction
                  resource="bank-reconciliations"
                  id={reconciliation.id}
                  action="reopen"
                  label="Reopen"
                  variant="secondary"
                  confirmTitle="Reopen this completed reconciliation?"
                  confirmMessage={
                    <>
                      <p>
                        Unlocks the lines in this period so they can be corrected. The certified cleared balance
                        {reconciliation.cleared_balance !== null ? (
                          <>
                            {" "}
                            (<Money value={reconciliation.cleared_balance} {...currency} />)
                          </>
                        ) : null}{" "}
                        stays on record until the period is completed again.
                      </p>
                      <p className="mt-2">Only possible while no other reconciliation is in progress on this account.</p>
                    </>
                  }
                  reason={{ label: "Reason", required: true, hint: "Required. Recorded in the notes and the audit trail." }}
                  successTitle="Reconciliation reopened"
                />
              ) : null}
              {inProgress ? (
                <DocumentAction
                  resource="bank-reconciliations"
                  id={reconciliation.id}
                  action="complete"
                  label="Complete reconciliation"
                  variant="primary"
                  // The server's verdict is the only gate. An unknown summary
                  // (failed load) keeps the button disabled.
                  disabled={!s?.can_complete}
                  confirmTitle="Complete this reconciliation?"
                  confirmMessage={
                    s ? (
                      <>
                        <p>
                          The server reports a difference of <Money value={s.difference} {...currency} /> between the statement
                          closing balance (<Money value={s.statement_closing_balance} {...currency} />) and the cleared balance
                          (<Money value={s.cleared_balance} {...currency} />), with no open lines.
                        </p>
                        <p className="mt-2">
                          Completing certifies the cleared balance and locks the {s.transaction_count}{" "}
                          {s.transaction_count === 1 ? "line" : "lines"} in this period. Changing any of them later requires
                          reopening with a reason. Nothing is posted to the ledger.
                        </p>
                      </>
                    ) : (
                      <p>The summary could not be loaded.</p>
                    )
                  }
                  successTitle="Reconciliation completed"
                />
              ) : null}
            </>
          ) : null
        }
      />

      <PageBody>
        {s ? (
          <>
            <StatGrid columns={3}>
              <StatCard label="Statement closing balance" value={<Money value={s.statement_closing_balance} {...currency} />} hint="As typed from the statement" />
              <StatCard
                label="Cleared balance"
                value={<Money value={s.cleared_balance} {...currency} />}
                hint={`Opening balance plus matched lines to ${formatDate(reconciliation.statement_end_date)}`}
              />
              <StatCard
                label="Difference"
                value={<Money value={s.difference} {...currency} />}
                tone={s.is_balanced ? "positive" : "negative"}
                hint={s.is_balanced ? "Balanced" : "Not balanced — closing minus cleared"}
              />
              <StatCard
                label="Book balance"
                value={<Money value={s.book_balance} {...currency} />}
                hint="Ledger, at the end date. Differences from cleared are timing, not errors."
              />
              <StatCard label="Lines in period" value={s.transaction_count} />
              <StatCard
                label="Open lines"
                value={s.open_transaction_count}
                tone={s.open_transaction_count > 0 ? "warning" : "default"}
                hint="Unmatched or suggested"
                {...(s.open_transaction_count > 0 ? { href: openLinesHref } : {})}
              />
            </StatGrid>

            {inProgress ? (
              <div
                role="status"
                className={
                  s.can_complete
                    ? "rounded-md border border-success-100 bg-success-50 px-3 py-2 text-sm text-success-700"
                    : "rounded-md border border-warning-100 bg-warning-50 px-3 py-2 text-sm text-warning-700"
                }
              >
                {s.can_complete ? (
                  <p>Ready to complete: the period balances and every line in it is explained.</p>
                ) : (
                  <>
                    <p className="font-medium">Not ready to complete:</p>
                    <ul className="mt-1 list-disc pl-5">
                      {!s.is_balanced ? (
                        <li>
                          The cleared balance differs from the statement closing balance by{" "}
                          <Money value={s.difference} {...currency} colorNegative={false} />.
                        </li>
                      ) : null}
                      {s.open_transaction_count > 0 ? (
                        <li>
                          {s.open_transaction_count} {s.open_transaction_count === 1 ? "line is" : "lines are"} still unmatched or
                          suggested.{" "}
                          <Link href={appHref(openLinesHref)} className="font-medium underline">
                            Review them
                          </Link>
                        </li>
                      ) : null}
                    </ul>
                  </>
                )}
              </div>
            ) : null}
          </>
        ) : (
          <ErrorState
            compact
            title="Could not load the reconciliation summary"
            message={summary.ok ? "" : summary.error.message}
            reference={summary.ok ? null : referenceOf(summary.error)}
          />
        )}

        <Card>
          <CardHeader title="Details" />
          <CardBody>
            <DetailList
              columns={3}
              items={[
                {
                  label: "Bank account",
                  value: bankAccount ? (
                    <Link href={`/banking/accounts/${bankAccount.id}`} className="text-brand-700 hover:underline">
                      {bankAccount.name}
                    </Link>
                  ) : (
                    "—"
                  ),
                },
                { label: "Statement period", value: period },
                {
                  label: "Certified cleared balance",
                  value:
                    reconciliation.cleared_balance !== null ? (
                      <Money value={reconciliation.cleared_balance} {...currency} />
                    ) : (
                      "Set when completed"
                    ),
                },
                { label: "Started", value: formatDateTime(reconciliation.created_at, { timeZone: session.timeZone }) },
                {
                  label: "Completed",
                  value: reconciliation.completed_at ? formatDateTime(reconciliation.completed_at, { timeZone: session.timeZone }) : "—",
                },
                ...(reconciliation.notes
                  ? [{ label: "Notes", value: <span className="whitespace-pre-line">{reconciliation.notes}</span>, span: true }]
                  : []),
              ]}
            />
          </CardBody>
        </Card>

        <Section
          title="Lines in this period"
          description="Matched lines make up the cleared balance. Open a line to match, categorize or exclude it."
        >
          <DataTable
            caption={`Statement lines from ${period}`}
            columns={lineColumns}
            data={lines.ok ? wholeList(lines.data.results) : undefined}
            error={lines.ok ? null : (lines.error as ApiError)}
            getRowId={(row) => row.id}
            getRowHref={(row) => `/banking/transactions/${row.id}`}
            emptyTitle="No statement lines in this period"
            emptyDescription="Import the statement for these dates on the bank account first."
            {...(bankAccount?.is_active && roleHasPermission(role, PERMISSIONS.IMPORT_BANK_STATEMENT)
              ? { emptyAction: { label: "Import statement", href: `/banking/accounts/${bankAccount.id}/import` } }
              : {})}
            page={1}
            pageSize={PERIOD_PAGE}
            buildPageHref={() => selfHref}
            {...(lines.ok && lines.data.count > PERIOD_PAGE
              ? {
                  note: (
                    <>
                      Showing the latest {PERIOD_PAGE} of {lines.data.count} lines.{" "}
                      <Link
                        href={`/banking/transactions?bank_account=${reconciliation.bank_account}&from_date=${reconciliation.statement_start_date}&to_date=${reconciliation.statement_end_date}`}
                        className="underline"
                      >
                        See all
                      </Link>
                    </>
                  ),
                }
              : {})}
          />
        </Section>
      </PageBody>
    </>
  );
}
