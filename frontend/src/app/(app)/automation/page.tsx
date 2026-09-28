import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { DetailTabs, PageBody } from "@/components/ui/detail";
import { DataTable, type Column } from "@/components/ui/data-table";
import { FilterBar, SortNote } from "@/components/ui/filter-bar";
import { LinkButton } from "@/components/ui/link-button";
import { AUTOMATION_RULE_STATUS, StatusBadge, statusOptions } from "@/components/ui/status-badge";
import { ForbiddenState } from "@/components/ui/states";
import { automationTabs } from "@/features/automation/tabs";
import { triggerLabel } from "@/features/automation/contract";
import { loadCatalogs } from "@/features/automation/server";
import { serverApi, tryServer } from "@/lib/api/server";
import { capabilitiesFor } from "@/lib/api/capabilities";
import { parseListQuery, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { formatDateTime } from "@/lib/datetime";
import type { ApiError } from "@/lib/api/errors";
import type { AutomationRule } from "@/types/api/automation";

export const metadata: Metadata = { title: "Automation" };

export default async function AutomationRulesPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.VIEW_AUTOMATION)) {
    return (
      <>
        <PageHeader title="Automation" />
        <ForbiddenState resource="automation" />
      </>
    );
  }

  const canCreate = roleHasPermission(role, PERMISSIONS.CREATE_AUTOMATION);
  const query = parseListQuery("/automation", "automation/rules", params);
  const [result, [triggers]] = await Promise.all([
    tryServer(() => serverApi.list<AutomationRule>("automation/rules", { query: query.apiParams })),
    loadCatalogs(),
  ]);
  const triggerCatalog = triggers.ok ? triggers.data : [];

  // The filter offers the catalog's triggers; if the catalog failed, the
  // active filter is still shown so it can be cleared.
  const triggerOptions = triggerCatalog.map((trigger) => ({ value: trigger.id, label: trigger.label }));
  const activeTrigger = query.filters["trigger_type"];
  if (activeTrigger && !triggerOptions.some((option) => option.value === activeTrigger)) {
    triggerOptions.push({ value: activeTrigger, label: activeTrigger });
  }

  const columns: Column<AutomationRule>[] = [
    {
      key: "name",
      header: "Rule",
      cell: (row) => (
        <span className="flex flex-col">
          <span className="font-medium text-ink-900">{row.name}</span>
          {row.description ? <span className="line-clamp-1 text-xs text-ink-500">{row.description}</span> : null}
        </span>
      ),
    },
    { key: "trigger", header: "Trigger", hideBelow: "sm", cell: (row) => triggerLabel(triggerCatalog, row.trigger_type) },
    {
      key: "status",
      header: "Status",
      cell: (row) => <StatusBadge status={row.status} map={AUTOMATION_RULE_STATUS} size="sm" />,
    },
    {
      key: "actions",
      header: "Actions",
      numeric: true,
      hideBelow: "md",
      cell: (row) => <span className="tabular">{row.actions.length}</span>,
    },
    { key: "priority", header: "Priority", numeric: true, hideBelow: "md", cell: (row) => <span className="tabular">{row.priority}</span> },
    { key: "version", header: "Version", numeric: true, hideBelow: "lg", cell: (row) => <span className="tabular">v{row.version}</span> },
    {
      key: "updated",
      header: "Updated",
      hideBelow: "lg",
      cell: (row) => (
        <span className="tabular whitespace-nowrap">{formatDateTime(row.updated_at, { timeZone: session.timeZone })}</span>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Automation"
        description="Rules run actions when something happens, on a schedule, or on demand. Only active rules run, and no rule can post to the ledger, move stock or send money."
        actions={
          canCreate ? (
            <LinkButton href="/automation/rules/new" variant="primary">
              New rule
            </LinkButton>
          ) : null
        }
      />
      {roleHasPermission(role, PERMISSIONS.VIEW_AUTOMATION_HISTORY) ? <DetailTabs tabs={automationTabs} /> : null}
      <PageBody>
        <FilterBar
          groups={[
            {
              key: "status",
              label: "Status",
              value: query.filters["status"] ?? "",
              options: statusOptions(AUTOMATION_RULE_STATUS),
            },
            {
              key: "trigger_type",
              label: "Trigger",
              value: activeTrigger ?? "",
              options: triggerOptions,
            },
          ]}
          buildFilterHref={query.buildFilterHref}
          clearHref={query.clearHref}
          activeFilterCount={query.activeFilterCount}
        />
        <DataTable
          caption="Automation rules"
          columns={columns}
          data={result.ok ? result.data : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          getRowId={(row) => row.id}
          getRowHref={(row) => `/automation/rules/${row.id}`}
          emptyTitle={query.activeFilterCount > 0 ? "No rules match these filters" : "No automation rules yet"}
          emptyDescription="A rule is saved as a draft and only runs once it is activated."
          {...(canCreate ? { emptyAction: { label: "New rule", href: "/automation/rules/new" } } : {})}
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={query.buildPageHref}
          note={<SortNote description={capabilitiesFor("automation/rules").defaultOrder ?? "priority, then name"} />}
        />
      </PageBody>
    </>
  );
}
