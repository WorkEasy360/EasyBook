import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { DataTable, type Column } from "@/components/ui/data-table";
import { FilterBar, SortNote } from "@/components/ui/filter-bar";
import { LinkButton } from "@/components/ui/link-button";
import { Money } from "@/components/ui/money";
import { OverdueHint } from "@/features/documents/overdue-hint";
import { StatusBadge, INVOICE_STATUS, statusOptions } from "@/components/ui/status-badge";
import { ForbiddenState } from "@/components/ui/states";
import { serverApi, tryServer } from "@/lib/api/server";
import { indexList } from "@/lib/api/lookups";
import { parseListQuery, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { formatDate, todayInZone } from "@/lib/datetime";
import type { ApiError } from "@/lib/api/errors";
import type { Customer, Invoice } from "@/types/api/sales";

export const metadata: Metadata = { title: "Invoices" };

/** "overdue" is never stored (see InvoiceStatus), so it is not offered as a filter. */
const FILTERABLE_STATUS = Object.fromEntries(
  Object.entries(INVOICE_STATUS).filter(([value]) => value !== "overdue"),
);

export default async function InvoicesPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_INVOICES)) {
    return (
      <>
        <PageHeader title="Invoices" />
        <ForbiddenState resource="invoices" />
      </>
    );
  }

  const canCreate = roleHasPermission(session.role, PERMISSIONS.CREATE_INVOICE);
  const query = parseListQuery("/sales/invoices", "sales/invoices", params);
  const result = await tryServer(() => serverApi.list<Invoice>("sales/invoices", { query: query.apiParams }));

  const customerIds = result.ok ? result.data.results.map((row) => row.customer) : [];
  if (query.filters["customer"]) customerIds.push(query.filters["customer"]);
  const customers = roleHasPermission(session.role, PERMISSIONS.VIEW_CUSTOMERS)
    ? await indexList<Customer>("sales/customers", customerIds)
    : new Map<string, Customer>();

  const today = todayInZone(session.timeZone);
  const filteredCustomer = query.filters["customer"] ? customers.get(query.filters["customer"]) : undefined;

  const columns: Column<Invoice>[] = [
    {
      key: "invoice_number",
      header: "Invoice",
      cell: (row) =>
        row.invoice_number ? <span className="tabular">{row.invoice_number}</span> : <span className="italic">Draft</span>,
    },
    {
      key: "customer",
      header: "Customer",
      cell: (row) => customers.get(row.customer)?.display_name ?? <span className="text-ink-400">Customer</span>,
    },
    {
      key: "invoice_date",
      header: "Date",
      hideBelow: "sm",
      cell: (row) => <span className="tabular whitespace-nowrap">{formatDate(row.invoice_date)}</span>,
    },
    {
      key: "due_date",
      header: "Due",
      hideBelow: "md",
      cell: (row) => (
        <span className="tabular whitespace-nowrap">
          {formatDate(row.due_date)}
          <OverdueHint invoice={row} today={today} />
        </span>
      ),
    },
    {
      key: "status",
      header: "Status",
      cell: (row) => <StatusBadge status={row.status} map={INVOICE_STATUS} size="sm" />,
    },
    {
      key: "total",
      header: "Total",
      numeric: true,
      hideBelow: "sm",
      cell: (row) => <Money value={row.total} currency={row.currency} />,
    },
    {
      key: "amount_due",
      header: "Balance due",
      numeric: true,
      cell: (row) =>
        row.status === "draft" || row.status === "void" ? (
          <span className="text-ink-400">—</span>
        ) : (
          <Money value={row.amount_due} currency={row.currency} strong />
        ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Invoices"
        description="Drafts can be edited freely. Posting an invoice records it in the ledger and fixes its number."
        actions={
          canCreate ? (
            <LinkButton href="/sales/invoices/new" variant="primary">
              New invoice
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
              options: statusOptions(FILTERABLE_STATUS),
            },
            ...(query.filters["customer"]
              ? [
                  {
                    key: "customer",
                    label: "Customer",
                    value: query.filters["customer"],
                    options: [
                      { value: query.filters["customer"], label: filteredCustomer?.display_name ?? "Selected customer" },
                    ],
                  },
                ]
              : []),
          ]}
          buildFilterHref={query.buildFilterHref}
          clearHref={query.clearHref}
          activeFilterCount={query.activeFilterCount}
        />
        <DataTable
          caption="Invoices"
          columns={columns}
          data={result.ok ? result.data : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          getRowId={(row) => row.id}
          getRowHref={(row) => `/sales/invoices/${row.id}`}
          emptyTitle={query.activeFilterCount > 0 ? "No invoices match these filters" : "No invoices yet"}
          emptyDescription="Create an invoice to bill a customer."
          {...(canCreate ? { emptyAction: { label: "New invoice", href: "/sales/invoices/new" } } : {})}
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={query.buildPageHref}
          note={<SortNote description="invoice date, newest first" />}
        />
      </PageBody>
    </>
  );
}
