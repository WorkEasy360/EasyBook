import { appHref } from "@/lib/routes";
import type { Metadata } from "next";
import type * as React from "react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { DetailList, PageBody, Section } from "@/components/ui/detail";
import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable, type Column } from "@/components/ui/data-table";
import { Money } from "@/components/ui/money";
import { StatCard, StatGrid } from "@/components/ui/stat-card";
import { BANK_RECONCILIATION_STATUS, BANK_TRANSACTION_STATUS, StatusBadge } from "@/components/ui/status-badge";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { DocumentAction } from "@/features/shared/document-action";
import { confidencePercent, unsigned } from "@/features/banking/amounts";
import {
  ApplyRulesButton,
  AutoMatchButton,
  CategorizeButton,
  ConfirmTransferPairButton,
  CreateMatchButton,
  FindSuggestionsButton,
  RemoveMatchButton,
} from "@/features/banking/transaction-actions";
import { serverApi, tryServer } from "@/lib/api/server";
import { recordsById } from "@/lib/api/lookups";
import { wholeList } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import { formatDate, formatDateTime } from "@/lib/datetime";
import type { Account, JournalEntry } from "@/types/api/accounting";
import type { CustomerPayment } from "@/types/api/sales";
import type { Expense, VendorPayment } from "@/types/api/purchases";
import {
  COUNTERPART_TYPE_LABELS,
  MATCH_TYPE_LABELS,
  SUGGESTION_SOURCE_LABELS,
  type BankAccount,
  type BankReconciliation,
  type BankTransaction,
  type BankTransactionMatch,
  type BankTransfer,
  type TransferCandidate,
} from "@/types/api/banking";

export const metadata: Metadata = { title: "Bank transaction" };

/**
 * The matching workspace for one statement line.
 *
 * What is possible depends on the line's status, and each rule is the
 * backend's (banking/services/matching.py, transactions.py, transfers.py):
 *  - unmatched / suggested: find suggestions, auto-match, match by hand,
 *    categorize, apply rules, pair with a transfer leg; exclude only while no
 *    confirmed match exists (cannot_exclude_matched_transaction);
 *  - matched: remove a match (posts nothing) or uncategorize (posts a
 *    reversal) to reopen it;
 *  - excluded: restore, and nothing else (transaction_excluded);
 *  - inside a COMPLETED reconciliation: nothing, until that reconciliation is
 *    reopened with a reason (transaction_reconciled).
 * Every status and amount shown is the server's; the page re-renders after
 * each action rather than guessing what changed.
 */
export default async function BankTransactionPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.VIEW_BANK_TRANSACTIONS)) {
    return (
      <>
        <PageHeader title="Bank transaction" />
        <ForbiddenState resource="bank transactions" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<BankTransaction>(`bank-transactions/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Bank transaction" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  const line = result.data;
  const canReconcile = roleHasPermission(role, PERMISSIONS.RECONCILE_BANK);
  const canViewAccounting = roleHasPermission(role, PERMISSIONS.VIEW_ACCOUNTING);
  const isOpen = line.status === "unmatched" || line.status === "suggested";

  const [account, matches, candidates, reconciliation] = await Promise.all([
    tryServer(() => serverApi.get<BankAccount>(`bank-accounts/${line.bank_account}`)),
    // GET …/suggestions/ returns every match row on the line, confirmed or
    // not. The view requires RECONCILE_BANK even to read.
    canReconcile
      ? tryServer(() => serverApi.get<BankTransactionMatch[]>(`bank-transactions/${line.id}/suggestions`))
      : Promise.resolve(null),
    canReconcile && isOpen
      ? tryServer(() => serverApi.get<TransferCandidate[]>(`bank-transactions/${line.id}/transfer-candidates`))
      : Promise.resolve(null),
    line.reconciliation
      ? tryServer(() => serverApi.get<BankReconciliation>(`bank-reconciliations/${line.reconciliation}`))
      : Promise.resolve(null),
  ]);

  const bankAccount = account.ok ? account.data : null;
  const matchRows = matches?.ok ? matches.data : [];
  const confirmed = matchRows.filter((match) => match.is_confirmed);
  const suggestions = matchRows.filter((match) => !match.is_confirmed);
  // A line in a completed period is signed off; the server refuses every change.
  const locked = reconciliation?.ok ? reconciliation.data.status === "completed" : line.reconciliation !== null;
  const canAct = canReconcile && !locked;
  const currency = bankAccount?.currency;

  // Names for the documents matches point at. Each lookup is skipped for a
  // role that cannot read that module; the match's own `reason` is the fallback.
  const idsOf = (field: "customer_payment" | "vendor_payment" | "expense" | "journal_entry") =>
    matchRows.map((match) => match[field]);
  const [customerPayments, vendorPayments, expenses, journals, transfers, ledger] = await Promise.all([
    roleHasPermission(role, PERMISSIONS.VIEW_PAYMENTS)
      ? recordsById<CustomerPayment>("sales/payments", idsOf("customer_payment"))
      : Promise.resolve(new Map<string, CustomerPayment>()),
    roleHasPermission(role, PERMISSIONS.VIEW_VENDOR_PAYMENTS)
      ? recordsById<VendorPayment>("purchases/payments", idsOf("vendor_payment"))
      : Promise.resolve(new Map<string, VendorPayment>()),
    roleHasPermission(role, PERMISSIONS.VIEW_EXPENSES)
      ? recordsById<Expense>("purchases/expenses", idsOf("expense"))
      : Promise.resolve(new Map<string, Expense>()),
    canViewAccounting
      ? recordsById<JournalEntry>("accounting/journals", idsOf("journal_entry"))
      : Promise.resolve(new Map<string, JournalEntry>()),
    // Transfers have no detail endpoint; one page of the list resolves numbers.
    matchRows.some((match) => match.bank_transfer)
      ? tryServer(() => serverApi.list<BankTransfer>("bank-transfers", { query: { page_size: 200 } }))
      : Promise.resolve(null),
    bankAccount && canViewAccounting
      ? tryServer(() => serverApi.get<Account>(`accounting/accounts/${bankAccount.account}`))
      : Promise.resolve(null),
  ]);
  const transferById = new Map((transfers?.ok ? transfers.data.results : []).map((row) => [row.id, row]));
  const ledgerLabel = ledger?.ok ? `${ledger.data.code} · ${ledger.data.name}` : "the bank's ledger account";

  function counterpart(match: BankTransactionMatch): { label: string; href: string | null } {
    const kind = COUNTERPART_TYPE_LABELS[match.counterpart_type] ?? match.counterpart_type;
    if (match.customer_payment) {
      const payment = customerPayments.get(match.customer_payment);
      return { label: payment ? `${kind} ${payment.payment_number}` : match.reason || kind, href: `/sales/payments/${match.customer_payment}` };
    }
    if (match.vendor_payment) {
      const payment = vendorPayments.get(match.vendor_payment);
      return { label: payment ? `${kind} ${payment.payment_number}` : match.reason || kind, href: `/purchases/payments/${match.vendor_payment}` };
    }
    if (match.expense) {
      const expense = expenses.get(match.expense);
      return { label: expense ? `${kind} ${expense.expense_number}` : match.reason || kind, href: `/purchases/expenses/${match.expense}` };
    }
    if (match.bank_transfer) {
      const transfer = transferById.get(match.bank_transfer);
      return { label: transfer ? `${kind} ${transfer.transfer_number}` : match.reason || kind, href: "/banking/transfers" };
    }
    if (match.journal_entry) {
      const journal = journals.get(match.journal_entry);
      return {
        label: journal ? `${kind} ${journal.journal_number}` : kind,
        href: canViewAccounting ? `/accounting/journals/${match.journal_entry}` : null,
      };
    }
    return { label: match.reason || kind, href: null };
  }

  function documentCell(match: BankTransactionMatch): React.ReactNode {
    const { label, href } = counterpart(match);
    return (
      <span>
        {href ? (
          <Link href={appHref(href)} className="text-brand-700 hover:underline">
            {label}
          </Link>
        ) : (
          label
        )}
        {match.match_type === "categorization" && match.reason ? (
          <span className="block text-xs text-ink-500">{match.reason}</span>
        ) : null}
      </span>
    );
  }

  const title = line.description || "Bank transaction";
  const selfHref = `/banking/transactions/${line.id}`;
  const signedMoney = <Money value={line.is_inflow ? line.amount : unsigned(line.amount)} {...(currency ? { currency } : {})} />;

  const confirmedColumns: Column<BankTransactionMatch>[] = [
    { key: "document", header: "Explained by", cell: documentCell },
    { key: "match_type", header: "How", hideBelow: "md", cell: (match) => MATCH_TYPE_LABELS[match.match_type] ?? match.match_type },
    {
      key: "confirmed_at",
      header: "Confirmed",
      hideBelow: "lg",
      cell: (match) => formatDateTime(match.confirmed_at, { timeZone: session.timeZone }),
    },
    {
      key: "amount",
      header: "Amount",
      numeric: true,
      cell: (match) => <Money value={match.amount} {...(currency ? { currency } : {})} />,
    },
    ...(canAct
      ? [
          {
            key: "actions",
            header: "Actions",
            headerLabel: "Actions",
            cell: (match: BankTransactionMatch) =>
              match.match_type === "categorization" ? (
                <DocumentAction
                  resource="bank-matches"
                  id={match.id}
                  action="uncategorize"
                  label="Uncategorize"
                  size="sm"
                  confirmTitle="Uncategorize this transaction?"
                  confirmMessage={
                    <>
                      <p>
                        Posts a reversing journal dated today for <Money value={match.amount} {...(currency ? { currency } : {})} />{" "}
                        and removes the categorization. The original journal stays on record.
                      </p>
                      <p className="mt-2">The statement line becomes open again.</p>
                    </>
                  }
                  successTitle="Categorization reversed"
                />
              ) : (
                <RemoveMatchButton
                  matchId={match.id}
                  label="Unmatch"
                  confirmTitle="Remove this match?"
                  confirmMessage={
                    <p>
                      The link between this statement line and {counterpart(match).label} is removed. The document and its
                      journal are untouched and nothing is posted. The line becomes open again.
                    </p>
                  }
                />
              ),
          },
        ]
      : []),
  ];

  const suggestionColumns: Column<BankTransactionMatch>[] = [
    { key: "document", header: "Suggested document", cell: documentCell },
    { key: "match_type", header: "Why", hideBelow: "md", cell: (match) => MATCH_TYPE_LABELS[match.match_type] ?? match.match_type },
    {
      key: "source",
      header: "Source",
      hideBelow: "lg",
      cell: (match) =>
        match.suggestion_source === "ai" ? (
          <Badge tone="warning" size="sm">
            {SUGGESTION_SOURCE_LABELS.ai}
          </Badge>
        ) : (
          SUGGESTION_SOURCE_LABELS[match.suggestion_source] ?? match.suggestion_source
        ),
    },
    {
      key: "confidence",
      header: "Score",
      numeric: true,
      cell: (match) => <span className="tabular">{confidencePercent(match.confidence)}</span>,
    },
    {
      key: "amount",
      header: "Amount",
      numeric: true,
      cell: (match) => <Money value={match.amount} {...(currency ? { currency } : {})} />,
    },
    ...(canAct
      ? [
          {
            key: "actions",
            header: "Actions",
            headerLabel: "Actions",
            cell: (match: BankTransactionMatch) => (
              <span className="flex flex-wrap justify-end gap-1.5">
                <DocumentAction
                  resource="bank-matches"
                  id={match.id}
                  action="confirm"
                  label="Confirm"
                  size="sm"
                  variant="primary"
                  confirmTitle="Confirm this match?"
                  confirmMessage={
                    <>
                      <p>
                        Records that this statement line is {counterpart(match).label}, for{" "}
                        <Money value={match.amount} {...(currency ? { currency } : {})} />.
                      </p>
                      <p className="mt-2">
                        Nothing is posted: the document already posted its own journal. The server refuses if the document is
                        already accounted for on another line.
                      </p>
                      {match.suggestion_source === "ai" ? (
                        <p className="mt-2 font-medium">This was suggested by AI. Check the document before confirming.</p>
                      ) : null}
                    </>
                  }
                  successTitle="Match confirmed"
                />
                <RemoveMatchButton
                  matchId={match.id}
                  label="Dismiss"
                  confirmTitle="Dismiss this suggestion?"
                  confirmMessage={<p>The suggestion is deleted. Nothing is posted and the document is untouched.</p>}
                />
              </span>
            ),
          },
        ]
      : []),
  ];

  const candidateRows = candidates?.ok ? candidates.data : [];
  const canRecordTransfer = roleHasPermission(role, PERMISSIONS.RECORD_BANK_TRANSFER);
  const candidateColumns: Column<TransferCandidate>[] = [
    { key: "account", header: "Other account", cell: (row) => row.bank_account_name },
    { key: "date", header: "Date", cell: (row) => <span className="tabular whitespace-nowrap">{formatDate(row.transaction_date)}</span> },
    { key: "description", header: "Description", hideBelow: "md", cell: (row) => row.description || "—" },
    {
      key: "amount",
      header: "Amount",
      numeric: true,
      cell: (row) => <Money value={row.amount} {...(currency ? { currency } : {})} />,
    },
    ...(canRecordTransfer && canAct && bankAccount
      ? [
          {
            key: "actions",
            header: "Actions",
            headerLabel: "Actions",
            cell: (row: TransferCandidate) => (
              <ConfirmTransferPairButton
                outflowTransactionId={line.is_inflow ? row.id : line.id}
                inflowTransactionId={line.is_inflow ? line.id : row.id}
                amount={unsigned(line.amount)}
                currency={bankAccount.currency}
                fromName={line.is_inflow ? row.bank_account_name : bankAccount.name}
                toName={line.is_inflow ? bankAccount.name : row.bank_account_name}
                transferDate={line.is_inflow ? row.transaction_date : line.transaction_date}
              />
            ),
          },
        ]
      : []),
  ];

  return (
    <>
      <PageHeader
        title={title}
        breadcrumbs={[
          { label: "Bank transactions", href: "/banking/transactions" },
          ...(bankAccount ? [{ label: bankAccount.name, href: `/banking/transactions?bank_account=${bankAccount.id}` }] : []),
          { label: formatDate(line.transaction_date) },
        ]}
        meta={<StatusBadge status={line.status} map={BANK_TRANSACTION_STATUS} />}
        description={`${line.is_inflow ? "Money in" : "Money out"} on ${formatDate(line.transaction_date)}${bankAccount ? ` · ${bankAccount.name}` : ""}`}
        actions={
          canAct && line.status === "excluded" ? (
            <DocumentAction
              resource="bank-transactions"
              id={line.id}
              action="restore"
              label="Restore"
              variant="primary"
              confirmTitle="Restore this transaction?"
              confirmMessage={
                <p>
                  The line returns to the work queue as unmatched and counts toward the statement balance again. Nothing is
                  posted.
                </p>
              }
              successTitle="Transaction restored"
            />
          ) : null
        }
      />

      <PageBody>
        {locked ? (
          <div role="status" className="rounded-md border border-info-100 bg-info-50 px-3 py-2 text-sm text-info-600">
            This line belongs to a completed reconciliation, so it cannot be changed.{" "}
            {line.reconciliation ? (
              <Link href={`/banking/reconciliation/${line.reconciliation}`} className="font-medium underline">
                Reopen the reconciliation
              </Link>
            ) : null}{" "}
            with a reason to correct it.
          </div>
        ) : null}

        <StatGrid columns={3}>
          <StatCard label={line.is_inflow ? "Money in" : "Money out"} value={signedMoney} hint={bankAccount?.name} />
          <StatCard
            label="Status"
            value={<StatusBadge status={line.status} map={BANK_TRANSACTION_STATUS} />}
            hint={
              line.status === "suggested"
                ? "Has suggestions or only part of it is explained"
                : line.status === "matched"
                  ? "Fully explained — counts toward the cleared balance"
                  : line.status === "excluded"
                    ? "Not a real transaction — left out of the statement balance"
                    : "Needs a decision"
            }
          />
          <StatCard
            label="Confirmed matches"
            value={matches ? (matches.ok ? confirmed.length : "—") : "—"}
            hint={matches ? "Each amount is shown below" : "Visible to roles that can reconcile"}
          />
        </StatGrid>

        <Card>
          <CardHeader title="Statement line" description="As the bank reported it. These fields never change after import." />
          <CardBody>
            <DetailList
              columns={3}
              items={[
                { label: "Date", value: formatDate(line.transaction_date) },
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
                { label: "Amount", value: <Money value={line.amount} {...(currency ? { currency } : {})} /> },
                { label: "Description", value: line.description || "—", span: true },
                { label: "Counterparty", value: line.counterparty_name || "—" },
                { label: "Bank reference", value: line.bank_reference || "—" },
                { label: "Bank transaction id", value: line.external_id || "—" },
                { label: "Source", value: line.statement_import ? "Statement import" : "Added by hand" },
                ...(line.status === "excluded" ? [{ label: "Excluded because", value: line.excluded_reason || "—", span: true }] : []),
                ...(line.reconciliation
                  ? [
                      {
                        label: "Reconciliation",
                        value: (
                          <Link href={`/banking/reconciliation/${line.reconciliation}`} className="text-brand-700 hover:underline">
                            {reconciliation?.ok ? (
                              <StatusBadge status={reconciliation.data.status} map={BANK_RECONCILIATION_STATUS} size="sm" />
                            ) : (
                              "View reconciliation"
                            )}
                          </Link>
                        ),
                      },
                    ]
                  : []),
              ]}
            />
          </CardBody>
        </Card>

        {canAct && isOpen && bankAccount ? (
          <Card>
            <CardHeader
              title="Explain this line"
              description="Match it to a payment, expense or transfer already recorded, or categorize it when no document exists. Only categorizing posts a journal."
            />
            <CardBody className="flex flex-wrap gap-2">
              <CategorizeButton
                transactionId={line.id}
                amount={line.amount}
                currency={bankAccount.currency}
                isInflow={line.is_inflow}
                transactionDate={line.transaction_date}
                bankLedgerLabel={ledgerLabel}
                hasConfirmedMatches={confirmed.length > 0}
              />
              <CreateMatchButton
                transactionId={line.id}
                bankAccountId={bankAccount.id}
                ledgerAccountId={bankAccount.account}
                isInflow={line.is_inflow}
                amount={line.amount}
                currency={bankAccount.currency}
              />
              <FindSuggestionsButton transactionId={line.id} />
              <AutoMatchButton transactionId={line.id} />
              <ApplyRulesButton transactionId={line.id} />
              {confirmed.length === 0 ? (
                <DocumentAction
                  resource="bank-transactions"
                  id={line.id}
                  action="exclude"
                  label="Exclude"
                  variant="danger"
                  confirmTitle="Exclude this transaction?"
                  confirmMessage={
                    <>
                      <p>
                        Only for a line that is <strong>not a real transaction</strong> — a duplicate the bank emitted, a line that
                        belongs to someone else. An excluded line is left out of the statement balance.
                      </p>
                      <p className="mt-2">
                        If money really moved and no document exists, categorize it instead; excluding it would hide a genuine
                        difference. Nothing is posted, and the line can be restored.
                      </p>
                    </>
                  }
                  reason={{ label: "Reason", required: true, hint: "Required. Kept on the line for the audit trail." }}
                  successTitle="Transaction excluded"
                />
              ) : null}
            </CardBody>
          </Card>
        ) : null}

        {matches ? (
          <Section
            title="Confirmed matches"
            description="What explains this line. The line is matched once these cover its full amount."
          >
            <DataTable
              caption={`Confirmed matches for ${title}`}
              columns={confirmedColumns}
              data={matches.ok ? wholeList(confirmed) : undefined}
              error={matches.ok ? null : (matches.error as ApiError)}
              getRowId={(match) => match.id}
              emptyTitle="Nothing confirmed yet"
              emptyDescription={isOpen ? "Confirm a suggestion, match a document or categorize the line." : undefined}
              page={1}
              pageSize={Math.max(confirmed.length, 1)}
              buildPageHref={() => selfHref}
            />
          </Section>
        ) : null}

        {matches?.ok && (suggestions.length > 0 || isOpen) ? (
          <Section
            title="Suggestions"
            description="Unconfirmed. Scored by exact amount, how close the dates are, and whether the document's reference or party appears in the narration."
          >
            <DataTable
              caption={`Suggested matches for ${title}`}
              columns={suggestionColumns}
              data={wholeList(suggestions)}
              getRowId={(match) => match.id}
              emptyTitle="No suggestions"
              emptyDescription={canAct ? "Use Find suggestions to search for documents with this exact amount." : undefined}
              page={1}
              pageSize={Math.max(suggestions.length, 1)}
              buildPageHref={() => selfHref}
            />
          </Section>
        ) : null}

        {candidates && (candidateRows.length > 0 || !candidates.ok) ? (
          <Section
            title="Possible transfer between your accounts"
            description="Opposite lines of the same amount on another of your accounts within five days. Confirming records one transfer for both."
          >
            <DataTable
              caption={`Possible transfer legs for ${title}`}
              columns={candidateColumns}
              data={candidates.ok ? wholeList(candidateRows) : undefined}
              error={candidates.ok ? null : (candidates.error as ApiError)}
              getRowId={(row) => row.id}
              getRowHref={(row) => `/banking/transactions/${row.id}`}
              page={1}
              pageSize={Math.max(candidateRows.length, 1)}
              buildPageHref={() => selfHref}
            />
          </Section>
        ) : null}
      </PageBody>
    </>
  );
}
