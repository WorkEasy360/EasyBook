import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { DataTable, type Column } from "@/components/ui/data-table";
import { FilterBar, SortNote } from "@/components/ui/filter-bar";
import { LinkButton } from "@/components/ui/link-button";
import { Badge } from "@/components/ui/badge";
import { RECURRING_FREQUENCY_LABELS } from "@/components/ui/status-badge";
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
import type { Customer, RecurringInvoiceTemplate } from "@/types/api/sales";

export const metadata: Metadata = { title: "Recurring invoices" };

const RESOURCE = "sales/recurring-invoices";

export default async function RecurringInvoicesPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_RECURRING_INVOICES)) {
    return (
      <>
        <PageHeader title="Recurring invoices" />
        <ForbiddenState resource="recurring invoices" />
      </>
    );
  }

  const canManage = roleHasPermission(session.role, PERMISSIONS.MANAGE_RECURRING_INVOICES);
  const query = parseListQuery("/sales/recurring-invoices", RESOURCE, params);
  const result = await tryServer(() => serverApi.list<RecurringInvoiceTemplate>(RESOURCE, { query: query.apiParams }));

  const customerIds = result.ok ? result.data.results.map((row) => row.customer) : [];
  if (query.filters["customer"]) customerIds.push(query.filters["customer"]);
  const customers = roleHasPermission(session.role, PERMISSIONS.VIEW_CUSTOMERS)
    ? await indexList<Customer>("sales/customers", customerIds)
    : new Map<string, Customer>();

  const columns: Column<RecurringInvoiceTemplate>[] = [
    {
      key: "customer",
      header: "Customer",
      cell: (row) => customers.get(row.customer)?.display_name ?? "Customer",
    },
    {
      key: "frequency",
      header: "Repeats",
      cell: (row) => RECURRING_FREQUENCY_LABELS[row.frequency] ?? row.frequency,
    },
    {
      key: "reference",
      header: "Reference",
      hideBelow: "lg",
      cell: (row) => row.reference || <span className="text-ink-400">—</span>,
    },
    {
      key: "next_run_at",
      header: "Next invoice",
      cell: (row) =>
        row.end_date && row.next_run_at > row.end_date ? (
          <span className="text-ink-500">Ended</span>
        ) : (
          <span className="tabular whitespace-nowrap">{formatDate(row.next_run_at)}</span>
        ),
    },
    {
      key: "end_date",
      header: "Ends",
      hideBelow: "md",
      cell: (row) =>
        row.end_date ? <span className="tabular whitespace-nowrap">{formatDate(row.end_date)}</span> : "No end date",
    },
    { key: "lines", header: "Lines", numeric: true, hideBelow: "sm", cell: (row) => row.lines.length },
    {
      key: "is_active",
      header: "Status",
      cell: (row) => (
        <Badge tone={row.is_active ? "success" : "neutral"} size="sm" marker={row.is_active}>
          {row.is_active ? "Active" : "Inactive"}
        </Badge>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Recurring invoices"
        description="Active templates create a draft invoice on each scheduled date. Drafts are posted like any other invoice."
        actions={
          canManage ? (
            <LinkButton href="/sales/recurring-invoices/new" variant="primary">
              New recurring invoice
            </LinkButton>
          ) : null
        }
      />
      <PageBody>
        <FilterBar
          groups={[
            {
              key: "is_active",
              label: "Status",
              value: query.filters["is_active"] ?? "",
              options: [
                { value: "true", label: "Active" },
                { value: "false", label: "Inactive" },
              ],
            },
            ...customerFilterGroups(query, customers),
          ]}
          buildFilterHref={query.buildFilterHref}
          clearHref={query.clearHref}
          activeFilterCount={query.activeFilterCount}
        />
        <DataTable
          caption="Recurring invoice templates"
          columns={columns}
          data={result.ok ? result.data : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          getRowId={(row) => row.id}
          getRowHref={(row) => `/sales/recurring-invoices/${row.id}`}
          emptyTitle={query.activeFilterCount > 0 ? "No templates match these filters" : "No recurring invoices yet"}
          emptyDescription="Set up a template to bill a customer the same lines on a schedule."
          {...(canManage ? { emptyAction: { label: "New recurring invoice", href: "/sales/recurring-invoices/new" } } : {})}
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={query.buildPageHref}
          note={<SortNote description={capabilitiesFor(RESOURCE).defaultOrder ?? "next run date"} />}
        />
      </PageBody>
    </>
  );
}
