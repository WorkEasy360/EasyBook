import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { DataTable, type Column } from "@/components/ui/data-table";
import { FilterBar, SortNote } from "@/components/ui/filter-bar";
import { LinkButton } from "@/components/ui/link-button";
import { Money } from "@/components/ui/money";
import { PAYMENT_METHOD_LABELS } from "@/components/ui/status-badge";
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
import type { Customer, CustomerPayment } from "@/types/api/sales";

export const metadata: Metadata = { title: "Payments received" };

const RESOURCE = "sales/payments";

export default async function PaymentsPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_PAYMENTS)) {
    return (
      <>
        <PageHeader title="Payments received" />
        <ForbiddenState resource="payments" />
      </>
    );
  }

  const canRecord = roleHasPermission(session.role, PERMISSIONS.RECORD_PAYMENT);
  const query = parseListQuery("/sales/payments", RESOURCE, params);
  const result = await tryServer(() => serverApi.list<CustomerPayment>(RESOURCE, { query: query.apiParams }));

  const customerIds = result.ok ? result.data.results.map((row) => row.customer) : [];
  if (query.filters["customer"]) customerIds.push(query.filters["customer"]);
  const customers = roleHasPermission(session.role, PERMISSIONS.VIEW_CUSTOMERS)
    ? await indexList<Customer>("sales/customers", customerIds)
    : new Map<string, Customer>();

  const newHref = query.filters["customer"] ? `/sales/payments/new?customer=${query.filters["customer"]}` : "/sales/payments/new";

  const columns: Column<CustomerPayment>[] = [
    { key: "payment_number", header: "Payment", cell: (row) => <span className="tabular">{row.payment_number}</span> },
    {
      key: "customer",
      header: "Customer",
      cell: (row) => customers.get(row.customer)?.display_name ?? <span className="text-ink-400">Customer</span>,
    },
    {
      key: "payment_date",
      header: "Date",
      hideBelow: "sm",
      cell: (row) => <span className="tabular whitespace-nowrap">{formatDate(row.payment_date)}</span>,
    },
    {
      key: "method",
      header: "Method",
      hideBelow: "md",
      cell: (row) => PAYMENT_METHOD_LABELS[row.payment_method] ?? row.payment_method,
    },
    {
      key: "reference",
      header: "Reference",
      hideBelow: "lg",
      cell: (row) => row.reference || <span className="text-ink-400">—</span>,
    },
    {
      key: "unapplied",
      header: "Unapplied",
      numeric: true,
      hideBelow: "md",
      // The backend writes at most one allocation without an invoice: the
      // unapplied remainder. Its amount is shown as returned, not computed.
      cell: (row) => {
        const unapplied = row.allocations.find((allocation) => allocation.invoice === null);
        return unapplied ? <Money value={unapplied.amount} currency={row.currency} /> : <span className="text-ink-400">—</span>;
      },
    },
    {
      key: "amount",
      header: "Amount",
      numeric: true,
      cell: (row) => <Money value={row.amount} currency={row.currency} strong />,
    },
  ];

  return (
    <>
      <PageHeader
        title="Payments received"
        description="Recording a payment posts it to the ledger immediately and settles the invoices it is applied to."
        actions={
          canRecord ? (
            <LinkButton href={newHref} variant="primary">
              Record payment
            </LinkButton>
          ) : null
        }
      />
      <PageBody>
        {query.filters["customer"] ? (
          <FilterBar
            groups={customerFilterGroups(query, customers)}
            buildFilterHref={query.buildFilterHref}
            clearHref={query.clearHref}
            activeFilterCount={query.activeFilterCount}
          />
        ) : null}
        <DataTable
          caption="Payments received"
          columns={columns}
          data={result.ok ? result.data : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          getRowId={(row) => row.id}
          getRowHref={(row) => `/sales/payments/${row.id}`}
          emptyTitle={query.activeFilterCount > 0 ? "No payments from this customer" : "No payments recorded yet"}
          emptyDescription="Record a payment when a customer pays one or more invoices."
          {...(canRecord ? { emptyAction: { label: "Record payment", href: newHref } } : {})}
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={query.buildPageHref}
          note={<SortNote description={capabilitiesFor(RESOURCE).defaultOrder ?? "newest first"} />}
        />
      </PageBody>
    </>
  );
}
