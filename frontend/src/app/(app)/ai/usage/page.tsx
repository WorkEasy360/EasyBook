import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody, Section } from "@/components/ui/detail";
import { DataTable, type Column } from "@/components/ui/data-table";
import { DateFilterForm } from "@/components/ui/date-filter-form";
import { StatCard, StatGrid } from "@/components/ui/stat-card";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { serverApi, tryServer } from "@/lib/api/server";
import { dateParamOf, wholeList, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { referenceOf } from "@/lib/api/errors";
import { formatDate } from "@/lib/datetime";
import type { AiUsage } from "@/types/api/ai";

export const metadata: Metadata = { title: "Ask Books usage" };

const CRUMBS = [{ label: "Ask Books", href: "/ai" }, { label: "Usage" }];

/** Token and request COUNTS — integers, not money, so ordinary number formatting is right. */
function count(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : value.toLocaleString("en-IN");
}

type ModelRow = AiUsage["by_model"][number];
type StatusRow = AiUsage["by_status"][number];
type FeatureRow = AiUsage["by_feature"][number];

/**
 * GET ai/usage/?from_date&to_date (VIEW_AI_USAGE — Owner/Admin).
 *
 * Organization-wide request metadata from AIRequestLog: counts and tokens
 * only; questions and answers are never in this log. Without dates the
 * backend reports from the first of the current month to today, and echoes
 * the period it used — which is what the page shows.
 */
export default async function AiUsagePage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_AI_USAGE)) {
    return (
      <>
        <PageHeader title="Ask Books usage" breadcrumbs={CRUMBS} />
        <ForbiddenState resource="Ask Books usage" />
      </>
    );
  }

  const fromDate = dateParamOf(params, "from_date");
  const toDate = dateParamOf(params, "to_date");
  const result = await tryServer(() =>
    serverApi.get<AiUsage>("ai/usage", { query: { from_date: fromDate, to_date: toDate } }),
  );

  const filters = (
    <DateFilterForm
      action="/ai/usage"
      fields={[
        { name: "from_date", label: "From", value: fromDate },
        { name: "to_date", label: "To", value: toDate },
      ]}
      clearHref="/ai/usage"
    />
  );

  if (!result.ok) {
    return (
      <>
        <PageHeader title="Ask Books usage" breadcrumbs={CRUMBS} />
        <PageBody>
          {filters}
          <ErrorState title="Could not load usage" message={result.error.message} reference={referenceOf(result.error)} />
        </PageBody>
      </>
    );
  }

  const usage = result.data;
  const hasCost = usage.by_model.some((row) => row.estimated_cost !== undefined);

  const modelColumns: Column<ModelRow>[] = [
    { key: "model", header: "Model", cell: (row) => row.model },
    { key: "provider", header: "Provider", hideBelow: "sm", cell: (row) => row.provider },
    { key: "requests", header: "Requests", numeric: true, cell: (row) => count(row.requests) },
    { key: "input", header: "Input tokens", numeric: true, hideBelow: "md", cell: (row) => count(row.input_tokens) },
    { key: "output", header: "Output tokens", numeric: true, hideBelow: "md", cell: (row) => count(row.output_tokens) },
    ...(hasCost
      ? [
          {
            key: "estimated_cost",
            header: "Estimated cost",
            numeric: true,
            // A decimal string with no currency in the contract: shown as
            // returned, never parsed or re-rounded.
            cell: (row: ModelRow) => <span className="tabular">{row.estimated_cost ?? "—"}</span>,
          } satisfies Column<ModelRow>,
        ]
      : []),
  ];

  const featureColumns: Column<FeatureRow>[] = [
    { key: "feature", header: "Feature", cell: (row) => row.feature || "—" },
    { key: "requests", header: "Requests", numeric: true, cell: (row) => count(row.requests) },
  ];
  const statusColumns: Column<StatusRow>[] = [
    { key: "status", header: "Outcome", cell: (row) => row.status || "—" },
    { key: "requests", header: "Requests", numeric: true, cell: (row) => count(row.requests) },
  ];

  const period = `${formatDate(usage.from_date)} – ${formatDate(usage.to_date)}`;

  return (
    <>
      <PageHeader
        title="Ask Books usage"
        breadcrumbs={CRUMBS}
        description={`Requests and tokens across your organization, ${period}. Questions and answers are not part of this log.`}
      />
      <PageBody>
        {filters}

        <StatGrid columns={4}>
          <StatCard label="Requests" value={count(usage.totals.requests)} hint={period} />
          <StatCard label="Input tokens" value={count(usage.totals.input_tokens)} />
          <StatCard label="Output tokens" value={count(usage.totals.output_tokens)} />
          <StatCard
            label="Cached input tokens"
            value={count(usage.totals.cached_input_tokens)}
            hint={`Embedding tokens: ${count(usage.totals.embedding_tokens)}`}
          />
        </StatGrid>

        <Section
          title="By model"
          description={
            hasCost
              ? "Estimated cost uses the per-model prices currently configured on the server. It is an estimate, not a bill."
              : "No model prices are configured, so no cost estimate is shown."
          }
        >
          <DataTable
            caption={`Ask Books usage by model, ${period}`}
            columns={modelColumns}
            data={wholeList(usage.by_model)}
            getRowId={(row) => `${row.provider}:${row.model}`}
            emptyTitle="No Ask Books requests in this period"
            page={1}
            pageSize={Math.max(usage.by_model.length, 1)}
            buildPageHref={() => "/ai/usage"}
          />
        </Section>

        <div className="grid gap-4 lg:grid-cols-2">
          <Section title="By feature">
            <DataTable
              caption={`Ask Books requests by feature, ${period}`}
              columns={featureColumns}
              data={wholeList(usage.by_feature)}
              getRowId={(row) => row.feature}
              emptyTitle="No requests"
              page={1}
              pageSize={Math.max(usage.by_feature.length, 1)}
              buildPageHref={() => "/ai/usage"}
            />
          </Section>
          <Section title="By outcome">
            <DataTable
              caption={`Ask Books requests by outcome, ${period}`}
              columns={statusColumns}
              data={wholeList(usage.by_status)}
              getRowId={(row) => row.status}
              emptyTitle="No requests"
              page={1}
              pageSize={Math.max(usage.by_status.length, 1)}
              buildPageHref={() => "/ai/usage"}
            />
          </Section>
        </div>
      </PageBody>
    </>
  );
}
