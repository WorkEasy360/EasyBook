import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { DetailList, PageBody, Section } from "@/components/ui/detail";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable, type Column } from "@/components/ui/data-table";
import { DateFilterForm } from "@/components/ui/date-filter-form";
import { Badge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/link-button";
import { Money } from "@/components/ui/money";
import { PrintButton } from "@/components/ui/print-button";
import { StatCard, StatGrid } from "@/components/ui/stat-card";
import { ACCOUNT_TYPE_LABELS } from "@/components/ui/status-badge";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { AccountDialogButton } from "@/features/accounting/account-dialog";
import { serverApi, tryServer } from "@/lib/api/server";
import { dateParamOf, wholeList, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import { formatDate } from "@/lib/datetime";
import { isDebitNormal, type Account, type AccountLedger, type LedgerEntry } from "@/types/api/accounting";

export const metadata: Metadata = { title: "Account" };

/**
 * One account and its ledger.
 *
 * The opening, running and closing balances are the ledger endpoint's own
 * (accounting/selectors.py :: get_account_running_ledger), signed in the
 * account's normal-balance direction. Nothing is re-added here.
 */
export default async function AccountDetailPage({
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

  if (!roleHasPermission(role, PERMISSIONS.VIEW_ACCOUNTING)) {
    return (
      <>
        <PageHeader title="Account" />
        <ForbiddenState resource="the chart of accounts" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<Account>(`accounting/accounts/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Account" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  const account = result.data;
  const fromDate = dateParamOf(query, "from_date");
  const toDate = dateParamOf(query, "to_date");
  const canViewLedger = roleHasPermission(role, PERMISSIONS.VIEW_TRANSACTIONS);
  const self = `/accounting/accounts/${account.id}`;

  const [ledger, siblings] = await Promise.all([
    canViewLedger
      ? tryServer(() =>
          serverApi.get<AccountLedger>(`accounting/accounts/${account.id}/ledger`, {
            query: { from_date: fromDate, to_date: toDate },
          }),
        )
      : Promise.resolve(null),
    // No `?parent=` filter exists, so sub-accounts are found on one page of
    // the chart. A chart larger than a page may have more than are listed.
    tryServer(() => serverApi.list<Account>("accounting/accounts", { query: { page_size: 200 } })),
  ]);

  const chart = siblings.ok ? siblings.data.results : [];
  const parent = account.parent ? chart.find((row) => row.id === account.parent) : undefined;
  const children = chart.filter((row) => row.parent === account.id);
  const title = `${account.code} · ${account.name}`;
  const normalSide = isDebitNormal(account.account_type) ? "debit" : "credit";

  const ledgerQuery = new URLSearchParams();
  if (fromDate) ledgerQuery.set("from_date", fromDate);
  if (toDate) ledgerQuery.set("to_date", toDate);
  const dateSuffix = ledgerQuery.toString();
  const glHref = `/accounting/general-ledger?account=${account.id}${dateSuffix ? `&${dateSuffix}` : ""}`;
  // Two lines of one journal can hit the same account with the same amounts,
  // so rows are keyed by position rather than by their content.
  const entries = ledger?.ok ? ledger.data.entries.map((entry, position) => ({ ...entry, position })) : [];

  const columns: Column<LedgerEntry & { position: number }>[] = [
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
      key: "description",
      header: "Description",
      hideBelow: "md",
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
        title={title}
        breadcrumbs={[{ label: "Chart of accounts", href: "/accounting/accounts" }, { label: account.code }]}
        meta={
          <>
            <Badge tone={account.is_active ? "success" : "neutral"} marker={account.is_active}>
              {account.is_active ? "Active" : "Inactive"}
            </Badge>
            {account.is_system ? <Badge tone="brand">System account</Badge> : null}
          </>
        }
        description={`${ACCOUNT_TYPE_LABELS[account.account_type] ?? account.account_type} account — increases on the ${normalSide} side.`}
        actions={
          <>
            <PrintButton />
            {canViewLedger ? <LinkButton href={glHref}>General ledger</LinkButton> : null}
            {roleHasPermission(role, PERMISSIONS.MANAGE_ACCOUNTING) ? (
              <AccountDialogButton account={account} triggerVariant="secondary" />
            ) : null}
          </>
        }
      />

      <PageBody className="print-document">
        {ledger?.ok ? (
          <StatGrid columns={3}>
            <StatCard
              label={fromDate ? `Opening · ${formatDate(fromDate)}` : "Opening balance"}
              value={<Money value={ledger.data.opening_balance} accounting />}
              hint="Posted before the period"
            />
            <StatCard
              label={toDate ? `Closing · ${formatDate(toDate)}` : "Closing balance"}
              value={<Money value={ledger.data.closing_balance} accounting />}
              hint={`Signed on the ${normalSide} side`}
            />
            <StatCard label="Postings in period" value={ledger.data.entries.length} />
          </StatGrid>
        ) : null}

        <Card>
          <CardHeader title="Details" />
          <CardBody>
            <DetailList
              columns={3}
              items={[
                { label: "Code", value: <span className="tabular">{account.code}</span> },
                { label: "Type", value: ACCOUNT_TYPE_LABELS[account.account_type] ?? account.account_type },
                { label: "Subtype", value: account.account_subtype || "—" },
                {
                  label: "Parent",
                  value: account.parent ? (
                    <Link href={`/accounting/accounts/${account.parent}`} className="text-brand-700 hover:underline">
                      {parent ? `${parent.code} · ${parent.name}` : "View parent account"}
                    </Link>
                  ) : (
                    "None"
                  ),
                },
                {
                  label: "Sub-accounts",
                  value:
                    children.length > 0 ? (
                      <span className="flex flex-wrap gap-x-3 gap-y-1">
                        {children.map((child) => (
                          <Link key={child.id} href={`/accounting/accounts/${child.id}`} className="text-brand-700 hover:underline">
                            {child.code} · {child.name}
                          </Link>
                        ))}
                      </span>
                    ) : (
                      "None"
                    ),
                },
                ...(account.description
                  ? [{ label: "Description", value: <span className="whitespace-pre-line">{account.description}</span>, span: true }]
                  : []),
              ]}
            />
          </CardBody>
        </Card>

        <Section
          title="Ledger"
          description="Posted journal lines on this account. Drafts are not included until they are posted."
        >
          {canViewLedger ? (
            <>
              <DateFilterForm
                action={self}
                fields={[
                  { name: "from_date", label: "From", value: fromDate },
                  { name: "to_date", label: "To", value: toDate },
                ]}
                {...(fromDate || toDate ? { clearHref: self } : {})}
              />
              <DataTable
                caption={`Ledger of ${title}`}
                columns={columns}
                data={ledger?.ok ? wholeList(entries) : undefined}
                error={ledger && !ledger.ok ? (ledger.error as ApiError) : null}
                getRowId={(row) => String(row.position)}
                emptyTitle={fromDate || toDate ? "No postings in this period" : "No postings yet"}
                emptyDescription="Postings appear here once a journal touching this account is posted."
                page={1}
                pageSize={Math.max(entries.length, 1)}
                buildPageHref={() => self}
              />
            </>
          ) : (
            <ForbiddenState resource="the ledger" compact />
          )}
        </Section>
      </PageBody>
    </>
  );
}
