import type { Metadata } from "next";
import Link from "next/link";
import { PageHeader } from "@/components/ui/page-header";
import { DetailTabs, PageBody, Section } from "@/components/ui/detail";
import { DataTable, TableFooterRow, type Column } from "@/components/ui/data-table";
import { FilterBar } from "@/components/ui/filter-bar";
import { LinkButton } from "@/components/ui/link-button";
import { Quantity } from "@/components/ui/money";
import { ForbiddenState } from "@/components/ui/states";
import { TimeEntryDialogButton } from "@/features/projects/time-entry-dialog";
import { TimeEntryTable } from "@/features/projects/time-entry-table";
import { loadTimeLookups, membershipLabel } from "@/features/projects/time-lookups";
import { TIMESHEET_TABS } from "@/features/projects/timesheet-tabs";
import { pivotWeek, startOfWeek, sumHours, weekDates, type WeekRow } from "@/features/projects/week";
import { serverApi, tryServer } from "@/lib/api/server";
import { dateParamOf, paramOf, wholeList, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { addDays, formatDate, parseDateString, todayInZone } from "@/lib/datetime";
import type { ApiError } from "@/lib/api/errors";
import type { TimeEntry } from "@/types/api/projects";

export const metadata: Metadata = { title: "Weekly timesheet" };

const PATH = "/projects/timesheets/week";
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;

/**
 * One person's week — GET /timesheet/?from_date&to_date&user_id, a bare
 * array of TimeEntry (captured live).
 *
 * The grid adds up HOURS for display (per task and per day); it states no
 * money. Naming another person needs VIEW_ALL_TIMESHEETS
 * (`timesheet_forbidden`), so the person switcher only exists for roles that
 * hold it — for everyone else the backend answers with their own week.
 */
export default async function TimesheetWeekPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.LOG_TIME)) {
    return (
      <>
        <PageHeader title="Weekly timesheet" />
        <ForbiddenState resource="timesheets" />
      </>
    );
  }

  const canSeeAll = roleHasPermission(role, PERMISSIONS.VIEW_ALL_TIMESHEETS);
  const canApprove = roleHasPermission(role, PERMISSIONS.APPROVE_TIME);
  const today = todayInZone(session.timeZone);
  const thisMonday = startOfWeek(today) ?? today;
  const monday = startOfWeek(dateParamOf(params, "week") ?? today) ?? thisMonday;
  const dates = weekDates(monday);
  const sunday = dates[6] ?? monday;
  const rawUser = paramOf(params, "user");
  const userId = canSeeAll && rawUser && UUID_PATTERN.test(rawUser) && rawUser !== session.user.id ? rawUser : null;

  const result = await tryServer(() =>
    serverApi.get<TimeEntry[]>("timesheet", {
      query: { from_date: monday, to_date: sunday, user_id: userId ?? undefined },
    }),
  );
  const entries = result.ok ? result.data : [];
  const lookups = await loadTimeLookups(entries, { includePeople: canSeeAll });
  const personName = userId ? (lookups.people.get(userId) ?? "Selected person") : "You";

  function href(overrides: { week?: string | null; user?: string | null }): string {
    const next = new URLSearchParams();
    const week = overrides.week === undefined ? monday : overrides.week;
    const user = overrides.user === undefined ? userId : overrides.user;
    if (week && week !== thisMonday) next.set("week", week);
    if (user) next.set("user", user);
    const query = next.toString();
    return query ? `${PATH}?${query}` : PATH;
  }

  const rows = pivotWeek(entries, monday);
  const dayTotals = dates.map((date) => sumHours(entries.filter((entry) => entry.entry_date === date).map((entry) => entry.hours)));
  const weekTotal = sumHours(entries.map((entry) => entry.hours));

  const columns: Column<WeekRow>[] = [
    {
      key: "task",
      header: "Project · task",
      cell: (row) => (
        <div className="min-w-0">
          <Link href={`/projects/${row.project}`} className="text-brand-700 hover:underline">
            {lookups.projects.get(row.project)?.name ?? "Project"}
          </Link>
          <p className="text-xs text-ink-500">{lookups.tasks.get(row.task) ?? "Task"}</p>
        </div>
      ),
    },
    ...dates.map<Column<WeekRow>>((date, index) => ({
      key: date,
      header: (
        <span className={date === today ? "text-brand-800" : undefined}>
          {WEEKDAYS[index]} {parseDateString(date)?.day}
        </span>
      ),
      headerLabel: formatDate(date),
      numeric: true,
      cell: (row) => (row.days[index] ? <Quantity value={row.days[index]} /> : <span className="text-ink-300">·</span>),
    })),
    { key: "total", header: "Total", numeric: true, cell: (row) => <Quantity value={row.total} unit="h" /> },
  ];

  return (
    <>
      <PageHeader
        title="Weekly timesheet"
        description={`${personName === "You" ? "Your" : `${personName}'s`} week of ${formatDate(monday)} – ${formatDate(sunday)}.`}
        actions={<TimeEntryDialogButton canLogForOthers={canSeeAll} />}
      />
      <DetailTabs tabs={TIMESHEET_TABS} />
      <PageBody>
        <div className="flex flex-wrap items-center gap-2" data-print="hide">
          <LinkButton href={href({ week: addDays(monday, -7) })} size="sm">
            Previous week
          </LinkButton>
          {monday !== thisMonday ? (
            <LinkButton href={href({ week: null })} size="sm">
              This week
            </LinkButton>
          ) : null}
          <LinkButton href={href({ week: addDays(monday, 7) })} size="sm">
            Next week
          </LinkButton>
        </div>

        {canSeeAll && lookups.memberships.length > 1 ? (
          <FilterBar
            groups={[
              {
                key: "user",
                label: "Person",
                value: userId ?? "",
                allLabel: "You",
                options: lookups.memberships
                  .filter((membership) => membership.user.id !== session.user.id)
                  .map((membership) => ({ value: membership.user.id, label: membershipLabel(membership) })),
              },
            ]}
            buildFilterHref={(_key, value) => href({ user: value })}
            clearHref={href({ user: null })}
            activeFilterCount={userId ? 1 : 0}
          />
        ) : null}

        <DataTable
          caption={`Hours by task, week of ${formatDate(monday)}`}
          columns={columns}
          data={result.ok ? wholeList(rows) : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          getRowId={(row) => row.key}
          emptyTitle="No time logged this week"
          emptyDescription="Log time against a task on an active project."
          page={1}
          pageSize={Math.max(rows.length, 1)}
          buildPageHref={() => href({})}
          footer={
            rows.length > 0 ? (
              <TableFooterRow
                label="Daily total"
                columnCount={columns.length}
                values={[
                  ...dayTotals.map((total, index) => ({ key: dates[index] ?? String(index), node: <Quantity value={total} /> })),
                  { key: "total", node: <Quantity value={weekTotal} unit="h" /> },
                ]}
              />
            ) : undefined
          }
          note="Totals add up the hours of the entries shown, in every status."
        />

        {entries.length > 0 ? (
          <Section title="Entries this week" description="Select draft or rejected entries to submit the week in one go.">
            <TimeEntryTable
              caption={`Time entries, week of ${formatDate(monday)}`}
              data={wholeList(entries)}
              error={null}
              projects={lookups.projects}
              tasks={lookups.tasks}
              people={lookups.people}
              currentUserId={session.user.id}
              canApprove={canApprove}
              canSeeAll={canSeeAll}
              page={1}
              pageSize={entries.length}
              buildPageHref={() => href({})}
              emptyTitle="No time logged this week"
              emptyDescription="Log time against a task on an active project."
            />
          </Section>
        ) : null}
      </PageBody>
    </>
  );
}
