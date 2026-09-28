import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { DetailTabs, PageBody } from "@/components/ui/detail";
import { DateFilterForm } from "@/components/ui/date-filter-form";
import { FilterBar, SortNote } from "@/components/ui/filter-bar";
import { TIME_ENTRY_STATUS, statusOptions } from "@/components/ui/status-badge";
import { ForbiddenState } from "@/components/ui/states";
import { TimeEntryDialogButton } from "@/features/projects/time-entry-dialog";
import { TimeEntryTable } from "@/features/projects/time-entry-table";
import { loadTimeLookups } from "@/features/projects/time-lookups";
import { TIMESHEET_TABS } from "@/features/projects/timesheet-tabs";
import { serverApi, tryServer } from "@/lib/api/server";
import { capabilitiesFor } from "@/lib/api/capabilities";
import { dateParamOf, parseListQuery, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import type { ApiError } from "@/lib/api/errors";
import type { TimeEntry } from "@/types/api/projects";

export const metadata: Metadata = { title: "Timesheets" };

const PATH = "/projects/timesheets";
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Time entries, with the approval workflow.
 *
 * What a person sees is decided by the backend, not by this page:
 * /time-entries/ returns only the caller's own entries unless they hold
 * VIEW_ALL_TIMESHEETS (projects/api/views.py :: _scope_time_entries), because
 * an entry's cost rate is an indirect read on what a colleague is paid.
 */
export default async function TimesheetsPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;
  const role = session.role;

  // Every time-entry endpoint is gated on LOG_TIME, including reads.
  if (!roleHasPermission(role, PERMISSIONS.LOG_TIME)) {
    return (
      <>
        <PageHeader title="Timesheets" />
        <ForbiddenState resource="timesheets" />
      </>
    );
  }

  const canSeeAll = roleHasPermission(role, PERMISSIONS.VIEW_ALL_TIMESHEETS);
  const canApprove = roleHasPermission(role, PERMISSIONS.APPROVE_TIME);
  const query = parseListQuery(PATH, "time-entries", params);
  // The view passes these straight into a queryset filter, where a malformed
  // date or UUID is a server error rather than a 400 — so only well-formed
  // values are forwarded.
  const apiParams = { ...query.apiParams };
  if (apiParams["from_date"] && !dateParamOf(params, "from_date")) delete apiParams["from_date"];
  if (apiParams["to_date"] && !dateParamOf(params, "to_date")) delete apiParams["to_date"];
  if (apiParams["project"] && !UUID_PATTERN.test(String(apiParams["project"]))) delete apiParams["project"];
  const result = await tryServer(() => serverApi.list<TimeEntry>("time-entries", { query: apiParams }));

  const projectFilter = query.filters["project"];
  const lookups = await loadTimeLookups(result.ok ? result.data.results : [], {
    includePeople: canSeeAll,
    extraProjectIds: projectFilter ? [projectFilter] : [],
  });

  const { from_date: fromDate, to_date: toDate, status } = query.filters;
  const preserved = { project: projectFilter, status };
  const hasDates = Boolean(fromDate || toDate);
  const withoutDates = new URLSearchParams();
  for (const [key, value] of Object.entries(preserved)) if (value) withoutDates.set(key, value);
  const clearDatesHref = withoutDates.toString() ? `${PATH}?${withoutDates}` : PATH;

  return (
    <>
      <PageHeader
        title="Timesheets"
        description={
          canSeeAll
            ? "Everyone's time. Logging time posts nothing; approved billable hours can be invoiced from the project."
            : "Your time. Submit entries for approval; approved billable hours can then be invoiced."
        }
        actions={<TimeEntryDialogButton canLogForOthers={canSeeAll} defaultProjectId={projectFilter ?? null} />}
      />
      <DetailTabs tabs={TIMESHEET_TABS} />
      <PageBody>
        <div className="flex flex-col gap-3">
          <FilterBar
            groups={[
              {
                key: "status",
                label: "Status",
                value: status ?? "",
                options: statusOptions(TIME_ENTRY_STATUS),
              },
              ...(projectFilter
                ? [
                    {
                      key: "project",
                      label: "Project",
                      value: projectFilter,
                      options: [{ value: projectFilter, label: lookups.projects.get(projectFilter)?.name ?? "Selected project" }],
                    },
                  ]
                : []),
            ]}
            buildFilterHref={query.buildFilterHref}
            clearHref={query.clearHref}
            activeFilterCount={query.activeFilterCount}
          />
          <DateFilterForm
            action={PATH}
            fields={[
              { name: "from_date", label: "From", value: fromDate },
              { name: "to_date", label: "To", value: toDate },
            ]}
            preserve={preserved}
            {...(hasDates ? { clearHref: clearDatesHref } : {})}
          />
        </div>

        {canApprove ? (
          <p className="text-xs text-ink-500">
            Approve submitted entries from their row, or select several and approve them together. Nobody can approve
            their own time.
          </p>
        ) : null}

        <TimeEntryTable
          caption="Time entries"
          data={result.ok ? result.data : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          projects={lookups.projects}
          tasks={lookups.tasks}
          people={lookups.people}
          currentUserId={session.user.id}
          canApprove={canApprove}
          canSeeAll={canSeeAll}
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={query.buildPageHref}
          emptyTitle={query.activeFilterCount > 0 ? "No time entries match these filters" : "No time logged yet"}
          emptyDescription="Log time against a task on an active project."
          note={<SortNote description={capabilitiesFor("time-entries").defaultOrder ?? "entry date, newest first"} />}
        />
      </PageBody>
    </>
  );
}
