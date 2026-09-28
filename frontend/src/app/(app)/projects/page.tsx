import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { DataTable, type Column } from "@/components/ui/data-table";
import { FilterBar, SortNote } from "@/components/ui/filter-bar";
import { LinkButton } from "@/components/ui/link-button";
import { Money } from "@/components/ui/money";
import { BILLING_METHOD_LABELS, PROJECT_STATUS, StatusBadge, statusOptions } from "@/components/ui/status-badge";
import { ForbiddenState } from "@/components/ui/states";
import { serverApi, tryServer } from "@/lib/api/server";
import { indexList } from "@/lib/api/lookups";
import { capabilitiesFor } from "@/lib/api/capabilities";
import { parseListQuery, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { formatDate } from "@/lib/datetime";
import type { ApiError } from "@/lib/api/errors";
import type { Customer } from "@/types/api/sales";
import type { Project } from "@/types/api/projects";

export const metadata: Metadata = { title: "Projects" };

export default async function ProjectsPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.VIEW_PROJECTS)) {
    return (
      <>
        <PageHeader title="Projects" />
        <ForbiddenState resource="projects" />
      </>
    );
  }

  const canCreate = roleHasPermission(role, PERMISSIONS.MANAGE_PROJECTS);
  const canLogTime = roleHasPermission(role, PERMISSIONS.LOG_TIME);
  const query = parseListQuery("/projects", "projects", params);
  const result = await tryServer(() => serverApi.list<Project>("projects", { query: query.apiParams }));

  const customerIds = result.ok ? result.data.results.map((row) => row.customer) : [];
  if (query.filters["customer"]) customerIds.push(query.filters["customer"]);
  const customers = roleHasPermission(role, PERMISSIONS.VIEW_CUSTOMERS)
    ? await indexList<Customer>("sales/customers", customerIds)
    : new Map<string, Customer>();
  const filteredCustomer = query.filters["customer"] ? customers.get(query.filters["customer"]) : undefined;

  const columns: Column<Project>[] = [
    { key: "project_code", header: "Code", cell: (row) => <span className="tabular">{row.project_code}</span> },
    { key: "name", header: "Name", cell: (row) => <span className="font-medium text-ink-900">{row.name}</span> },
    {
      key: "customer",
      header: "Customer",
      hideBelow: "sm",
      cell: (row) => customers.get(row.customer)?.display_name ?? <span className="text-ink-400">Customer</span>,
    },
    {
      key: "billing_method",
      header: "Billing",
      hideBelow: "md",
      cell: (row) => BILLING_METHOD_LABELS[row.billing_method] ?? row.billing_method,
    },
    {
      key: "dates",
      header: "Dates",
      hideBelow: "lg",
      cell: (row) =>
        row.start_date || row.end_date ? (
          <span className="tabular whitespace-nowrap">
            {formatDate(row.start_date)} – {row.end_date ? formatDate(row.end_date) : "open"}
          </span>
        ) : (
          <span className="text-ink-400">—</span>
        ),
    },
    {
      key: "budget_amount",
      header: "Budget",
      numeric: true,
      hideBelow: "md",
      cell: (row) => <Money value={row.budget_amount} currency={row.currency} />,
    },
    { key: "status", header: "Status", cell: (row) => <StatusBadge status={row.status} map={PROJECT_STATUS} size="sm" /> },
  ];

  return (
    <>
      <PageHeader
        title="Projects"
        description="Track work for customers. Projects post nothing to the ledger; approved billable time becomes a draft invoice."
        actions={
          <>
            {canLogTime || roleHasPermission(role, PERMISSIONS.VIEW_ALL_TIMESHEETS) ? (
              <LinkButton href="/projects/timesheets">Timesheets</LinkButton>
            ) : null}
            {canCreate ? (
              <LinkButton href="/projects/new" variant="primary">
                New project
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
              options: statusOptions(PROJECT_STATUS),
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
          caption="Projects"
          columns={columns}
          data={result.ok ? result.data : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          getRowId={(row) => row.id}
          getRowHref={(row) => `/projects/${row.id}`}
          emptyTitle={query.activeFilterCount > 0 ? "No projects match these filters" : "No projects yet"}
          emptyDescription="Create a project to track time and bill it to a customer."
          {...(canCreate ? { emptyAction: { label: "New project", href: "/projects/new" } } : {})}
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={query.buildPageHref}
          note={<SortNote description={capabilitiesFor("projects").defaultOrder ?? "name"} />}
        />
      </PageBody>
    </>
  );
}
