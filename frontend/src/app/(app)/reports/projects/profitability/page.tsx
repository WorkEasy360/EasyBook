import type { Metadata } from "next";
import { DataTable, type Column } from "@/components/ui/data-table";
import { FilterBar } from "@/components/ui/filter-bar";
import { Money, Quantity } from "@/components/ui/money";
import {
  BILLING_METHOD_LABELS,
  PROJECT_STATUS,
  StatusBadge,
  statusOptions,
} from "@/components/ui/status-badge";
import { serverApi, tryServer } from "@/lib/api/server";
import { csvExportHref } from "@/lib/api/capabilities";
import type { ApiError } from "@/lib/api/errors";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { formatPercent } from "@/lib/money";
import { paramOf, wholeList, type RawSearchParams } from "@/lib/list-query";
import { reportEntry } from "@/features/reports/catalogue";
import { hrefWith, reportTimestamp } from "@/features/reports/period";
import { ReportForbidden, ReportShell } from "@/features/reports/report-shell";
import type { ProjectProfitabilityRow, RowsEnvelope } from "@/types/api/reports";

const PATH = "/reports/projects/profitability";
const ENTRY = reportEntry(PATH);

export const metadata: Metadata = { title: ENTRY.title };

/**
 * Revenue, cost and margin per project, lifetime to date, straight from
 * projects.selectors.get_project_profitability. Gated on VIEW_ALL_TIMESHEETS
 * (not VIEW_PROJECTS) because margin exposes labour cost. No date filter and
 * no organization-wide total exist on this endpoint.
 */
export default async function ProjectProfitabilityPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_ALL_TIMESHEETS)) {
    return <ReportForbidden title={ENTRY.title} resource="project profitability" />;
  }

  const rawStatus = paramOf(params, "status");
  const status = rawStatus && rawStatus in PROJECT_STATUS ? rawStatus : undefined;
  const query = { status };

  const result = await tryServer(() =>
    serverApi.get<RowsEnvelope<ProjectProfitabilityRow>>("reports/projects/profitability", { query }),
  );
  const rows = result.ok ? result.data.rows : [];

  const columns: Column<ProjectProfitabilityRow>[] = [
    {
      key: "name",
      header: "Project",
      cell: (row) => (
        <>
          <span className="tabular mr-2 text-ink-500">{row.project_code}</span>
          {row.name}
        </>
      ),
    },
    {
      key: "status",
      header: "Status",
      hideBelow: "sm",
      cell: (row) => <StatusBadge status={row.status} map={PROJECT_STATUS} size="sm" />,
    },
    {
      key: "billing_method",
      header: "Billing",
      hideBelow: "lg",
      cell: (row) => BILLING_METHOD_LABELS[row.billing_method] ?? row.billing_method,
    },
    { key: "revenue", header: "Revenue", numeric: true, cell: (row) => <Money value={row.revenue} /> },
    {
      key: "unbilled_value",
      header: "Unbilled",
      numeric: true,
      hideBelow: "lg",
      cell: (row) => <Money value={row.unbilled_value} />,
    },
    {
      key: "labour_cost",
      header: "Labour cost",
      numeric: true,
      hideBelow: "lg",
      cell: (row) => <Money value={row.labour_cost} />,
    },
    {
      key: "expense_cost",
      header: "Expense cost",
      numeric: true,
      hideBelow: "lg",
      cell: (row) => <Money value={row.expense_cost} />,
    },
    {
      key: "total_cost",
      header: "Total cost",
      numeric: true,
      hideBelow: "md",
      cell: (row) => <Money value={row.total_cost} />,
    },
    { key: "margin", header: "Margin", numeric: true, cell: (row) => <Money value={row.margin} strong /> },
    {
      key: "margin_percent",
      header: "Margin %",
      numeric: true,
      hideBelow: "sm",
      // Null when there is no revenue to take a percentage of.
      cell: (row) =>
        row.margin_percent === null ? (
          <span className="text-ink-500">n/a</span>
        ) : (
          <span className="tabular">{formatPercent(row.margin_percent)}</span>
        ),
    },
    {
      key: "total_hours",
      header: "Hours",
      numeric: true,
      hideBelow: "md",
      cell: (row) => (
        <span title={`${row.billable_hours} billable, ${row.non_billable_hours} non-billable`}>
          <Quantity value={row.total_hours} />
        </span>
      ),
    },
  ];

  return (
    <ReportShell
      title={ENTRY.title}
      description="Lifetime to date — this report has no date filter."
      csvHref={csvExportHref("projects/profitability", query)}
      generatedAt={result.ok ? reportTimestamp() : null}
      timeZone={session.timeZone}
    >
      <FilterBar
        groups={[
          { key: "status", label: "Status", value: status ?? "", options: statusOptions(PROJECT_STATUS) },
        ]}
        buildFilterHref={(key, value) => hrefWith(PATH, { [key]: value ?? undefined })}
        clearHref={PATH}
        activeFilterCount={status ? 1 : 0}
      />
      <DataTable
        caption="Project profitability"
        columns={columns}
        data={result.ok ? wholeList(rows) : undefined}
        error={result.ok ? null : (result.error as ApiError)}
        getRowId={(row) => row.project_id}
        getRowHref={(row) => `/projects/${row.project_id}`}
        emptyTitle={status ? "No projects with this status" : "No projects yet"}
        emptyDescription="Projects appear here once created; revenue and cost follow invoices, time and expenses."
        page={1}
        pageSize={Math.max(rows.length, 1)}
        buildPageHref={() => PATH}
        note="Sorted by project code."
      />
    </ReportShell>
  );
}
