import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { DetailList, PageBody, Section } from "@/components/ui/detail";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable, type Column } from "@/components/ui/data-table";
import { LinkButton } from "@/components/ui/link-button";
import { AUTOMATION_EXECUTION_STATUS, AUTOMATION_RULE_STATUS, StatusBadge } from "@/components/ui/status-badge";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { DocumentAction } from "@/features/shared/document-action";
import { RunRuleDialog } from "@/features/automation/run-rule-dialog";
import { ActionSettings, ConditionsTable } from "@/features/automation/rule-summary";
import {
  TRIGGERS_WITHOUT_DISPATCH,
  actionLabel,
  conditionFieldsFor,
  entityLabelFor,
  isUuid,
  triggerLabel,
} from "@/features/automation/contract";
import { loadCatalogs, loadMembers, memberName } from "@/features/automation/server";
import { serverApi, tryServer } from "@/lib/api/server";
import { recordsById } from "@/lib/api/lookups";
import { wholeList } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import { formatDateTime } from "@/lib/datetime";
import type { Item } from "@/types/api/items";
import type { Vendor } from "@/types/api/purchases";
import type { Customer } from "@/types/api/sales";
import {
  TRIGGER_CATEGORY_LABELS,
  TRIGGER_SOURCE_LABELS,
  type AutomationExecution,
  type AutomationRule,
} from "@/types/api/automation";

export const metadata: Metadata = { title: "Automation rule" };

const RECENT_RUNS = 10;

export default async function AutomationRuleDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.VIEW_AUTOMATION)) {
    return (
      <>
        <PageHeader title="Automation rule" />
        <ForbiddenState resource="automation" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<AutomationRule>(`automation/rules/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Automation rule" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  const rule = result.data;
  const canViewHistory = roleHasPermission(role, PERMISSIONS.VIEW_AUTOMATION_HISTORY);

  // Id-valued conditions (customer_id, vendor_id, item_id) are shown by name
  // when the role can read that record type.
  const fields = conditionFieldsFor(rule.trigger_type) ?? {};
  const idsFor = (picker: string) =>
    rule.conditions
      .filter((condition) => fields[condition.field]?.picker === picker)
      .flatMap((condition) => condition.value.split(",").map((value) => value.trim()))
      .filter(isUuid);

  const [[triggers, actions], members, executions, customers, vendors, items] = await Promise.all([
    loadCatalogs(),
    loadMembers(),
    canViewHistory
      ? tryServer(() =>
          serverApi.list<AutomationExecution>("automation/executions", { query: { rule: rule.id, page_size: RECENT_RUNS } }),
        )
      : Promise.resolve(null),
    roleHasPermission(role, PERMISSIONS.VIEW_CUSTOMERS)
      ? recordsById<Customer>("sales/customers", idsFor("customer"))
      : Promise.resolve(new Map<string, Customer>()),
    roleHasPermission(role, PERMISSIONS.VIEW_VENDORS)
      ? recordsById<Vendor>("purchases/vendors", idsFor("vendor"))
      : Promise.resolve(new Map<string, Vendor>()),
    roleHasPermission(role, PERMISSIONS.VIEW_ITEMS)
      ? recordsById<Item>("items", idsFor("item"))
      : Promise.resolve(new Map<string, Item>()),
  ]);

  const triggerCatalog = triggers.ok ? triggers.data : [];
  const actionCatalog = actions.ok ? actions.data : [];
  const trigger = triggerCatalog.find((entry) => entry.id === rule.trigger_type);
  const recordNames = new Map<string, string>([
    ...[...customers.values()].map((row) => [row.id, row.display_name] as [string, string]),
    ...[...vendors.values()].map((row) => [row.id, row.display_name] as [string, string]),
    ...[...items.values()].map((row) => [row.id, row.name] as [string, string]),
  ]);

  const status = rule.status;
  const isArchived = status === "archived";
  const title = rule.name;

  const runColumns: Column<AutomationExecution>[] = [
    {
      key: "created",
      header: "Started",
      cell: (row) => (
        <span className="tabular whitespace-nowrap">{formatDateTime(row.created_at, { timeZone: session.timeZone })}</span>
      ),
    },
    { key: "source", header: "Source", hideBelow: "sm", cell: (row) => TRIGGER_SOURCE_LABELS[row.trigger_source] ?? row.trigger_source },
    { key: "status", header: "Status", cell: (row) => <StatusBadge status={row.status} map={AUTOMATION_EXECUTION_STATUS} size="sm" /> },
    { key: "version", header: "Version", numeric: true, hideBelow: "md", cell: (row) => <span className="tabular">v{row.rule_version}</span> },
  ];

  return (
    <>
      <PageHeader
        title={title}
        breadcrumbs={[{ label: "Automation", href: "/automation" }, { label: title }]}
        meta={<StatusBadge status={status} map={AUTOMATION_RULE_STATUS} />}
        description={rule.description || undefined}
        actions={
          <>
            {!isArchived && roleHasPermission(role, PERMISSIONS.EDIT_AUTOMATION) ? (
              <LinkButton href={`/automation/rules/${rule.id}/edit`}>Edit</LinkButton>
            ) : null}
            {!isArchived && roleHasPermission(role, PERMISSIONS.EDIT_AUTOMATION) ? (
              <DocumentAction
                resource="automation/rules"
                id={rule.id}
                action="archive"
                label="Archive"
                variant="danger"
                confirmTitle={`Archive ${title}?`}
                confirmMessage={
                  <p>
                    An archived rule never runs again and can no longer be edited or reactivated. Its run history is
                    kept. This cannot be undone.
                  </p>
                }
                successTitle="Rule archived"
              />
            ) : null}
            {status === "active" && roleHasPermission(role, PERMISSIONS.DISABLE_AUTOMATION) ? (
              <DocumentAction
                resource="automation/rules"
                id={rule.id}
                action="pause"
                label="Pause"
                confirmTitle={`Pause ${title}?`}
                confirmMessage={<p>The rule stops running until it is activated again. Runs already queued still finish.</p>}
                successTitle="Rule paused"
              />
            ) : null}
            {status === "active" && roleHasPermission(role, PERMISSIONS.RUN_AUTOMATION) ? (
              <RunRuleDialog ruleId={rule.id} ruleName={title} entityLabel={entityLabelFor(rule.trigger_type)} />
            ) : null}
            {(status === "draft" || status === "paused") && roleHasPermission(role, PERMISSIONS.ENABLE_AUTOMATION) ? (
              <DocumentAction
                resource="automation/rules"
                id={rule.id}
                action="activate"
                label="Activate"
                variant="primary"
                confirmTitle={`Activate ${title}?`}
                confirmMessage={
                  <>
                    <p>
                      Once active, the rule runs every time its trigger fires
                      {trigger ? ` (${trigger.label.toLowerCase()})` : ""} and its conditions match: notifications are
                      sent and webhooks are called automatically.
                    </p>
                    <p className="mt-2">The whole rule is checked again before it is activated.</p>
                  </>
                }
                successTitle="Rule activated"
              />
            ) : null}
          </>
        }
      />

      <PageBody>
        {TRIGGERS_WITHOUT_DISPATCH.has(rule.trigger_type) ? (
          <p role="note" className="rounded-md border border-warning-100 bg-warning-50 px-3 py-2 text-sm text-warning-700">
            Nothing fires this trigger yet, so this rule does not run by itself even when active. It can still be run by
            hand.
          </p>
        ) : null}

        {!triggers.ok || !actions.ok ? (
          <ErrorState
            compact
            title="Could not load the automation catalog"
            message="Trigger and action names are shown as identifiers."
            reference={referenceOf(!triggers.ok ? triggers.error : !actions.ok ? actions.error : null)}
          />
        ) : null}

        <Card>
          <CardHeader title="Details" />
          <CardBody>
            <DetailList
              items={[
                { label: "Trigger", value: triggerLabel(triggerCatalog, rule.trigger_type) },
                {
                  label: "Trigger type",
                  value: trigger ? (TRIGGER_CATEGORY_LABELS[trigger.category] ?? trigger.category) : "—",
                },
                { label: "Version", value: <span className="tabular">v{rule.version}</span> },
                { label: "Priority", value: <span className="tabular">{rule.priority}</span> },
                { label: "On a failed action", value: rule.stop_on_failure ? "Stop the run" : "Continue with the next action" },
                {
                  label: "Most runs per 24 hours",
                  value: rule.max_runs_per_period === null ? "No limit" : <span className="tabular">{rule.max_runs_per_period}</span>,
                },
                {
                  label: "Cooldown",
                  value:
                    rule.cooldown_days === null
                      ? "Default"
                      : `${rule.cooldown_days} ${rule.cooldown_days === 1 ? "day" : "days"}`,
                },
                { label: "Created", value: formatDateTime(rule.created_at, { timeZone: session.timeZone }) },
                { label: "Last changed", value: formatDateTime(rule.updated_at, { timeZone: session.timeZone }) },
              ]}
            />
          </CardBody>
        </Card>

        <Section title="Conditions" description="Every condition must match for the actions to run.">
          <ConditionsTable
            conditions={rule.conditions}
            triggerType={rule.trigger_type}
            recordNames={recordNames}
            caption={`Conditions of ${title}`}
            selfHref={`/automation/rules/${rule.id}`}
          />
        </Section>

        <Section
          title="Actions"
          description={rule.stop_on_failure ? "Run in order; the run stops at the first failure." : "Run in order."}
        >
          <ol className="flex flex-col gap-3">
            {rule.actions.map((action, index) => (
              <li key={`${action.action_id}-${index}`}>
                <Card>
                  <CardHeader as="h3" title={`${index + 1}. ${actionLabel(actionCatalog, action.action_id)}`} />
                  <CardBody>
                    <ActionSettings
                      action={action}
                      catalog={actionCatalog}
                      memberName={(userId) => memberName(members, userId)}
                    />
                  </CardBody>
                </Card>
              </li>
            ))}
          </ol>
        </Section>

        {executions ? (
          <Section
            title="Recent runs"
            actions={
              <LinkButton href={`/automation/executions?rule=${rule.id}`} size="sm" variant="ghost">
                All runs
              </LinkButton>
            }
          >
            <DataTable
              caption={`Recent runs of ${title}`}
              columns={runColumns}
              data={executions.ok ? wholeList(executions.data.results) : undefined}
              error={executions.ok ? null : (executions.error as ApiError)}
              getRowId={(row) => row.id}
              getRowHref={(row) => `/automation/executions/${row.id}`}
              emptyTitle="This rule has not run yet"
              emptyDescription={status === "active" ? "Runs appear here when the trigger fires or the rule is run by hand." : "Only an active rule runs."}
              page={1}
              pageSize={RECENT_RUNS}
              buildPageHref={() => `/automation/rules/${rule.id}`}
            />
          </Section>
        ) : null}
      </PageBody>
    </>
  );
}
