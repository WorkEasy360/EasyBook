"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Controller, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Checkbox, FormError, FormField, Input, Textarea } from "@/components/ui/field";
import { NumericInput, QuantityInput } from "@/components/ui/numeric-input";
import { useToast } from "@/components/ui/toast";
import { ItemPicker } from "@/features/shared/pickers";
import { useApiMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import { isValidDecimal, money } from "@/lib/money";
import type { BillingMethod, Task, TaskInput, TaskUpdateInput } from "@/types/api/projects";

/**
 * Add or edit a task on a project.
 *
 * `is_billable` on a task is an upper bound the PROJECT can veto
 * (services/rates.py :: resolve_is_billable): on a fixed-fee or non-billable
 * project no task's time is chargeable, whatever this box says. The hint says
 * so rather than letting the checkbox imply otherwise.
 */

const optionalAmount = z
  .string()
  .refine((value) => value.trim() === "" || (isValidDecimal(value) && money(value).gte(0)), "Cannot be negative.");

const schema = z.object({
  name: z.string().trim().min(1, "A name is required.").max(255),
  description: z.string().max(4000),
  is_billable: z.boolean(),
  hourly_rate: optionalAmount,
  estimated_hours: optionalAmount,
  service_item_id: z.string().nullable(),
  is_active: z.boolean(),
});

type Values = z.infer<typeof schema>;

const blankToNull = (value: string) => (value.trim() === "" ? null : value);

function defaultsOf(task: Task | undefined): Values {
  return {
    name: task?.name ?? "",
    description: task?.description ?? "",
    is_billable: task?.is_billable ?? true,
    hourly_rate: task?.hourly_rate ?? "",
    estimated_hours: task?.estimated_hours ?? "",
    service_item_id: task?.service_item ?? null,
    is_active: task?.is_active ?? true,
  };
}

export function TaskDialogButton({
  projectId,
  billingMethod,
  currency,
  task,
}: {
  projectId: string;
  billingMethod: BillingMethod;
  currency: string;
  task?: Task;
}) {
  const router = useRouter();
  const toast = useToast();
  const [open, setOpen] = React.useState(false);
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });
  const formId = React.useId();

  const {
    control,
    register,
    handleSubmit,
    reset,
    setError,
    formState: { errors },
  } = useForm<Values>({ resolver: zodResolver(schema), defaultValues: defaultsOf(task) });

  const mutation = useApiMutation<Task, TaskInput | TaskUpdateInput>(
    `projects/${projectId}/tasks`,
    (input) =>
      task ? api.patch<Task>(`projects/tasks/${task.id}`, input) : api.post<Task>(`projects/${projectId}/tasks`, input),
    {
      onSuccess: (saved) => {
        toast.push({ tone: "success", title: task ? "Task updated" : "Task added", description: saved.name });
        setOpen(false);
        reset(defaultsOf(task ? saved : undefined));
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
    const input: TaskInput = {
      name: values.name,
      description: values.description,
      is_billable: values.is_billable,
      hourly_rate: blankToNull(values.hourly_rate),
      estimated_hours: blankToNull(values.estimated_hours),
      service_item_id: values.service_item_id,
    };
    mutation.mutate(task ? { ...input, is_active: values.is_active } : input);
  }

  const chargeable = billingMethod === "hourly";

  return (
    <>
      <Button
        variant={task ? "link" : "secondary"}
        size="sm"
        onClick={() => {
          setFormError({ message: null, reference: null });
          reset(defaultsOf(task));
          setOpen(true);
        }}
      >
        {task ? (
          <>
            Edit<span className="sr-only"> task {task.name}</span>
          </>
        ) : (
          "Add task"
        )}
      </Button>

      <Dialog
        open={open}
        onClose={() => {
          if (!mutation.isPending) setOpen(false);
        }}
        title={task ? `Edit task ${task.name}` : "Add task"}
        dismissible={!mutation.isPending}
        size="lg"
        footer={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button type="submit" form={formId} variant="primary" loading={mutation.isPending}>
              {task ? "Save task" : "Add task"}
            </Button>
          </>
        }
      >
        <form id={formId} method="post" noValidate onSubmit={(event) => void handleSubmit(onSubmit)(event)} className="flex flex-col gap-4">
          <FormError message={formError.message} reference={formError.reference} />
          <div className="grid gap-4 sm:grid-cols-2">
            <FormField label="Task name" required error={errors.name?.message ?? null} className="sm:col-span-2" hint="Unique within the project.">
              <Input {...register("name")} />
            </FormField>
            <FormField label="Description" error={errors.description?.message ?? null} className="sm:col-span-2">
              <Textarea rows={2} {...register("description")} />
            </FormField>
            <FormField
              label="Hourly rate"
              error={errors.hourly_rate?.message ?? null}
              hint="Used when the person has no rate of their own on this project."
            >
              <Controller
                control={control}
                name="hourly_rate"
                render={({ field }) => (
                  <NumericInput nonNegative currency={currency} value={field.value} onValueChange={field.onChange} onBlur={field.onBlur} />
                )}
              />
            </FormField>
            <FormField label="Estimated hours" error={errors.estimated_hours?.message ?? null}>
              <Controller
                control={control}
                name="estimated_hours"
                render={({ field }) => (
                  <QuantityInput scale={2} suffix="h" value={field.value} onValueChange={field.onChange} onBlur={field.onBlur} />
                )}
              />
            </FormField>
            <FormField
              label="Service item"
              error={errors.service_item_id?.message ?? null}
              className="sm:col-span-2"
              hint="Overrides the project's service item when this task's time is invoiced."
            >
              <Controller
                control={control}
                name="service_item_id"
                render={({ field }) => <ItemPicker usage="sales" value={field.value} onChange={field.onChange} />}
              />
            </FormField>
          </div>
          <Checkbox
            label="Billable"
            hint={
              chargeable
                ? "Approved hours on this task can be invoiced."
                : "This project's billing method means no task's time is invoiced, whatever this setting."
            }
            {...register("is_billable")}
          />
          {task ? (
            <Checkbox
              label="Active"
              hint="Time cannot be logged against an inactive task. Existing entries are kept."
              {...register("is_active")}
            />
          ) : null}
        </form>
      </Dialog>
    </>
  );
}
