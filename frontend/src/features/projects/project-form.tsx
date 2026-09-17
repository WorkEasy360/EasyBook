"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Controller, useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { FormError, FormField, Input, Select, Textarea } from "@/components/ui/field";
import { NumericInput, QuantityInput } from "@/components/ui/numeric-input";
import { BILLING_METHOD_LABELS } from "@/components/ui/status-badge";
import { useToast } from "@/components/ui/toast";
import { useOrg } from "@/components/providers/org-provider";
import { CustomerPicker, ItemPicker } from "@/features/shared/pickers";
import { useUnsavedChanges } from "@/lib/hooks/use-unsaved-changes";
import { useIdempotentMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import { isValidDecimal, money } from "@/lib/money";
import type { BillingMethod, Project, ProjectInput, ProjectUpdateInput } from "@/types/api/projects";

/**
 * Create or edit a project.
 *
 * Creating a project posts nothing (projects/CLAUDE.md: the module never
 * raises a journal). The billing method is the upper bound on what time can
 * ever be charged, and it is immutable once the project exists
 * (`billing_method_immutable`) — as are the customer, code and currency,
 * which ProjectUpdateSerializer does not read. On edit those are shown fixed.
 */

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const METHODS = Object.keys(BILLING_METHOD_LABELS) as [BillingMethod, ...BillingMethod[]];

const optionalAmount = (label: string) =>
  z.string().refine((value) => value.trim() === "" || (isValidDecimal(value) && money(value).gte(0)), `${label} cannot be negative.`);

const schema = z
  .object({
    customer_id: z.string().min(1, "Choose a customer."),
    project_code: z.string().trim().min(1, "A project code is required.").max(32),
    name: z.string().trim().min(1, "A name is required.").max(255),
    description: z.string().max(4000),
    billing_method: z.enum(METHODS),
    default_hourly_rate: optionalAmount("The hourly rate"),
    fixed_fee_amount: optionalAmount("The fee"),
    service_item_id: z.string().nullable(),
    budget_hours: optionalAmount("Budget hours"),
    budget_amount: optionalAmount("The budget"),
    start_date: z.string().refine((value) => value === "" || DATE.test(value), "Enter a valid date."),
    end_date: z.string().refine((value) => value === "" || DATE.test(value), "Enter a valid date."),
    notes: z.string().max(4000),
  })
  // projects/services/projects.py :: _validate_billing_configuration
  .refine((values) => values.billing_method !== "fixed_fee" || (values.fixed_fee_amount !== "" && money(values.fixed_fee_amount).gt(0)), {
    message: "A fixed-fee project needs a fee above zero.",
    path: ["fixed_fee_amount"],
  })
  // `project_end_before_start`. ISO dates compare correctly as strings.
  .refine((values) => !values.start_date || !values.end_date || values.end_date >= values.start_date, {
    message: "The end date cannot be before the start date.",
    path: ["end_date"],
  });

type Values = z.infer<typeof schema>;

const blankToNull = (value: string) => (value.trim() === "" ? null : value);

function toValues(project: Project | undefined, customerId: string | undefined): Values {
  return {
    customer_id: project?.customer ?? customerId ?? "",
    project_code: project?.project_code ?? "",
    name: project?.name ?? "",
    description: project?.description ?? "",
    billing_method: project?.billing_method ?? "hourly",
    default_hourly_rate: project?.default_hourly_rate ?? "",
    fixed_fee_amount: project?.fixed_fee_amount ?? "",
    service_item_id: project?.service_item ?? null,
    budget_hours: project?.budget_hours ?? "",
    budget_amount: project?.budget_amount ?? "",
    start_date: project?.start_date ?? "",
    end_date: project?.end_date ?? "",
    notes: project?.notes ?? "",
  };
}

export function ProjectForm({ project, customerId }: { project?: Project; customerId?: string }) {
  const router = useRouter();
  const toast = useToast();
  const org = useOrg();
  const isEdit = Boolean(project);
  const currency = project?.currency ?? org.currency;
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });

  const {
    control,
    register,
    handleSubmit,
    setError,
    formState: { errors, isDirty, isSubmitting },
  } = useForm<Values>({ resolver: zodResolver(schema), defaultValues: toValues(project, customerId) });
  useUnsavedChanges(isDirty && !isSubmitting);

  const billingMethod = useWatch({ control, name: "billing_method" });

  const mutation = useIdempotentMutation<Project, ProjectInput | ProjectUpdateInput>(
    "projects",
    (input, idempotencyKey) =>
      project
        ? api.patch<Project>(`projects/${project.id}`, input)
        : api.post<Project>("projects", input, { idempotencyKey }),
    {
      onSuccess: (saved) => {
        toast.push({ tone: "success", title: isEdit ? "Project saved" : "Project created", description: saved.name });
        router.push(`/projects/${saved.id}`);
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
    const shared = {
      name: values.name,
      description: values.description,
      // Only the controls shown for this billing method are sent, so an edit
      // never silently clears a value the form did not display.
      ...(values.billing_method === "hourly"
        ? { default_hourly_rate: blankToNull(values.default_hourly_rate), service_item_id: values.service_item_id }
        : {}),
      // `fixed_fee_amount_unexpected`: only a fixed-fee project may carry one.
      ...(values.billing_method === "fixed_fee" ? { fixed_fee_amount: blankToNull(values.fixed_fee_amount) } : {}),
      budget_hours: blankToNull(values.budget_hours),
      budget_amount: blankToNull(values.budget_amount),
      start_date: blankToNull(values.start_date),
      end_date: blankToNull(values.end_date),
      notes: values.notes,
    };
    if (project) {
      mutation.mutate(shared);
      return;
    }
    mutation.mutate({
      ...shared,
      customer_id: values.customer_id,
      project_code: values.project_code.trim(),
      billing_method: values.billing_method,
      currency: org.currency,
    });
  }

  const lockedHint = isEdit ? "Fixed once the project exists." : undefined;

  return (
    <form method="post" noValidate onSubmit={(event) => void handleSubmit(onSubmit)(event)} className="flex flex-col gap-4">
      <FormError message={formError.message} reference={formError.reference} />

      <Card>
        <CardHeader title="Project" />
        <CardBody className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <FormField
            label="Customer"
            required
            error={errors.customer_id?.message ?? null}
            {...(lockedHint ? { hint: lockedHint } : {})}
          >
            <Controller
              control={control}
              name="customer_id"
              render={({ field }) => (
                <CustomerPicker value={field.value || null} onChange={(value) => field.onChange(value ?? "")} disabled={isEdit} />
              )}
            />
          </FormField>
          <FormField
            label="Project code"
            required
            disabled={isEdit}
            error={errors.project_code?.message ?? null}
            hint={lockedHint ?? "Unique in this organization. Used as the reference on invoices for its time."}
          >
            <Input className="tabular" {...register("project_code")} />
          </FormField>
          <FormField label="Name" required error={errors.name?.message ?? null}>
            <Input {...register("name")} />
          </FormField>
          <FormField label="Description" error={errors.description?.message ?? null} className="sm:col-span-2 lg:col-span-3">
            <Textarea rows={2} {...register("description")} />
          </FormField>
          <FormField label="Start date" error={errors.start_date?.message ?? null} hint="Time cannot be logged before it.">
            <Input type="date" {...register("start_date")} />
          </FormField>
          <FormField label="End date" error={errors.end_date?.message ?? null} hint="Time cannot be logged after it.">
            <Input type="date" {...register("end_date")} />
          </FormField>
          <FormField label="Currency" disabled hint={isEdit ? "Fixed once the project exists." : "The organization currency."}>
            <Input value={currency} readOnly />
          </FormField>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Billing"
          description="How this project's time can be charged. The method cannot be changed later: logged time is priced under the method in force when it was recorded."
        />
        <CardBody className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <FormField
            label="Billing method"
            required
            disabled={isEdit}
            error={errors.billing_method?.message ?? null}
            hint={
              lockedHint ??
              (billingMethod === "hourly"
                ? "Billable task hours are invoiced at the resolved hourly rate."
                : billingMethod === "fixed_fee"
                  ? "Hours are tracked for cost only; invoice the agreed fee through sales."
                  : "Hours are tracked for cost and reporting, never invoiced.")
            }
          >
            <Select {...register("billing_method")}>
              {METHODS.map((method) => (
                <option key={method} value={method}>
                  {BILLING_METHOD_LABELS[method]}
                </option>
              ))}
            </Select>
          </FormField>

          {billingMethod === "hourly" ? (
            <FormField
              label="Default hourly rate"
              error={errors.default_hourly_rate?.message ?? null}
              hint="Used when neither the person's project rate nor the task sets one."
            >
              <Controller
                control={control}
                name="default_hourly_rate"
                render={({ field }) => (
                  <NumericInput nonNegative currency={currency} value={field.value} onValueChange={field.onChange} onBlur={field.onBlur} />
                )}
              />
            </FormField>
          ) : null}

          {billingMethod === "fixed_fee" ? (
            <FormField label="Fixed fee" required error={errors.fixed_fee_amount?.message ?? null}>
              <Controller
                control={control}
                name="fixed_fee_amount"
                render={({ field }) => (
                  <NumericInput nonNegative currency={currency} value={field.value} onValueChange={field.onChange} onBlur={field.onBlur} />
                )}
              />
            </FormField>
          ) : null}

          {billingMethod === "hourly" ? (
            <FormField
              label="Service item"
              error={errors.service_item_id?.message ?? null}
              hint="The service item time is invoiced as, unless a task names its own. Must be a sellable service."
            >
              <Controller
                control={control}
                name="service_item_id"
                render={({ field }) => <ItemPicker usage="sales" value={field.value} onChange={field.onChange} />}
              />
            </FormField>
          ) : null}

          <FormField label="Budget hours" error={errors.budget_hours?.message ?? null}>
            <Controller
              control={control}
              name="budget_hours"
              render={({ field }) => (
                <QuantityInput scale={2} value={field.value} onValueChange={field.onChange} onBlur={field.onBlur} suffix="h" />
              )}
            />
          </FormField>
          <FormField label="Budget amount" error={errors.budget_amount?.message ?? null}>
            <Controller
              control={control}
              name="budget_amount"
              render={({ field }) => (
                <NumericInput nonNegative currency={currency} value={field.value} onValueChange={field.onChange} onBlur={field.onBlur} />
              )}
            />
          </FormField>
          <FormField label="Notes" error={errors.notes?.message ?? null} className="sm:col-span-2 lg:col-span-3">
            <Textarea rows={2} {...register("notes")} />
          </FormField>
        </CardBody>
      </Card>

      <div className="flex items-center justify-end gap-2">
        <Button variant="secondary" onClick={() => router.back()} disabled={mutation.isPending}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={mutation.isPending} loadingLabel="Saving">
          {isEdit ? "Save project" : "Create project"}
        </Button>
      </div>
    </form>
  );
}
