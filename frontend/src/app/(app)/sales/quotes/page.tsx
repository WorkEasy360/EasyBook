import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { DataTable, type Column } from "@/components/ui/data-table";
import { FilterBar, SortNote } from "@/components/ui/filter-bar";
import { LinkButton } from "@/components/ui/link-button";
import { Money } from "@/components/ui/money";
import { QUOTE_STATUS, StatusBadge, statusOptions } from "@/components/ui/status-badge";
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
import type { Customer, Quote } from "@/types/api/sales";

export const metadata: Metadata = { title: "Quotes" };

const RESOURCE = "sales/quotes";

export default async function QuotesPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_QUOTES)) {
    return (
      <>
        <PageHeader title="Quotes" />
        <ForbiddenState resource="quotes" />
      </>
    );
  }

  const canManage = roleHasPermission(session.role, PERMISSIONS.MANAGE_QUOTES);
  const query = parseListQuery("/sales/quotes", RESOURCE, params);
  const result = await tryServer(() => serverApi.list<Quote>(RESOURCE, { query: query.apiParams }));

  const customerIds = result.ok ? result.data.results.map((row) => row.customer) : [];
  if (query.filters["customer"]) customerIds.push(query.filters["customer"]);
  const customers = roleHasPermission(session.role, PERMISSIONS.VIEW_CUSTOMERS)
    ? await indexList<Customer>("sales/customers", customerIds)
    : new Map<string, Customer>();

  const columns: Column<Quote>[] = [
    { key: "quote_number", header: "Quote", cell: (row) => <span className="tabular">{row.quote_number}</span> },
    {
      key: "customer",
      header: "Customer",
      cell: (row) => customers.get(row.customer)?.display_name ?? <span className="text-ink-400">Customer</span>,
    },
    {
      key: "issue_date",
      header: "Date",
      hideBelow: "sm",
      cell: (row) => <span className="tabular whitespace-nowrap">{formatDate(row.issue_date)}</span>,
    },
    {
      key: "expiry_date",
      header: "Expires",
      hideBelow: "md",
      cell: (row) =>
        row.expiry_date ? (
          <span className="tabular whitespace-nowrap">{formatDate(row.expiry_date)}</span>
        ) : (
          <span className="text-ink-400">—</span>
        ),
    },
    { key: "status", header: "Status", cell: (row) => <StatusBadge status={row.status} map={QUOTE_STATUS} size="sm" /> },
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
        title="Quotes"
        description="A quote posts nothing. Once the customer accepts it, convert it into a sales order or an invoice."
        actions={
          canManage ? (
            <LinkButton href="/sales/quotes/new" variant="primary">
              New quote
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
              options: statusOptions(QUOTE_STATUS),
            },
            ...customerFilterGroups(query, customers),
          ]}
          buildFilterHref={query.buildFilterHref}
          clearHref={query.clearHref}
          activeFilterCount={query.activeFilterCount}
        />
        <DataTable
          caption="Quotes"
          columns={columns}
          data={result.ok ? result.data : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          getRowId={(row) => row.id}
          getRowHref={(row) => `/sales/quotes/${row.id}`}
          emptyTitle={query.activeFilterCount > 0 ? "No quotes match these filters" : "No quotes yet"}
          emptyDescription="Create a quote to offer prices to a customer before they commit."
          {...(canManage ? { emptyAction: { label: "New quote", href: "/sales/quotes/new" } } : {})}
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={query.buildPageHref}
          note={<SortNote description={capabilitiesFor(RESOURCE).defaultOrder ?? "newest first"} />}
        />
      </PageBody>
    </>
  );
}
