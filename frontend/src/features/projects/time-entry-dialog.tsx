"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Controller, useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FormError, FormField, Input, Textarea } from "@/components/ui/field";
import { NumericInput } from "@/components/ui/numeric-input";
import { useToast } from "@/components/ui/toast";
import { useOrg } from "@/components/providers/org-provider";
import { ProjectPicker } from "@/features/shared/pickers";
import { useIdempotentMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import { todayInZone } from "@/lib/datetime";
import { isValidDecimal, money } from "@/lib/money";
import { MemberPicker, TaskPicker } from "./pickers";
import type { TimeEntry, TimeEntryInput, TimeEntryUpdateInput } from "@/types/api/projects";

/**
 * Log time, or correct a draft/rejected entry.
 *
 * Billability and both rates are NOT asked for: the backend resolves them
 * from the project, task and person and freezes them onto the entry
 * (projects/services/rates.py), so the client never decides whether its own
 * time is chargeable. Only an ACTIVE project accepts time
 * (`project_not_active`), which is why the project picker offers active ones.
 *
 * Logging for someone else needs VIEW_ALL_TIMESHEETS
 * (`log_time_for_others_forbidden`), so the person field only appears for
 * roles that hold it. Editing a rejected entry returns it to draft for
 * resubmission (services/time_entries.py :: update_time_entry).
 */

const DATE = /^\d{4}-\d{2}-\d{2}$/;

const schema = z.object({
  project_id: z.string().min(1, "Choose a project."),
  task_id: z.string().min(1, "Choose a task."),
  entry_date: z.string().regex(DATE, "Enter the date worked."),
  // `hours_invalid` / `hours_exceed_day`.
  hours: z
    .string()
    .refine((value) => isValidDecimal(value) && money(value).gt(0), "Enter hours above zero.")
    .refine((value) => !isValidDecimal(value) || money(value).lte(24), "One entry cannot exceed 24 hours."),
  description: z.string().max(500),
  user_id: z.string().nullable(),
});

type Values = z.infer<typeof schema>;

export function TimeEntryDialogButton({
  entry,
  defaultProjectId,
  canLogForOthers,
  triggerLabel,
  projectName,
}: {
  entry?: TimeEntry;
  /** Shown read-only when editing: an entry's project is fixed. */
  projectName?: string;
  defaultProjectId?: string | null;
  canLogForOthers: boolean;
  triggerLabel?: string;
}) {
  const router = useRouter();
  const toast = useToast();
  const org = useOrg();
  const [open, setOpen] = React.useState(false);
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });
  const formId = React.useId();

  const defaults = React.useCallback(
    (): Values => ({
      project_id: entry?.project ?? defaultProjectId ?? "",
      task_id: entry?.task ?? "",
      entry_date: entry?.entry_date ?? todayInZone(org.timeZone),
      hours: entry?.hours ?? "",
      description: entry?.description ?? "",
      user_id: null,
    }),
    [entry, defaultProjectId, org.timeZone],
  );

  const {
    control,
    register,
    handleSubmit,
    reset,
    setValue,
    setError,
    formState: { errors },
  } = useForm<Values>({ resolver: zodResolver(schema), defaultValues: defaults() });
  const projectId = useWatch({ control, name: "project_id" });

  const mutation = useIdempotentMutation<TimeEntry, TimeEntryInput | TimeEntryUpdateInput>(
    "time-entries",
    (input, idempotencyKey) =>
      entry
        ? api.patch<TimeEntry>(`time-entries/${entry.id}`, input)
        : api.post<TimeEntry>("time-entries", input, { idempotencyKey }),
    {
      onSuccess: (saved) => {
        toast.push({
          tone: "success",
          title: entry ? "Time entry updated" : "Time logged",
          description: saved.status === "draft" ? "Saved as a draft — submit it for approval." : undefined,
        });
        setOpen(false);
        reset(defaults());
        router.refresh();
      },
      onError: (error) => {
        setFormError({ message: formErrorOf(error), reference: referenceOf(error) });
        for (const [field, messages] of Object.entries(fieldErrorsOf(error))) {
          if (field in schema.shape && messages[0]) {
            setError(field as keyof Values, { type: "server", message: messages[0] });
          }
        }
      },
    },
  );

  function onSubmit(values: Values) {
    setFormError({ message: null, reference: null });
    if (entry) {
      mutation.mutate({
        entry_date: values.entry_date,
        hours: values.hours,
        description: values.description,
        task_id: values.task_id,
      });
      return;
    }
    mutation.mutate({
      project_id: values.project_id,
      task_id: values.task_id,
      entry_date: values.entry_date,
      hours: values.hours,
      description: values.description,
      ...(canLogForOthers && values.user_id ? { user_id: values.user_id } : {}),
    });
  }

  return (
    <>
      <Button
        variant={entry ? "link" : "primary"}
        size={entry ? "sm" : "md"}
        onClick={() => {
          setFormError({ message: null, reference: null });
          reset(defaults());
          setOpen(true);
        }}
      >
        {entry ? (
          <>
            Edit<span className="sr-only"> time entry</span>
          </>
        ) : (
          (triggerLabel ?? "Log time")
        )}
      </Button>

      <Dialog
        open={open}
        onClose={() => {
          if (!mutation.isPending) setOpen(false);
        }}
        title={entry ? "Edit time entry" : "Log time"}
        {...(entry?.status === "rejected"
          ? { description: "Saving returns this rejected entry to draft; submit it again for approval." }
          : {})}
        dismissible={!mutation.isPending}
        size="lg"
        footer={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button type="submit" form={formId} variant="primary" loading={mutation.isPending}>
              {entry ? "Save entry" : "Log time"}
            </Button>
          </>
        }
      >
        <form id={formId} method="post" noValidate onSubmit={(event) => void handleSubmit(onSubmit)(event)} className="flex flex-col gap-4">
          <FormError message={formError.message} reference={formError.reference} />
          {entry?.rejection_reason ? (
            <p className="rounded-md border border-warning-100 bg-warning-50 px-3 py-2 text-sm text-warning-700">
              Rejected: {entry.rejection_reason}
            </p>
          ) : null}
          <div className="grid gap-4 sm:grid-cols-2">
            <FormField
              label="Project"
              required
              error={errors.project_id?.message ?? null}
              {...(entry ? { hint: "An entry cannot move to another project." } : { hint: "Active projects only." })}
            >
              {entry ? (
                <Input value={projectName ?? "This entry's project"} readOnly />
              ) : (
                <Controller
                  control={control}
                  name="project_id"
                  render={({ field }) => (
                    <ProjectPicker
                      value={field.value || null}
                      onChange={(value) => {
                        field.onChange(value ?? "");
                        // A task belongs to one project (`task_project_mismatch`).
                        setValue("task_id", "");
                      }}
                    />
                  )}
                />
              )}
            </FormField>
            <FormField label="Task" required error={errors.task_id?.message ?? null}>
              <Controller
                control={control}
                name="task_id"
                render={({ field }) => (
                  <TaskPicker
                    projectId={projectId || null}
                    value={field.value || null}
                    onChange={(value) => field.onChange(value ?? "")}
                  />
                )}
              />
            </FormField>
            <FormField label="Date" required error={errors.entry_date?.message ?? null} hint="Within the project's dates.">
              <Input type="date" {...register("entry_date")} />
            </FormField>
            <FormField label="Hours" required error={errors.hours?.message ?? null} hint="Decimal hours: 1.5 is ninety minutes.">
              <Controller
                control={control}
                name="hours"
                render={({ field }) => (
                  <NumericInput
                    currency={false}
                    scale={2}
                    nonNegative
                    suffix="h"
                    value={field.value}
                    onValueChange={field.onChange}
                    onBlur={field.onBlur}
                  />
                )}
              />
            </FormField>
            {canLogForOthers && !entry ? (
              <FormField
                label="Person"
                error={errors.user_id?.message ?? null}
                className="sm:col-span-2"
                hint="Leave empty to log your own time."
              >
                <Controller
                  control={control}
                  name="user_id"
                  render={({ field }) => (
                    <MemberPicker value={field.value} onChange={field.onChange} placeholder="Myself" />
                  )}
                />
              </FormField>
            ) : null}
            <FormField label="Description" error={errors.description?.message ?? null} className="sm:col-span-2">
              <Textarea rows={2} {...register("description")} />
            </FormField>
          </div>
        </form>
      </Dialog>
    </>
  );
}
