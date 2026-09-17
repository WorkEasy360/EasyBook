import { appHref } from "@/lib/routes";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { DetailList, PageBody, Section } from "@/components/ui/detail";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable, TableFooterRow, type Column } from "@/components/ui/data-table";
import { LinkButton } from "@/components/ui/link-button";
import { Money } from "@/components/ui/money";
import { PrintButton } from "@/components/ui/print-button";
import { JOURNAL_STATUS, StatusBadge } from "@/components/ui/status-badge";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { DocumentAction } from "@/features/shared/document-action";
import { ReverseJournalDialog } from "@/features/accounting/reverse-journal-dialog";
import { journalSourceOf } from "@/features/accounting/journal-source";
import { estimateJournalTotals } from "@/features/accounting/journal-math";
import { serverApi, tryServer } from "@/lib/api/server";
import { accountLabel, recordsById } from "@/lib/api/lookups";
import { wholeList } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import { formatDate, formatDateTime } from "@/lib/datetime";
import type { Account, JournalEntry, JournalLine } from "@/types/api/accounting";

export const metadata: Metadata = { title: "Journal" };

export default async function JournalDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.VIEW_TRANSACTIONS)) {
    return (
      <>
        <PageHeader title="Journal" />
        <ForbiddenState resource="journals" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<JournalEntry>(`accounting/journals/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Journal" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  const journal = result.data;
  const isDraft = journal.status === "draft";
  const isPosted = journal.status === "posted";
  const canManage = roleHasPermission(role, PERMISSIONS.MANAGE_TRANSACTIONS);
  const canViewAccounts = roleHasPermission(role, PERMISSIONS.VIEW_ACCOUNTING);

  const [accounts, reversal] = await Promise.all([
    canViewAccounts
      ? recordsById<Account>("accounting/accounts", journal.lines.map((line) => line.account))
      : Promise.resolve(new Map<string, Account>()),
    // The original carries no pointer to its reversal (only the reversal
    // points back through `reverses`), and the list cannot filter on it, so
    // one page of posted journals is scanned. Not finding it only loses the link.
    journal.status === "reversed"
      ? tryServer(() =>
          serverApi.list<JournalEntry>("accounting/journals", { query: { status: "posted", page_size: 200 } }),
        )
      : Promise.resolve(null),
  ]);

  const reversedBy = reversal?.ok ? reversal.data.results.find((row) => row.reverses === journal.id) : undefined;
  const source = journalSourceOf(journal.source_type, journal.source_id);
  const title = journal.journal_number || "Draft journal";
  // Display-only sums of the lines on screen, labelled as such. For a posted
  // journal the engine already refused anything unbalanced; for a draft this
  // is the same estimate the form showed.
  const lineTotals = estimateJournalTotals(journal.lines);

  const columns: Column<JournalLine>[] = [
    { key: "line_number", header: "#", width: "3rem", cell: (line) => line.line_number },
    {
      key: "account",
      header: "Account",
      cell: (line) =>
        canViewAccounts ? (
          <Link href={`/accounting/accounts/${line.account}`} className="text-brand-700 hover:underline">
            {accounts.has(line.account) ? accountLabel(accounts, line.account) : "View account"}
          </Link>
        ) : (
          "Account"
        ),
    },
    {
      key: "description",
      header: "Description",
      // Not hidden on narrow screens: the sums footer spans columns by position.
      cell: (line) => line.description || <span className="text-ink-400">—</span>,
    },
    {
      key: "debit",
      header: "Debit",
      numeric: true,
      cell: (line) => <Money value={line.debit} currency={journal.currency} hideSymbol />,
    },
    {
      key: "credit",
      header: "Credit",
      numeric: true,
      cell: (line) => <Money value={line.credit} currency={journal.currency} hideSymbol />,
    },
  ];

  return (
    <>
      <PageHeader
        title={title}
        breadcrumbs={[{ label: "Journals", href: "/accounting/journals" }, { label: title }]}
        meta={<StatusBadge status={journal.status} map={JOURNAL_STATUS} />}
        description={journal.memo || undefined}
        actions={
          <>
            <PrintButton />
            {isDraft && canManage ? (
              <LinkButton href={`/accounting/journals/${journal.id}/edit`}>Edit</LinkButton>
            ) : null}
            {isPosted && canManage ? <ReverseJournalDialog journal={journal} /> : null}
            {isDraft && canManage ? (
              <DocumentAction
                resource="accounting/journals"
                id={journal.id}
                action="post"
                label="Post journal"
                variant="primary"
                confirmTitle="Post this journal?"
                confirmMessage={
                  <>
                    <p>
                      Posting writes {journal.lines.length} lines to the general ledger and assigns the journal its
                      number. A posted journal cannot be edited or deleted — only reversed.
                    </p>
                    <p className="mt-2">
                      The ledger refuses it unless total debits equal total credits and the posting date falls in an
                      open fiscal year.
                    </p>
                  </>
                }
                successTitle="Journal posted"
              />
            ) : null}
          </>
        }
      />

      <PageBody className="print-document">
        <Card>
          <CardHeader title="Details" />
          <CardBody>
            <DetailList
              columns={3}
              items={[
                { label: "Posting date", value: formatDate(journal.posting_date) },
                { label: "Reference", value: journal.reference || "—" },
                { label: "Currency", value: journal.currency },
                { label: "Exchange rate", value: <span className="tabular">{journal.exchange_rate}</span> },
                {
                  label: "Source",
                  value: source ? (
                    source.href ? (
                      <Link href={appHref(source.href)} className="text-brand-700 hover:underline">
                        {source.label}
                      </Link>
                    ) : (
                      source.label
                    )
                  ) : (
                    "Manual journal"
                  ),
                },
                ...(journal.reverses
                  ? [
                      {
                        label: "Reverses",
                        value: (
                          <Link href={`/accounting/journals/${journal.reverses}`} className="text-brand-700 hover:underline">
                            Original journal
                          </Link>
                        ),
                      },
                    ]
                  : []),
                ...(journal.status === "reversed"
                  ? [
                      {
                        label: "Reversed by",
                        value: reversedBy ? (
                          <Link href={`/accounting/journals/${reversedBy.id}`} className="tabular text-brand-700 hover:underline">
                            {reversedBy.journal_number}
                          </Link>
                        ) : (
                          "A reversing journal"
                        ),
                      },
                    ]
                  : []),
                {
                  label: "Posted",
                  value: journal.posted_at
                    ? formatDateTime(journal.posted_at, { timeZone: session.timeZone })
                    : "Not posted",
                },
                { label: "Created", value: formatDateTime(journal.created_at, { timeZone: session.timeZone }) },
                ...(journal.memo ? [{ label: "Memo", value: <span className="whitespace-pre-line">{journal.memo}</span>, span: true }] : []),
              ]}
            />
          </CardBody>
        </Card>

        <Section
          title="Lines"
          description={
            isDraft
              ? "Line sums below are an estimate; posting re-checks that debits equal credits."
              : "Posted lines are permanent. To correct them, reverse this journal and record a new one."
          }
        >
          <DataTable
            caption={`Lines of ${title}`}
            columns={columns}
            data={wholeList(journal.lines)}
            getRowId={(line) => line.id}
            emptyTitle="No lines"
            page={1}
            pageSize={Math.max(journal.lines.length, 1)}
            buildPageHref={() => `/accounting/journals/${journal.id}`}
            footer={
              journal.lines.length > 0 ? (
                <TableFooterRow
                  label={isDraft ? "Sum of lines (estimate)" : "Sum of lines"}
                  columnCount={columns.length}
                  values={[
                    { key: "debit", node: <Money value={lineTotals.debit} currency={journal.currency} strong /> },
                    { key: "credit", node: <Money value={lineTotals.credit} currency={journal.currency} strong /> },
                  ]}
                />
              ) : undefined
            }
          />
          {isDraft && !lineTotals.balanced ? (
            <p className="text-xs font-medium text-warning-700">
              These lines do not balance, so posting will be refused until the draft is corrected.
            </p>
          ) : null}
        </Section>
      </PageBody>
    </>
  );
}
