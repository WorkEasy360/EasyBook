import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { DetailTabs, PageBody } from "@/components/ui/detail";
import { DataTable, type Column } from "@/components/ui/data-table";
import { FilterBar, SortNote } from "@/components/ui/filter-bar";
import { AUTOMATION_EXECUTION_STATUS, StatusBadge, statusOptions } from "@/components/ui/status-badge";
import { ForbiddenState } from "@/components/ui/states";
import { automationTabs } from "@/features/automation/tabs";
import { isUuid } from "@/features/automation/contract";
import { serverApi, tryServer } from "@/lib/api/server";
import { indexList } from "@/lib/api/lookups";
import { capabilitiesFor } from "@/lib/api/capabilities";
import { parseListQuery, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { formatDateTime } from "@/lib/datetime";
import type { ApiError } from "@/lib/api/errors";
import { TRIGGER_SOURCE_LABELS, type AutomationExecution, type AutomationRule } from "@/types/api/automation";

export const metadata: Metadata = { title: "Automation run history" };

export default async function AutomationExecutionsPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const raw = await searchParams;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.VIEW_AUTOMATION_HISTORY)) {
    return (
      <>
        <PageHeader title="Run history" />
        <ForbiddenState resource="automation run history" />
      </>
    );
  }

  // ?rule=<not a uuid> makes the API answer 500 (the view filters a UUID
  // column with the raw string), so a malformed id is dropped here.
  const params = { ...raw };
  const ruleParam = Array.isArray(params["rule"]) ? params["rule"][0] : params["rule"];
  if (ruleParam !== undefined && !isUuid(ruleParam)) delete params["rule"];

  const query = parseListQuery("/automation/executions", "automation/executions", params);
  const result = await tryServer(() => serverApi.list<AutomationExecution>("automation/executions", { query: query.apiParams }));

  const ruleIds = result.ok ? result.data.results.map((row) => row.rule) : [];
  const ruleFilter = query.filters["rule"];
  if (ruleFilter) ruleIds.push(ruleFilter);
  const rules = roleHasPermission(role, PERMISSIONS.VIEW_AUTOMATION)
    ? await indexList<AutomationRule>("automation/rules", ruleIds)
    : new Map<string, AutomationRule>();

  const columns: Column<AutomationExecution>[] = [
    {
      key: "created",
      header: "Started",
      cell: (row) => (
        <span className="tabular whitespace-nowrap">{formatDateTime(row.created_at, { timeZone: session.timeZone })}</span>
      ),
    },
    {
      key: "rule",
      header: "Rule",
      cell: (row) => rules.get(row.rule)?.name ?? <span className="text-ink-400">Rule</span>,
    },
    {
      key: "source",
      header: "Source",
      hideBelow: "sm",
      cell: (row) => TRIGGER_SOURCE_LABELS[row.trigger_source] ?? row.trigger_source,
    },
    {
      key: "status",
      header: "Status",
      cell: (row) => <StatusBadge status={row.status} map={AUTOMATION_EXECUTION_STATUS} size="sm" />,
    },
    {
      key: "steps",
      header: "Steps done",
      numeric: true,
      hideBelow: "md",
      cell: (row) =>
        row.steps.length === 0 ? (
          <span className="text-ink-400">—</span>
        ) : (
          <span className="tabular">
            {row.steps.filter((step) => step.status === "succeeded").length} of {row.steps.length}
          </span>
        ),
    },
    {
      key: "finished",
      header: "Finished",
      hideBelow: "lg",
      cell: (row) => (
        <span className="tabular whitespace-nowrap">{formatDateTime(row.finished_at, { timeZone: session.timeZone })}</span>
      ),
    },
  ];

  const filteredRule = ruleFilter ? rules.get(ruleFilter) : undefined;

  return (
    <>
      <PageHeader
        title="Run history"
        breadcrumbs={[{ label: "Automation", href: "/automation" }, { label: "Run history" }]}
        description="Every run records the rule version it used, so later edits never change what a past run did."
      />
      <DetailTabs tabs={automationTabs} />
      <PageBody>
        <FilterBar
          groups={[
            {
              key: "status",
              label: "Status",
              value: query.filters["status"] ?? "",
              options: statusOptions(AUTOMATION_EXECUTION_STATUS),
            },
            ...(ruleFilter
              ? [
                  {
                    key: "rule",
                    label: "Rule",
                    value: ruleFilter,
                    options: [{ value: ruleFilter, label: filteredRule?.name ?? "Selected rule" }],
                  },
                ]
              : []),
          ]}
          buildFilterHref={query.buildFilterHref}
          clearHref={query.clearHref}
          activeFilterCount={query.activeFilterCount}
        />
        <DataTable
          caption="Automation runs"
          columns={columns}
          data={result.ok ? result.data : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          getRowId={(row) => row.id}
          getRowHref={(row) => `/automation/executions/${row.id}`}
          emptyTitle={query.activeFilterCount > 0 ? "No runs match these filters" : "No automation runs yet"}
          emptyDescription="Runs appear when an active rule's trigger fires or a rule is run by hand."
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={query.buildPageHref}
          note={<SortNote description={capabilitiesFor("automation/executions").defaultOrder ?? "newest first"} />}
        />
      </PageBody>
    </>
  );
}
