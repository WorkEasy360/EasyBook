import type { Metadata } from "next";
import Link from "next/link";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { DataTable, type Column } from "@/components/ui/data-table";
import { SortNote } from "@/components/ui/filter-bar";
import { LinkButton } from "@/components/ui/link-button";
import { Money } from "@/components/ui/money";
import { BANK_TRANSFER_STATUS, StatusBadge } from "@/components/ui/status-badge";
import { ForbiddenState } from "@/components/ui/states";
import { DocumentAction } from "@/features/shared/document-action";
import { serverApi, tryServer } from "@/lib/api/server";
import { capabilitiesFor } from "@/lib/api/capabilities";
import { parseListQuery, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { formatDate, formatDateTime } from "@/lib/datetime";
import type { ApiError } from "@/lib/api/errors";
import type { BankAccount, BankTransfer } from "@/types/api/banking";

export const metadata: Metadata = { title: "Transfers" };

/**
 * Transfers between the organization's own accounts.
 *
 * The API has a list, a create and a void — no detail endpoint — so voiding
 * happens from the row. The list takes no filters (BankTransferListView reads
 * none). Amounts are each transfer's own; no balance is derived here.
 */
export default async function TransfersPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.VIEW_BANK_TRANSACTIONS)) {
    return (
      <>
        <PageHeader title="Transfers" />
        <ForbiddenState resource="bank transfers" />
      </>
    );
  }

  const canRecord = roleHasPermission(role, PERMISSIONS.RECORD_BANK_TRANSFER);
  const canViewAccounting = roleHasPermission(role, PERMISSIONS.VIEW_ACCOUNTING);
  const query = parseListQuery("/banking/transfers", "bank-transfers", params);

  const [result, accounts] = await Promise.all([
    tryServer(() => serverApi.list<BankTransfer>("bank-transfers", { query: query.apiParams })),
    roleHasPermission(role, PERMISSIONS.VIEW_BANK_ACCOUNTS)
      ? tryServer(() => serverApi.list<BankAccount>("bank-accounts", { query: { page_size: 200 } }))
      : Promise.resolve(null),
  ]);
  const accountById = new Map((accounts?.ok ? accounts.data.results : []).map((row) => [row.id, row]));
  const nameOf = (id: string) => accountById.get(id)?.name ?? "Bank account";

  const columns: Column<BankTransfer>[] = [
    {
      key: "transfer_number",
      header: "Transfer",
      cell: (row) => <span className="tabular">{row.transfer_number}</span>,
    },
    {
      key: "transfer_date",
      header: "Date",
      cell: (row) => <span className="tabular whitespace-nowrap">{formatDate(row.transfer_date)}</span>,
    },
    {
      key: "accounts",
      header: "From → To",
      cell: (row) => (
        <span>
          <Link href={`/banking/accounts/${row.from_bank_account}`} className="text-brand-700 hover:underline">
            {nameOf(row.from_bank_account)}
          </Link>
          <span aria-hidden="true" className="px-1 text-ink-400">
            →
          </span>
          <span className="sr-only"> to </span>
          <Link href={`/banking/accounts/${row.to_bank_account}`} className="text-brand-700 hover:underline">
            {nameOf(row.to_bank_account)}
          </Link>
          {row.reference ? <span className="block text-xs text-ink-500">{row.reference}</span> : null}
        </span>
      ),
    },
    {
      key: "status",
      header: "Status",
      cell: (row) => (
        <span>
          <StatusBadge status={row.status} map={BANK_TRANSFER_STATUS} size="sm" />
          {row.status === "void" && row.voided_at ? (
            <span className="block text-xs text-ink-500">
              {formatDateTime(row.voided_at, { timeZone: session.timeZone })}
              {row.void_reason ? ` · ${row.void_reason}` : ""}
            </span>
          ) : null}
        </span>
      ),
    },
    {
      key: "journal",
      header: "Journal",
      hideBelow: "md",
      cell: (row) =>
        row.accounting_journal && canViewAccounting ? (
          <Link href={`/accounting/journals/${row.accounting_journal}`} className="text-brand-700 hover:underline">
            View journal
          </Link>
        ) : (
          <span className="text-ink-400">—</span>
        ),
    },
    {
      key: "amount",
      header: "Amount",
      numeric: true,
      cell: (row) => <Money value={row.amount} currency={row.currency} />,
    },
    ...(canRecord
      ? [
          {
            key: "actions",
            header: "Actions",
            headerLabel: "Actions",
            cell: (row: BankTransfer) =>
              row.status === "posted" ? (
                <DocumentAction
                  resource="bank-transfers"
                  id={row.id}
                  action="void"
                  label="Void"
                  size="sm"
                  variant="danger"
                  confirmTitle={`Void ${row.transfer_number}?`}
                  confirmMessage={
                    <>
                      <p>
                        Posts a reversing journal dated today for <Money value={row.amount} currency={row.currency} />, undoing
                        the move from {nameOf(row.from_bank_account)} to {nameOf(row.to_bank_account)}. The transfer stays on
                        record, marked void. This cannot be undone.
                      </p>
                      <p className="mt-2">
                        If a statement line is matched to this transfer, unmatch it first — the server refuses otherwise.
                      </p>
                    </>
                  }
                  reason={{ label: "Reason", hint: "Kept on the transfer for the audit trail." }}
                  successTitle="Transfer voided"
                />
              ) : null,
          },
        ]
      : []),
  ];

  return (
    <>
      <PageHeader
        title="Transfers"
        description="Money moved between your own accounts. Each posts one journal between the two ledger accounts and never touches income or expense."
        actions={
          canRecord ? (
            <LinkButton href="/banking/transfers/new" variant="primary">
              New transfer
            </LinkButton>
          ) : null
        }
      />
      <PageBody>
        <DataTable
          caption="Transfers"
          columns={columns}
          data={result.ok ? result.data : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          getRowId={(row) => row.id}
          emptyTitle="No transfers yet"
          emptyDescription="Record a transfer when money moves between two of your accounts."
          {...(canRecord ? { emptyAction: { label: "New transfer", href: "/banking/transfers/new" } } : {})}
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={query.buildPageHref}
          note={<SortNote description={capabilitiesFor("bank-transfers").defaultOrder ?? "transfer date"} />}
        />
      </PageBody>
    </>
  );
}
