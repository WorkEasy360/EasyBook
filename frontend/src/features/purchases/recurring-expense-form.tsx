"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Controller, useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Checkbox, FormError, FormField, Input, Select, Textarea } from "@/components/ui/field";
import { NumericInput, PercentInput } from "@/components/ui/numeric-input";
import { TotalsPanel } from "@/components/ui/detail";
import { Money } from "@/components/ui/money";
import { RECURRING_FREQUENCY_LABELS } from "@/components/ui/status-badge";
import { useToast } from "@/components/ui/toast";
import { useOrg } from "@/components/providers/org-provider";
import { AccountPicker, VendorPicker } from "@/features/shared/pickers";
import { useUnsavedChanges } from "@/lib/hooks/use-unsaved-changes";
import { useApiMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { formErrorOf, referenceOf } from "@/lib/api/errors";
import { isValidDecimal, money } from "@/lib/money";
import { todayInZone } from "@/lib/datetime";
import { serverFieldErrors } from "./form-errors";
import { estimateExpense } from "./quantities";
import type {
  RecurringExpenseTemplate,
  RecurringExpenseTemplateInput,
  RecurringExpenseTemplateUpdateInput,
  RecurringFrequency,
} from "@/types/api/purchases";

/**
 * Create or edit a recurring expense template (services/recurring.py).
 *
 * The template posts nothing. On each run date the scheduled task creates an
 * ordinary DRAFT expense with these accounts, amount and rate; tax and total
 * are computed then, so the panel here is an estimate of one occurrence.
 *
 * RecurringExpenseTemplateUpdateSerializer accepts frequency, end date,
 * amount, tax rate, description, reference, notes and the active flag. The
 * vendor, start date and accounts are fixed once created.
 */

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const FREQUENCIES = Object.keys(RECURRING_FREQUENCY_LABELS) as [RecurringFrequency, ...RecurringFrequency[]];

const schema = z
  .object({
    vendor_id: z.string().nullable(),
    frequency: z.enum(FREQUENCIES),
    start_date: z.string().regex(DATE, "Enter the first run date."),
    end_date: z.union([z.literal(""), z.string().regex(DATE, "Enter a valid date.")]),
    description: z.string().max(255, "At most 255 characters."),
    amount: z.string().refine((value) => isValidDecimal(value) && money(value).gt(0), "Enter an amount above zero."),
    tax_rate: z
      .string()
      .refine((value) => value.trim() === "" || (isValidDecimal(value) && money(value).gte(0)), "The tax rate cannot be negative."),
    expense_account_id: z.string().min(1, "Choose the expense account."),
    paid_through_account_id: z.string().min(1, "Choose the account it is paid through."),
    tax_recoverable_account_id: z.string().nullable(),
    reference: z.string().max(255, "At most 255 characters."),
    notes: z.string().max(4000),
    is_active: z.boolean(),
  })
  .refine((values) => values.end_date === "" || values.end_date >= values.start_date, {
    message: "The end date cannot be before the start date.",
    path: ["end_date"],
  });

type Values = z.infer<typeof schema>;
const FIELDS = Object.keys(schema.shape);

export function RecurringExpenseForm({ template, vendorId }: { template?: RecurringExpenseTemplate; vendorId?: string }) {
  const router = useRouter();
  const toast = useToast();
  const org = useOrg();
  const isEdit = Boolean(template);
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
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: template
      ? {
          vendor_id: template.vendor,
          frequency: template.frequency,
          start_date: template.start_date,
          end_date: template.end_date ?? "",
          description: template.description,
          amount: template.amount,
          tax_rate: template.tax_rate,
          expense_account_id: template.expense_account,
          paid_through_account_id: template.paid_through_account,
          tax_recoverable_account_id: template.tax_recoverable_account,
          reference: template.reference,
          notes: template.notes,
          is_active: template.is_active,
        }
      : {
          vendor_id: vendorId ?? null,
          frequency: "monthly",
          start_date: todayInZone(org.timeZone),
          end_date: "",
          description: "",
          amount: "",
          tax_rate: "",
          expense_account_id: "",
          paid_through_account_id: "",
          tax_recoverable_account_id: null,
          reference: "",
          notes: "",
          is_active: true,
        },
  });

  useUnsavedChanges(isDirty && !isSubmitting);
  const amount = useWatch({ control, name: "amount" });
  const taxRate = useWatch({ control, name: "tax_rate" });
  const estimate = estimateExpense(amount, taxRate);

  const mutation = useApiMutation<RecurringExpenseTemplate, RecurringExpenseTemplateInput | RecurringExpenseTemplateUpdateInput>(
    "purchases/recurring-expenses",
    (input) =>
      template
        ? api.patch<RecurringExpenseTemplate>(`purchases/recurring-expenses/${template.id}`, input)
        : api.post<RecurringExpenseTemplate>("purchases/recurring-expenses", input),
    {
      onSuccess: (saved) => {
        toast.push({ tone: "success", title: isEdit ? "Recurring expense updated" : "Recurring expense created" });
        router.push(`/purchases/recurring-expenses/${saved.id}`);
        router.refresh();
      },
      onError: (error) => {
        setFormError({ message: formErrorOf(error), reference: referenceOf(error) });
        const pairs = serverFieldErrors(error, FIELDS, {
          recurring_end_before_start: "end_date",
          expense_amount_invalid: "amount",
          vendor_inactive: "vendor_id",
        });
        for (const [field, message] of pairs) {
          setError(field as keyof Values, { type: "server", message });
        }
      },
    },
  );

  function onSubmit(values: Values) {
    setFormError({ message: null, reference: null });
    const rate = values.tax_rate.trim() === "" ? "0" : values.tax_rate;
    const endDate = values.end_date === "" ? null : values.end_date;
    if (template) {
      mutation.mutate({
        frequency: values.frequency,
        end_date: endDate,
        amount: values.amount,
        tax_rate: rate,
        description: values.description,
        reference: values.reference,
        notes: values.notes,
      });
      return;
    }
    mutation.mutate({
      vendor_id: values.vendor_id,
      frequency: values.frequency,
      start_date: values.start_date,
      end_date: endDate,
      description: values.description,
      amount: values.amount,
      tax_rate: rate,
      expense_account_id: values.expense_account_id,
      paid_through_account_id: values.paid_through_account_id,
      tax_recoverable_account_id: values.tax_recoverable_account_id,
      reference: values.reference,
      notes: values.notes,
      is_active: values.is_active,
    });
  }

  const lockedHint = isEdit ? "Fixed once the template is created." : undefined;

  return (
    <form method="post" noValidate onSubmit={(event) => void handleSubmit(onSubmit)(event)} className="flex flex-col gap-4">
      <FormError message={formError.message} reference={formError.reference} />

      <Card>
        <CardHeader title="Schedule" />
        <CardBody className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <FormField label="Frequency" required error={errors.frequency?.message ?? null}>
            <Select {...register("frequency")}>
              {FREQUENCIES.map((frequency) => (
                <option key={frequency} value={frequency}>
                  {RECURRING_FREQUENCY_LABELS[frequency]}
                </option>
              ))}
            </Select>
          </FormField>

          <FormField
            label="Start date"
            required
            error={errors.start_date?.message ?? null}
            hint={lockedHint ?? "The first expense is generated on this date."}
          >
            <Input type="date" disabled={isEdit} {...register("start_date")} />
          </FormField>

          <FormField label="End date" error={errors.end_date?.message ?? null} hint="Leave blank to run until deactivated.">
            <Input type="date" {...register("end_date")} />
          </FormField>

          <FormField label="Vendor" error={errors.vendor_id?.message ?? null} hint={lockedHint ?? "Optional."}>
            <Controller
              control={control}
              name="vendor_id"
              render={({ field }) => <VendorPicker value={field.value} onChange={field.onChange} disabled={isEdit} />}
            />
          </FormField>

          <FormField label="Description" error={errors.description?.message ?? null} className="lg:col-span-2">
            <Input placeholder="e.g. Office rent" {...register("description")} />
          </FormField>

          <FormField label="Reference" error={errors.reference?.message ?? null}>
            <Input {...register("reference")} />
          </FormField>

          {isEdit ? null : (
            <div className="flex items-end pb-2">
              <Checkbox label="Active" hint="Inactive templates generate nothing." {...register("is_active")} />
            </div>
          )}
        </CardBody>
      </Card>

      <div className="grid gap-4 lg:grid-cols-5">
        <Card className="lg:col-span-3">
          <CardHeader title="Amount and accounts" />
          <CardBody className="grid gap-4 sm:grid-cols-2">
            <FormField label="Amount" required error={errors.amount?.message ?? null} hint="Before tax, each occurrence.">
              <Controller
                control={control}
                name="amount"
                render={({ field }) => (
                  <NumericInput nonNegative value={field.value} onValueChange={field.onChange} onBlur={field.onBlur} />
                )}
              />
            </FormField>

            <FormField label="Tax rate" error={errors.tax_rate?.message ?? null} hint="Leave blank for no tax.">
              <Controller
                control={control}
                name="tax_rate"
                render={({ field }) => (
                  <PercentInput nonNegative value={field.value} onValueChange={field.onChange} onBlur={field.onBlur} />
                )}
              />
            </FormField>

            <FormField label="Expense account" required error={errors.expense_account_id?.message ?? null} hint={lockedHint}>
              <Controller
                control={control}
                name="expense_account_id"
                render={({ field }) => (
                  <AccountPicker
                    accountType="expense"
                    value={field.value || null}
                    onChange={(value) => field.onChange(value ?? "")}
                    disabled={isEdit}
                  />
                )}
              />
            </FormField>

            <FormField
              label="Paid through"
              required
              error={errors.paid_through_account_id?.message ?? null}
              hint={lockedHint ?? "A bank or cash account, or a payable (liability) account."}
            >
              <Controller
                control={control}
                name="paid_through_account_id"
                render={({ field }) => (
                  <AccountPicker value={field.value || null} onChange={(value) => field.onChange(value ?? "")} disabled={isEdit} />
                )}
              />
            </FormField>

            <FormField
              label="Tax recoverable account"
              error={errors.tax_recoverable_account_id?.message ?? null}
              hint={lockedHint ?? "An asset account. Needed to post generated expenses that carry tax."}
            >
              <Controller
                control={control}
                name="tax_recoverable_account_id"
                render={({ field }) => (
                  <AccountPicker accountType="asset" value={field.value} onChange={field.onChange} disabled={isEdit} />
                )}
              />
            </FormField>
          </CardBody>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader
            title="Each occurrence"
            description="Estimated. Each generated expense is calculated by the server when it is created."
          />
          <CardBody className="flex flex-col items-end gap-2">
            <TotalsPanel
              rows={[
                { label: "Amount", value: <Money value={estimate?.amount ?? null} currency={org.currency} /> },
                { label: "Tax", value: <Money value={estimate?.tax ?? null} currency={org.currency} /> },
                { label: "Total (estimate)", value: <Money value={estimate?.total ?? null} currency={org.currency} strong />, emphasis: true },
              ]}
            />
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader title="Notes" />
        <CardBody>
          <FormField label="Notes" error={errors.notes?.message ?? null} hint="Copied to each generated expense.">
            <Textarea rows={3} {...register("notes")} />
          </FormField>
        </CardBody>
      </Card>

      <div className="flex items-center justify-end gap-2">
        <Button variant="secondary" onClick={() => router.back()} disabled={mutation.isPending}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={mutation.isPending} loadingLabel="Saving">
          {isEdit ? "Save changes" : "Create recurring expense"}
        </Button>
      </div>
    </form>
  );
}
