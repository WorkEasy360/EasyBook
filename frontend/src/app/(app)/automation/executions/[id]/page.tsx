import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { DetailList, PageBody, Section } from "@/components/ui/detail";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { AUTOMATION_EXECUTION_STATUS, AUTOMATION_STEP_STATUS, StatusBadge } from "@/components/ui/status-badge";
import { EmptyState, ErrorState, ForbiddenState } from "@/components/ui/states";
import { DocumentAction } from "@/features/shared/document-action";
import { JsonBlock } from "@/features/automation/rule-summary";
import { actionLabel, redactConfig } from "@/features/automation/contract";
import { loadCatalogs, loadMembers, memberName } from "@/features/automation/server";
import { serverApi, tryServer } from "@/lib/api/server";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import { formatDateTime } from "@/lib/datetime";
import {
  FAILURE_CATEGORY_LABELS,
  TRIGGER_SOURCE_LABELS,
  type AutomationExecution,
  type AutomationRule,
} from "@/types/api/automation";

export const metadata: Metadata = { title: "Automation run" };

export default async function AutomationExecutionDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.VIEW_AUTOMATION_HISTORY)) {
    return (
      <>
        <PageHeader title="Automation run" />
        <ForbiddenState resource="automation run history" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<AutomationExecution>(`automation/executions/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Automation run" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  const execution = result.data;
  const [rule, [, actions], members] = await Promise.all([
    roleHasPermission(role, PERMISSIONS.VIEW_AUTOMATION)
      ? tryServer(() => serverApi.get<AutomationRule>(`automation/rules/${execution.rule}`))
      : Promise.resolve(null),
    loadCatalogs(),
    loadMembers(),
  ]);

  const actionCatalog = actions.ok ? actions.data : [];
  const ruleName = rule?.ok ? rule.data.name : null;
  const time = (value: string | null) => formatDateTime(value, { timeZone: session.timeZone });
  const title = ruleName ? `Run of ${ruleName}` : "Automation run";
  // retry_execution accepts only failed or partial runs (automation_execution_not_retryable).
  const canRetry =
    (execution.status === "failed" || execution.status === "partial") &&
    roleHasPermission(role, PERMISSIONS.RETRY_AUTOMATION);
  const waiting = execution.status === "pending" || execution.status === "running";

  return (
    <>
      <PageHeader
        title={title}
        breadcrumbs={[
          { label: "Automation", href: "/automation" },
          { label: "Run history", href: "/automation/executions" },
          { label: time(execution.created_at) },
        ]}
        meta={<StatusBadge status={execution.status} map={AUTOMATION_EXECUTION_STATUS} />}
        actions={
          canRetry ? (
            <DocumentAction
              resource="automation/executions"
              id={execution.id}
              action="retry"
              label="Retry failed steps"
              variant="primary"
              confirmTitle="Retry this run?"
              confirmMessage={
                <>
                  <p>
                    Steps that failed are run again with the settings this run started with. Steps that already
                    succeeded are not repeated, so no notification or webhook call is sent twice.
                  </p>
                  <p className="mt-2">The retry is queued and processed in the background.</p>
                </>
              }
              successTitle="Retry queued"
            />
          ) : null
        }
      />

      <PageBody>
        {waiting ? (
          <p role="status" className="rounded-md border border-info-100 bg-info-50 px-3 py-2 text-sm text-info-700">
            This run is {execution.status === "pending" ? "queued" : "in progress"} in the background. Refresh the page
            to see its latest status.
          </p>
        ) : null}

        <Card>
          <CardHeader title="Details" />
          <CardBody>
            <DetailList
              items={[
                {
                  label: "Rule",
                  value: (
                    <Link href={`/automation/rules/${execution.rule}`} className="text-brand-700 hover:underline">
                      {ruleName ?? "View rule"}
                    </Link>
                  ),
                },
                {
                  label: "Rule version",
                  value: (
                    <span className="tabular">
                      v{execution.rule_version}
                      {rule?.ok && rule.data.version !== execution.rule_version
                        ? ` (the rule is now at v${rule.data.version})`
                        : ""}
                    </span>
                  ),
                },
                { label: "Source", value: TRIGGER_SOURCE_LABELS[execution.trigger_source] ?? execution.trigger_source },
                {
                  label: "Record",
                  value: execution.entity_id ? <span className="tabular break-all">{execution.entity_id}</span> : "None",
                },
                {
                  label: "Started by",
                  value: execution.initiated_by ? memberName(members, execution.initiated_by) : "The trigger",
                },
                { label: "Ran with the permissions of", value: memberName(members, execution.executed_as, "Nobody") },
                { label: "Created", value: time(execution.created_at) },
                { label: "Started", value: time(execution.started_at) },
                { label: "Finished", value: time(execution.finished_at) },
                { label: "Attempts", value: <span className="tabular">{execution.attempt_count}</span> },
                { label: "Chain depth", value: <span className="tabular">{execution.depth}</span> },
                { label: "Correlation id", value: <span className="tabular break-all">{execution.correlation_id}</span> },
                {
                  label: "Caused by",
                  value: execution.causation_id ? <span className="tabular break-all">{execution.causation_id}</span> : "—",
                },
                ...(execution.error_summary
                  ? [
                      {
                        label: "Error",
                        value: <span className="whitespace-pre-wrap text-danger-700">{execution.error_summary}</span>,
                        span: true,
                      },
                    ]
                  : []),
              ]}
            />
          </CardBody>
        </Card>

        <Section
          title="Steps"
          description="One step per action, in order. Settings are what the step ran with; secrets are never shown."
        >
          {execution.steps.length === 0 ? (
            <EmptyState
              compact
              title={waiting ? "No steps yet" : "No actions ran"}
              description={
                waiting
                  ? "Steps appear once the background worker starts this run."
                  : "The rule's conditions did not match, so none of its actions ran."
              }
            />
          ) : (
            <ol className="flex flex-col gap-3">
              {execution.steps.map((step) => (
                <li key={step.id}>
                  <Card>
                    <CardHeader
                      as="h3"
                      title={`${step.order + 1}. ${actionLabel(actionCatalog, step.action_id)}`}
                      actions={<StatusBadge status={step.status} map={AUTOMATION_STEP_STATUS} size="sm" />}
                    />
                    <CardBody className="flex flex-col gap-4">
                      <DetailList
                        columns={3}
                        items={[
                          { label: "Attempt", value: <span className="tabular">{step.attempt}</span> },
                          { label: "Started", value: time(step.started_at) },
                          { label: "Finished", value: time(step.finished_at) },
                          ...(step.failure_category
                            ? [
                                {
                                  label: "Failure",
                                  value: FAILURE_CATEGORY_LABELS[step.failure_category] ?? step.failure_category,
                                },
                              ]
                            : []),
                          ...(step.error_message
                            ? [
                                {
                                  label: "Error message",
                                  value: <span className="whitespace-pre-wrap break-words">{step.error_message}</span>,
                                  span: true,
                                },
                              ]
                            : []),
                        ]}
                      />
                      <div className="grid gap-4 lg:grid-cols-2">
                        {/* config_snapshot arrives UNMASKED from the API (a webhook step carries its raw secret); it is redacted here, on the server, before rendering. */}
                        <JsonBlock label="Settings" value={redactConfig(step.action_id, step.config_snapshot)} />
                        <JsonBlock label="Result" value={step.result} />
                      </div>
                    </CardBody>
                  </Card>
                </li>
              ))}
            </ol>
          )}
        </Section>
      </PageBody>
    </>
  );
}
