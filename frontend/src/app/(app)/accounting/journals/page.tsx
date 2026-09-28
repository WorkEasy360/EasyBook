import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { DataTable, type Column } from "@/components/ui/data-table";
import { FilterBar, SortNote } from "@/components/ui/filter-bar";
import { LinkButton } from "@/components/ui/link-button";
import { JOURNAL_STATUS, StatusBadge, statusOptions } from "@/components/ui/status-badge";
import { ForbiddenState } from "@/components/ui/states";
import { journalSourceOf } from "@/features/accounting/journal-source";
import { serverApi, tryServer } from "@/lib/api/server";
import { capabilitiesFor } from "@/lib/api/capabilities";
import { parseListQuery, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { formatDate } from "@/lib/datetime";
import type { ApiError } from "@/lib/api/errors";
import type { JournalEntry } from "@/types/api/accounting";

export const metadata: Metadata = { title: "Journals" };

/**
 * Every journal entry: manual ones and those the engines raised from
 * documents. The list endpoint reads `status` only — it ignores any date
 * range — so no date filter is offered here.
 *
 * No amount column: the serializer carries no total, and a sum computed here
 * would be a figure the ledger never stated. The detail page shows the lines.
 */
export default async function JournalsPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  // The journal endpoints are gated on VIEW_TRANSACTIONS, not VIEW_ACCOUNTING.
  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_TRANSACTIONS)) {
    return (
      <>
        <PageHeader title="Journals" />
        <ForbiddenState resource="journals" />
      </>
    );
  }

  const canCreate = roleHasPermission(session.role, PERMISSIONS.MANAGE_TRANSACTIONS);
  const query = parseListQuery("/accounting/journals", "accounting/journals", params);
  const result = await tryServer(() => serverApi.list<JournalEntry>("accounting/journals", { query: query.apiParams }));

  const columns: Column<JournalEntry>[] = [
    {
      key: "journal_number",
      header: "Journal",
      cell: (row) =>
        row.journal_number ? <span className="tabular">{row.journal_number}</span> : <span className="italic">Draft</span>,
    },
    {
      key: "posting_date",
      header: "Date",
      cell: (row) => <span className="tabular whitespace-nowrap">{formatDate(row.posting_date)}</span>,
    },
    {
      key: "memo",
      header: "Memo",
      cell: (row) => (
        <span className="line-clamp-1 max-w-md">{row.memo || <span className="text-ink-400">No memo</span>}</span>
      ),
    },
    {
      key: "source",
      header: "Source",
      hideBelow: "md",
      cell: (row) => {
        if (row.reverses) return "Reversal";
        return journalSourceOf(row.source_type, row.source_id)?.label ?? "Manual";
      },
    },
    {
      key: "reference",
      header: "Reference",
      hideBelow: "lg",
      cell: (row) => row.reference || <span className="text-ink-400">—</span>,
    },
    {
      key: "lines",
      header: "Lines",
      numeric: true,
      hideBelow: "sm",
      cell: (row) => row.lines.length,
    },
    {
      key: "status",
      header: "Status",
      cell: (row) => <StatusBadge status={row.status} map={JOURNAL_STATUS} size="sm" />,
    },
  ];

  return (
    <>
      <PageHeader
        title="Journals"
        description="Drafts can be edited freely. Posting a journal writes it to the ledger, assigns its number and makes it permanent; corrections are reversals."
        actions={
          <>
            <LinkButton href="/accounting/general-ledger">General ledger</LinkButton>
            {canCreate ? (
              <LinkButton href="/accounting/journals/new" variant="primary">
                New journal
              </LinkButton>
            ) : null}
          </>
        }
      />
      <PageBody>
        <FilterBar
          groups={[
            {
              key: "status",
              label: "Status",
              value: query.filters["status"] ?? "",
              options: statusOptions(JOURNAL_STATUS),
            },
          ]}
          buildFilterHref={query.buildFilterHref}
          clearHref={query.clearHref}
          activeFilterCount={query.activeFilterCount}
        />
        <DataTable
          caption="Journals"
          columns={columns}
          data={result.ok ? result.data : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          getRowId={(row) => row.id}
          getRowHref={(row) => `/accounting/journals/${row.id}`}
          emptyTitle={query.activeFilterCount > 0 ? "No journals match this filter" : "No journals yet"}
          emptyDescription="Journals appear when documents are posted, or when you record a manual journal."
          {...(canCreate ? { emptyAction: { label: "New journal", href: "/accounting/journals/new" } } : {})}
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={query.buildPageHref}
          note={<SortNote description={capabilitiesFor("accounting/journals").defaultOrder ?? "posting date"} />}
        />
      </PageBody>
    </>
  );
}
