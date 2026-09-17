import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { DataTable, type Column } from "@/components/ui/data-table";
import { FilterBar, SortNote } from "@/components/ui/filter-bar";
import { LinkButton } from "@/components/ui/link-button";
import { Money } from "@/components/ui/money";
import {
  CREDIT_NOTE_REASON_LABELS,
  CREDIT_NOTE_STATUS,
  StatusBadge,
  statusOptions,
} from "@/components/ui/status-badge";
import { ForbiddenState } from "@/components/ui/states";
import { customerFilterGroups } from "@/features/sales/list-helpers";
import { serverApi, tryServer } from "@/lib/api/server";
import { indexList } from "@/lib/api/lookups";
import { capabilitiesFor } from "@/lib/api/capabilities";
import { parseListQuery, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { formatDate } from "@/lib/datetime";
import type { ApiError } from "@/lib/api/errors";
import type { CreditNote, Customer } from "@/types/api/sales";

export const metadata: Metadata = { title: "Credit notes" };

const RESOURCE = "sales/credit-notes";

export default async function CreditNotesPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_CREDIT_NOTES)) {
    return (
      <>
        <PageHeader title="Credit notes" />
        <ForbiddenState resource="credit notes" />
      </>
    );
  }

  const canIssue = roleHasPermission(session.role, PERMISSIONS.ISSUE_CREDIT_NOTE);
  const query = parseListQuery("/sales/credit-notes", RESOURCE, params);
  const result = await tryServer(() => serverApi.list<CreditNote>(RESOURCE, { query: query.apiParams }));

  const customerIds = result.ok ? result.data.results.map((row) => row.customer) : [];
  if (query.filters["customer"]) customerIds.push(query.filters["customer"]);
  const customers = roleHasPermission(session.role, PERMISSIONS.VIEW_CUSTOMERS)
    ? await indexList<Customer>("sales/customers", customerIds)
    : new Map<string, Customer>();

  const columns: Column<CreditNote>[] = [
    {
      key: "credit_note_number",
      header: "Credit note",
      // Numbered only on issue (issue_credit_note allocates CN-…).
      cell: (row) =>
        row.credit_note_number ? <span className="tabular">{row.credit_note_number}</span> : <span className="italic">Draft</span>,
    },
    {
      key: "customer",
      header: "Customer",
      cell: (row) => customers.get(row.customer)?.display_name ?? <span className="text-ink-400">Customer</span>,
    },
    {
      key: "credit_note_date",
      header: "Date",
      hideBelow: "sm",
      cell: (row) => <span className="tabular whitespace-nowrap">{formatDate(row.credit_note_date)}</span>,
    },
    {
      key: "reason",
      header: "Reason",
      hideBelow: "md",
      cell: (row) => CREDIT_NOTE_REASON_LABELS[row.reason] ?? row.reason,
    },
    {
      key: "status",
      header: "Status",
      cell: (row) => <StatusBadge status={row.status} map={CREDIT_NOTE_STATUS} size="sm" />,
    },
    {
      key: "total",
      header: "Total",
      numeric: true,
      cell: (row) => <Money value={row.total} currency={row.currency} strong />,
    },
  ];

  return (
    <>
      <PageHeader
        title="Credit notes"
        description="A draft posts nothing. Issuing a credit note reverses revenue and tax in the ledger and reduces the invoice's balance due."
        actions={
          canIssue ? (
            <LinkButton href="/sales/credit-notes/new" variant="primary">
              New credit note
            </LinkButton>
          ) : null
        }
      />
      <PageBody>
        <FilterBar
          groups={[
            {
              key: "status",
              label: "Status",
              value: query.filters["status"] ?? "",
              options: statusOptions(CREDIT_NOTE_STATUS),
            },
            ...customerFilterGroups(query, customers),
          ]}
          buildFilterHref={query.buildFilterHref}
          clearHref={query.clearHref}
          activeFilterCount={query.activeFilterCount}
        />
        <DataTable
          caption="Credit notes"
          columns={columns}
          data={result.ok ? result.data : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          getRowId={(row) => row.id}
          getRowHref={(row) => `/sales/credit-notes/${row.id}`}
          emptyTitle={query.activeFilterCount > 0 ? "No credit notes match these filters" : "No credit notes yet"}
          emptyDescription="Raise a credit note from a posted invoice to credit a return or a correction."
          {...(canIssue ? { emptyAction: { label: "New credit note", href: "/sales/credit-notes/new" } } : {})}
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={query.buildPageHref}
          note={<SortNote description={capabilitiesFor(RESOURCE).defaultOrder ?? "newest first"} />}
        />
      </PageBody>
    </>
  );
}
