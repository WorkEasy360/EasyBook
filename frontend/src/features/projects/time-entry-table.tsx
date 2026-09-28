import type * as React from "react";
import Link from "next/link";
import { DataTable, type Column } from "@/components/ui/data-table";
import { Money, Quantity } from "@/components/ui/money";
import { StatusBadge, TIME_ENTRY_STATUS } from "@/components/ui/status-badge";
import { DocumentAction } from "@/features/shared/document-action";
import { formatDate } from "@/lib/datetime";
import type { ApiError } from "@/lib/api/errors";
import type { Paginated } from "@/lib/api/types";
import type { TimeEntry } from "@/types/api/projects";
import { DeleteTimeEntryButton } from "./delete-time-entry";
import { TimeEntryDialogButton } from "./time-entry-dialog";
import { BulkTimeActions, SelectAllEntriesCheckbox, SelectEntryCheckbox, TimeSelectionProvider } from "./time-selection";

/**
 * The time-entry table shared by the entries list and the weekly view.
 *
 * A Server Component: rows render on the server; the selection checkboxes,
 * bulk bar and per-row actions are the only client pieces. Which actions a
 * row offers follows the backend's lifecycle (services/time_entries.py):
 * edit and delete while draft or rejected (delete also once submitted or
 * approved, never once invoiced), submit from draft/rejected, approve from
 * submitted and never one's own, reject while submitted or approved.
 */

export interface TimeEntryTableProps {
  caption: string;
  data: Paginated<TimeEntry> | undefined;
  error: ApiError | Error | null;
  projects: Map<string, { name: string; project_code: string; currency: string }>;
  tasks: Map<string, string>;
  people: Map<string, string>;
  currentUserId: string;
  canApprove: boolean;
  canSeeAll: boolean;
  page: number;
  pageSize: number;
  buildPageHref: (page: number) => string;
  emptyTitle: string;
  emptyDescription: string;
  note?: React.ReactNode;
}

export function TimeEntryTable({
  caption,
  data,
  error,
  projects,
  tasks,
  people,
  currentUserId,
  canApprove,
  canSeeAll,
  page,
  pageSize,
  buildPageHref,
  emptyTitle,
  emptyDescription,
  note,
}: TimeEntryTableProps) {
  const rows = data?.results ?? [];

  function summaryOf(entry: TimeEntry): string {
    return `${entry.hours} h on ${formatDate(entry.entry_date)}`;
  }

  const columns: Column<TimeEntry>[] = [
    {
      key: "select",
      // No headerLabel: DataTable aria-hides a header that has one, which
      // would hide this checkbox. It carries its own aria-label.
      header: <SelectAllEntriesCheckbox ids={rows.map((entry) => entry.id)} />,
      width: "2.5rem",
      cell: (entry) => <SelectEntryCheckbox id={entry.id} label={`Select ${summaryOf(entry)}`} />,
    },
    {
      key: "entry_date",
      header: "Date",
      cell: (entry) => <span className="tabular whitespace-nowrap">{formatDate(entry.entry_date)}</span>,
    },
    {
      key: "project",
      header: "Project · task",
      cell: (entry) => {
        const project = projects.get(entry.project);
        return (
          <div className="min-w-0">
            <Link href={`/projects/${entry.project}`} className="text-brand-700 hover:underline">
              {project ? project.name : "Project"}
            </Link>
            <p className="text-xs text-ink-500">{tasks.get(entry.task) ?? "Task"}</p>
            {entry.description ? <p className="line-clamp-1 text-xs text-ink-600">{entry.description}</p> : null}
          </div>
        );
      },
    },
    ...(canSeeAll
      ? [
          {
            key: "user",
            header: "Person",
            hideBelow: "md" as const,
            cell: (entry: TimeEntry) =>
              entry.user === currentUserId ? "You" : (people.get(entry.user) ?? "Former member"),
          },
        ]
      : []),
    { key: "hours", header: "Hours", numeric: true, cell: (entry) => <Quantity value={entry.hours} unit="h" /> },
    {
      key: "billable_amount",
      header: "Billable",
      numeric: true,
      hideBelow: "sm",
      cell: (entry) =>
        entry.is_billable ? (
          <Money value={entry.billable_amount} currency={projects.get(entry.project)?.currency} />
        ) : (
          <span className="text-xs text-ink-500">Non-billable</span>
        ),
    },
    {
      key: "status",
      header: "Status",
      cell: (entry) => (
        <div>
          <StatusBadge status={entry.status} map={TIME_ENTRY_STATUS} size="sm" />
          {entry.status === "rejected" && entry.rejection_reason ? (
            <p className="mt-0.5 line-clamp-2 max-w-48 text-xs text-danger-700">{entry.rejection_reason}</p>
          ) : null}
        </div>
      ),
    },
    {
      key: "actions",
      header: "",
      headerLabel: "Actions",
      numeric: true,
      cell: (entry) => {
        const editable = entry.status === "draft" || entry.status === "rejected";
        const own = entry.user === currentUserId;
        return (
          <div className="flex flex-wrap items-center justify-end gap-x-2 gap-y-1">
            {editable ? (
              <TimeEntryDialogButton
                entry={entry}
                canLogForOthers={false}
                projectName={projects.get(entry.project)?.name ?? "Project"}
              />
            ) : null}
            {editable ? (
              <DocumentAction
                resource="time-entries"
                id={entry.id}
                action="submit"
                label="Submit"
                size="sm"
                confirmTitle="Submit this entry for approval?"
                confirmMessage={<p>{summaryOf(entry)} goes to an approver. It cannot be edited unless it is rejected.</p>}
                successTitle="Entry submitted"
              />
            ) : null}
            {canApprove && entry.status === "submitted" && !own ? (
              <DocumentAction
                resource="time-entries"
                id={entry.id}
                action="approve"
                label="Approve"
                size="sm"
                variant="primary"
                confirmTitle="Approve this entry?"
                confirmMessage={
                  <p>
                    {summaryOf(entry)} is approved{entry.is_billable ? " and becomes invoiceable" : ""}. It can still be
                    rejected until it is invoiced.
                  </p>
                }
                successTitle="Entry approved"
              />
            ) : null}
            {canApprove && (entry.status === "submitted" || entry.status === "approved") ? (
              <DocumentAction
                resource="time-entries"
                id={entry.id}
                action="reject"
                label="Reject"
                size="sm"
                variant="danger"
                confirmTitle="Reject this entry?"
                confirmMessage={<p>{summaryOf(entry)} goes back to its author to correct and resubmit.</p>}
                reason={{ label: "Reason", hint: "Shown to the person who logged the time." }}
                successTitle="Entry rejected"
              />
            ) : null}
            {entry.status !== "invoiced" ? <DeleteTimeEntryButton entryId={entry.id} summary={summaryOf(entry)} /> : null}
          </div>
        );
      },
    },
  ];

  return (
    <TimeSelectionProvider>
      <div className="flex flex-col gap-3">
        {rows.length > 0 ? (
          <BulkTimeActions
            entries={rows.map((entry) => ({ id: entry.id, status: entry.status, user: entry.user }))}
            currentUserId={currentUserId}
            canApprove={canApprove}
          />
        ) : null}
        <DataTable
          caption={caption}
          columns={columns}
          data={data}
          error={error}
          getRowId={(entry) => entry.id}
          emptyTitle={emptyTitle}
          emptyDescription={emptyDescription}
          page={page}
          pageSize={pageSize}
          buildPageHref={buildPageHref}
          note={note}
        />
      </div>
    </TimeSelectionProvider>
  );
}
