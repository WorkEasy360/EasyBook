"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FormError } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { useIdempotentMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { formErrorOf, referenceOf } from "@/lib/api/errors";
import type { TimeEntry, TimeEntryBulkInput, TimeEntryStatus } from "@/types/api/projects";

/**
 * Row selection for bulk submit / approve on a server-rendered time table.
 *
 * The table stays a Server Component; only this provider, the checkboxes and
 * the action bar are client code. Eligibility mirrors
 * projects/services/time_entries.py :: _ALLOWED_TRANSITIONS — a draft or
 * rejected entry can be submitted, a submitted one approved — and nobody
 * approves their own time (`self_approval_forbidden`). Both bulk endpoints
 * are ATOMIC: one ineligible entry fails the whole batch, so ineligible
 * selections are left out of the request rather than sent to fail it.
 */

interface SelectionValue {
  selected: ReadonlySet<string>;
  toggle: (id: string, on: boolean) => void;
  setMany: (ids: readonly string[], on: boolean) => void;
  clear: () => void;
}

const SelectionContext = React.createContext<SelectionValue | null>(null);

function useSelection(): SelectionValue {
  const context = React.useContext(SelectionContext);
  if (!context) throw new Error("Time selection controls must be inside <TimeSelectionProvider>.");
  return context;
}

export function TimeSelectionProvider({ children }: { children: React.ReactNode }) {
  const [selected, setSelected] = React.useState<ReadonlySet<string>>(() => new Set());

  const value = React.useMemo<SelectionValue>(
    () => ({
      selected,
      toggle: (id, on) =>
        setSelected((current) => {
          const next = new Set(current);
          if (on) next.add(id);
          else next.delete(id);
          return next;
        }),
      setMany: (ids, on) =>
        setSelected((current) => {
          const next = new Set(current);
          for (const id of ids) {
            if (on) next.add(id);
            else next.delete(id);
          }
          return next;
        }),
      clear: () => setSelected(new Set()),
    }),
    [selected],
  );

  return <SelectionContext.Provider value={value}>{children}</SelectionContext.Provider>;
}

export function SelectEntryCheckbox({ id, label }: { id: string; label: string }) {
  const { selected, toggle } = useSelection();
  return (
    <input
      type="checkbox"
      aria-label={label}
      className="size-4 rounded border-ink-300 accent-brand-700"
      checked={selected.has(id)}
      onChange={(event) => toggle(id, event.target.checked)}
    />
  );
}

export function SelectAllEntriesCheckbox({ ids }: { ids: readonly string[] }) {
  const { selected, setMany } = useSelection();
  const ref = React.useRef<HTMLInputElement>(null);
  const count = ids.filter((id) => selected.has(id)).length;
  const all = ids.length > 0 && count === ids.length;

  React.useEffect(() => {
    if (ref.current) ref.current.indeterminate = count > 0 && !all;
  }, [count, all]);

  return (
    <input
      ref={ref}
      type="checkbox"
      aria-label="Select all entries on this page"
      className="size-4 rounded border-ink-300 accent-brand-700"
      checked={all}
      disabled={ids.length === 0}
      onChange={(event) => setMany(ids, event.target.checked)}
    />
  );
}

export interface SelectableEntry {
  id: string;
  status: TimeEntryStatus;
  user: string;
}

const SUBMITTABLE: ReadonlySet<TimeEntryStatus> = new Set(["draft", "rejected"]);

export function BulkTimeActions({
  entries,
  currentUserId,
  canApprove,
}: {
  entries: readonly SelectableEntry[];
  currentUserId: string;
  canApprove: boolean;
}) {
  const { selected, clear } = useSelection();
  const chosen = entries.filter((entry) => selected.has(entry.id));
  const submittable = chosen.filter((entry) => SUBMITTABLE.has(entry.status)).map((entry) => entry.id);
  const approvable = canApprove
    ? chosen.filter((entry) => entry.status === "submitted" && entry.user !== currentUserId).map((entry) => entry.id)
    : [];
  const skipped = chosen.length - new Set([...submittable, ...approvable]).size;

  return (
    <div className="flex flex-wrap items-center gap-2" data-print="hide" aria-live="polite">
      <span className="text-xs text-ink-600">
        {chosen.length === 0 ? "Select entries to submit or approve them together." : `${chosen.length} selected`}
        {skipped > 0 ? ` · ${skipped} not eligible for either action` : ""}
      </span>
      <BulkButton
        action="bulk-submit"
        ids={submittable}
        label="Submit selected"
        confirmTitle="Submit these entries for approval?"
        confirmMessage="Submitted entries can no longer be edited unless an approver rejects them. All of them are submitted together, or none are."
        successTitle="Entries submitted"
        onDone={clear}
      />
      {canApprove ? (
        <BulkButton
          action="bulk-approve"
          ids={approvable}
          label="Approve selected"
          confirmTitle="Approve these entries?"
          confirmMessage="Approved billable time becomes invoiceable. Your own entries are never included. All of them are approved together, or none are."
          successTitle="Entries approved"
          onDone={clear}
          primary
        />
      ) : null}
    </div>
  );
}

function BulkButton({
  action,
  ids,
  label,
  confirmTitle,
  confirmMessage,
  successTitle,
  onDone,
  primary = false,
}: {
  action: "bulk-submit" | "bulk-approve";
  ids: readonly string[];
  label: string;
  confirmTitle: string;
  confirmMessage: string;
  successTitle: string;
  onDone: () => void;
  primary?: boolean;
}) {
  const router = useRouter();
  const toast = useToast();
  const [open, setOpen] = React.useState(false);
  const [error, setError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });

  const mutation = useIdempotentMutation<TimeEntry[], TimeEntryBulkInput>(
    "time-entries",
    (input, idempotencyKey) => api.post<TimeEntry[]>(`time-entries/${action}`, input, { idempotencyKey }),
    {
      onSuccess: (saved) => {
        setOpen(false);
        toast.push({ tone: "success", title: successTitle, description: `${saved.length} ${saved.length === 1 ? "entry" : "entries"}` });
        onDone();
        router.refresh();
      },
      onError: (failure) => setError({ message: formErrorOf(failure), reference: referenceOf(failure) }),
    },
  );

  function close() {
    if (!mutation.isPending) setOpen(false);
  }

  return (
    <>
      <Button
        size="sm"
        variant={primary ? "primary" : "secondary"}
        disabled={ids.length === 0}
        onClick={() => {
          setError({ message: null, reference: null });
          setOpen(true);
        }}
      >
        {label} ({ids.length})
      </Button>
      <Dialog
        open={open}
        onClose={close}
        title={confirmTitle}
        size="sm"
        dismissible={!mutation.isPending}
        footer={
          <>
            <Button variant="secondary" onClick={close} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button
              variant="primary"
              loading={mutation.isPending}
              onClick={() => mutation.mutate({ entry_ids: [...ids] })}
            >
              {label} ({ids.length})
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-3 text-sm text-ink-700">
          <p>{confirmMessage}</p>
          <FormError message={error.message} reference={error.reference} />
        </div>
      </Dialog>
    </>
  );
}
