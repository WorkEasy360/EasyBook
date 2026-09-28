import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { DetailList, PageBody, Section } from "@/components/ui/detail";
import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable, type Column } from "@/components/ui/data-table";
import { DateFilterForm } from "@/components/ui/date-filter-form";
import { LinkButton } from "@/components/ui/link-button";
import { Money } from "@/components/ui/money";
import { StatCard, StatGrid } from "@/components/ui/stat-card";
import {
  BANK_ACCOUNT_KIND_LABELS,
  STATEMENT_IMPORT_STATUS,
  StatusBadge,
} from "@/components/ui/status-badge";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { ManualTransactionButton } from "@/features/banking/manual-transaction-dialog";
import { transactionColumns } from "@/features/banking/transaction-columns";
import { serverApi, tryServer } from "@/lib/api/server";
import { dateParamOf, wholeList, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import { formatDate, formatDateTime } from "@/lib/datetime";
import { isZero } from "@/lib/money";
import type { Account } from "@/types/api/accounting";
import {
  STATEMENT_SOURCE_LABELS,
  type BankAccount,
  type BankAccountSummary,
  type BankTransaction,
  type StatementImport,
} from "@/types/api/banking";
import type { Paginated } from "@/lib/api/types";

export const metadata: Metadata = { title: "Bank account" };

const RECENT = 10;

/**
 * One bank account: the three balances, its latest statement lines and its
 * import history.
 *
 * Every balance on this page is the backend's (GET bank-accounts/{id}/summary/,
 * banking/selectors.py). Book is the ledger; statement is the opening balance
 * plus every line not excluded; cleared is the explained part of the
 * statement. The gaps between them are REPORTED by the server, never
 * subtracted here.
 */
export default async function BankAccountDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<RawSearchParams>;
}) {
  const session = await requireSession();
  const { id } = await params;
  const query = await searchParams;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.VIEW_BANK_ACCOUNTS)) {
    return (
      <>
        <PageHeader title="Bank account" />
        <ForbiddenState resource="bank accounts" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<BankAccount>(`bank-accounts/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Bank account" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  const account = result.data;
  const asOf = dateParamOf(query, "as_of");
  const canViewLines = roleHasPermission(role, PERMISSIONS.VIEW_BANK_TRANSACTIONS);
  const canViewAccounting = roleHasPermission(role, PERMISSIONS.VIEW_ACCOUNTING);
  const canImport = roleHasPermission(role, PERMISSIONS.IMPORT_BANK_STATEMENT);

  const [summary, lines, imports, ledger] = await Promise.all([
    canViewLines
      ? tryServer(() => serverApi.get<BankAccountSummary>(`bank-accounts/${id}/summary`, { query: { as_of: asOf } }))
      : Promise.resolve(null),
    canViewLines
      ? tryServer(() =>
          serverApi.list<BankTransaction>("bank-transactions", { query: { bank_account: id, page_size: RECENT } }),
        )
      : Promise.resolve(null),
    canViewLines
      ? tryServer(() =>
          serverApi.get<Paginated<StatementImport>>(`bank-accounts/${id}/statement-imports`, {
            query: { page_size: RECENT },
          }),
        )
      : Promise.resolve(null),
    canViewAccounting ? tryServer(() => serverApi.get<Account>(`accounting/accounts/${account.account}`)) : Promise.resolve(null),
  ]);

  const kindLabel = BANK_ACCOUNT_KIND_LABELS[account.kind] ?? account.kind;
  const ledgerLabel = ledger?.ok ? `${ledger.data.code} · ${ledger.data.name}` : "Linked ledger account";
  const selfHref = `/banking/accounts/${account.id}`;
  const money = (value: string) => <Money value={value} currency={account.currency} />;

  const importColumns: Column<StatementImport>[] = [
    {
      key: "created_at",
      header: "Imported",
      cell: (row) => <span className="whitespace-nowrap">{formatDateTime(row.created_at, { timeZone: session.timeZone })}</span>,
    },
    {
      key: "file_name",
      header: "File",
      cell: (row) => row.file_name || STATEMENT_SOURCE_LABELS[row.source_format] || row.source_format,
    },
    {
      key: "period",
      header: "Period",
      hideBelow: "md",
      cell: (row) =>
        row.statement_start_date && row.statement_end_date ? (
          <span className="tabular whitespace-nowrap">
            {formatDate(row.statement_start_date)} – {formatDate(row.statement_end_date)}
          </span>
        ) : (
          <span className="text-ink-400">—</span>
        ),
    },
    { key: "status", header: "Status", cell: (row) => <StatusBadge status={row.status} map={STATEMENT_IMPORT_STATUS} size="sm" /> },
    { key: "rows_read", header: "Read", numeric: true, hideBelow: "sm", cell: (row) => <span className="tabular">{row.rows_read}</span> },
    { key: "rows_imported", header: "Imported", numeric: true, cell: (row) => <span className="tabular">{row.rows_imported}</span> },
    {
      key: "rows_skipped_duplicate",
      header: "Duplicates skipped",
      numeric: true,
      hideBelow: "sm",
      cell: (row) => <span className="tabular">{row.rows_skipped_duplicate}</span>,
    },
  ];

  return (
    <>
      <PageHeader
        title={account.name}
        breadcrumbs={[{ label: "Bank accounts", href: "/banking/accounts" }, { label: account.name }]}
        meta={
          <>
            <Badge tone="brand">{kindLabel}</Badge>
            {account.is_active ? null : <Badge tone="neutral">Inactive</Badge>}
          </>
        }
        description={[account.bank_name, account.masked_number, account.currency].filter(Boolean).join(" · ")}
        actions={
          <>
            {roleHasPermission(role, PERMISSIONS.MANAGE_BANK_ACCOUNTS) ? (
              <LinkButton href={`${selfHref}/edit`}>Edit</LinkButton>
            ) : null}
            {canImport && account.is_active ? <ManualTransactionButton bankAccountId={account.id} currency={account.currency} /> : null}
            {roleHasPermission(role, PERMISSIONS.RECORD_BANK_TRANSFER) && account.is_active ? (
              <LinkButton href={`/banking/transfers/new?from=${account.id}`}>Transfer</LinkButton>
            ) : null}
            {roleHasPermission(role, PERMISSIONS.RECONCILE_BANK) ? (
              <LinkButton href={`/banking/reconciliation/new?bank_account=${account.id}`}>Reconcile</LinkButton>
            ) : null}
            {/* The importer refuses an inactive account (bank_account_inactive). */}
            {canImport && account.is_active ? (
              <LinkButton href={`${selfHref}/import`} variant="primary">
                Import statement
              </LinkButton>
            ) : null}
          </>
        }
      />

      <PageBody>
        {summary ? (
          <Section
            title={asOf ? `Balances as of ${formatDate(asOf)}` : "Balances"}
            description="Calculated by the server from posted journals and the statement lines held for this account."
            actions={
              <DateFilterForm
                action={selfHref}
                fields={[{ name: "as_of", label: "As of", value: asOf }]}
                {...(asOf ? { clearHref: selfHref } : {})}
              />
            }
          >
            {summary.ok ? (
              <StatGrid columns={3}>
                <StatCard
                  label="Statement balance"
                  value={money(summary.data.statement_balance)}
                  hint="Opening balance plus every line not excluded"
                />
                <StatCard
                  label="Cleared balance"
                  value={money(summary.data.cleared_balance)}
                  hint="Opening balance plus the matched lines"
                />
                <StatCard label="Book balance" value={money(summary.data.book_balance)} hint={`Posted journals on ${ledgerLabel}`} />
                <StatCard
                  label="Not yet explained"
                  value={money(summary.data.unexplained_statement_amount)}
                  tone={isZero(summary.data.unexplained_statement_amount) ? "default" : "warning"}
                  hint="Statement minus cleared: lines still to match, categorize or exclude"
                />
                <StatCard
                  label="Uncleared in the books"
                  value={money(summary.data.uncleared_book_amount)}
                  hint="Book minus cleared: timing differences such as uncleared cheques. Not an error."
                />
                <StatCard
                  label="Open lines"
                  value={summary.data.open_transaction_count}
                  tone={summary.data.open_transaction_count > 0 ? "warning" : "default"}
                  hint="Unmatched or suggested"
                  {...(summary.data.open_transaction_count > 0
                    ? { href: `/banking/transactions?bank_account=${account.id}&status=unmatched` }
                    : {})}
                />
              </StatGrid>
            ) : (
              <ErrorState
                compact
                title="Could not load the balances"
                message={summary.error.message}
                reference={referenceOf(summary.error)}
              />
            )}
          </Section>
        ) : null}

        <Card>
          <CardHeader title="Details" />
          <CardBody>
            <DetailList
              columns={3}
              items={[
                { label: "Kind", value: kindLabel },
                {
                  label: "Ledger account",
                  value: canViewAccounting ? (
                    <Link href={`/accounting/accounts/${account.account}`} className="text-brand-700 hover:underline">
                      {ledgerLabel}
                    </Link>
                  ) : (
                    "Linked"
                  ),
                },
                { label: "Currency", value: account.currency },
                { label: "Bank", value: account.bank_name || "—" },
                { label: "Account number", value: account.masked_number ? <span className="tabular">{account.masked_number}</span> : "—" },
                { label: "Branch identifier", value: account.branch_identifier || "—" },
                {
                  label: "Opening statement balance",
                  value: (
                    <>
                      {money(account.opening_balance)}
                      {account.opening_balance_date ? (
                        <span className="block text-xs text-ink-500">on {formatDate(account.opening_balance_date)}</span>
                      ) : null}
                    </>
                  ),
                },
                { label: "Statement source", value: account.provider_key === "manual" ? "File import and manual entry" : account.provider_key },
                { label: "Created", value: formatDateTime(account.created_at, { timeZone: session.timeZone }) },
                ...(account.notes ? [{ label: "Notes", value: <span className="whitespace-pre-line">{account.notes}</span>, span: true }] : []),
              ]}
            />
          </CardBody>
        </Card>

        {lines ? (
          <Section
            title="Recent statement lines"
            description="Newest first. Open a line to match, categorize or exclude it."
            actions={
              <LinkButton href={`/banking/transactions?bank_account=${account.id}`} size="sm">
                All lines
              </LinkButton>
            }
          >
            <DataTable
              caption={`Recent statement lines for ${account.name}`}
              columns={transactionColumns({ accounts: new Map([[account.id, account]]), showAccount: false })}
              data={lines.ok ? wholeList(lines.data.results) : undefined}
              error={lines.ok ? null : (lines.error as ApiError)}
              getRowId={(row) => row.id}
              getRowHref={(row) => `/banking/transactions/${row.id}`}
              emptyTitle="No statement lines yet"
              emptyDescription="Import a CSV statement from your bank to start reconciling this account."
              {...(canImport && account.is_active ? { emptyAction: { label: "Import statement", href: `${selfHref}/import` } } : {})}
              page={1}
              pageSize={RECENT}
              buildPageHref={() => selfHref}
              {...(lines.ok && lines.data.count > RECENT
                ? { note: `Showing the latest ${RECENT} of ${lines.data.count} lines.` }
                : {})}
            />
          </Section>
        ) : null}

        {imports ? (
          <Section title="Statement imports" description="Each file imported into this account. Importing posts nothing to the ledger.">
            <DataTable
              caption={`Statement imports for ${account.name}`}
              columns={importColumns}
              data={imports.ok ? wholeList(imports.data.results) : undefined}
              error={imports.ok ? null : (imports.error as ApiError)}
              getRowId={(row) => row.id}
              emptyTitle="No statements imported"
              page={1}
              pageSize={RECENT}
              buildPageHref={() => selfHref}
              {...(imports.ok && imports.data.count > RECENT
                ? { note: `Showing the latest ${RECENT} of ${imports.data.count} imports.` }
                : {})}
            />
          </Section>
        ) : null}
      </PageBody>
    </>
  );
}
