"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Controller, FormProvider, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Checkbox, FormError, FormField, Input, Select, Textarea } from "@/components/ui/field";
import { RECURRING_FREQUENCY_LABELS } from "@/components/ui/status-badge";
import { useToast } from "@/components/ui/toast";
import { useOrg } from "@/components/providers/org-provider";
import { AccountPicker, VendorPicker } from "@/features/shared/pickers";
import {
  EMPTY_PRICED_LINE,
  EstimatedTotals,
  PricedLinesEditor,
  toPricedLineInput,
} from "@/features/documents/priced-lines-editor";
import { useUnsavedChanges } from "@/lib/hooks/use-unsaved-changes";
import { useApiMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { formErrorOf, referenceOf } from "@/lib/api/errors";
import { todayInZone } from "@/lib/datetime";
import { serverFieldErrors } from "./form-errors";
import { purchaseLineSchema } from "./line-schema";
import { LineExpenseAccountField, isInventoried, needsExpenseAccount, useItemIndex } from "./line-extras";
import type {
  RecurringBillTemplate,
  RecurringBillTemplateInput,
  RecurringBillTemplateUpdateInput,
  RecurringFrequency,
} from "@/types/api/purchases";

/**
 * Create or edit a recurring bill template (services/recurring.py).
 *
 * A template posts nothing. On each run date the scheduled task generates an
 * ordinary DRAFT bill from it, priced by the engine at that moment; the
 * template itself holds only the inputs, so there are no template totals —
 * the panel beside the lines is an estimate of one generated bill.
 *
 * Inventory-tracked items are refused (recurring_item_inventoried): stock
 * arrives on a goods receipt, not on a schedule. This is for subscriptions,
 * rent and retainers.
 *
 * RecurringBillTemplateUpdateSerializer accepts frequency, end date, due
 * days, reference, notes, active flag and lines — at any status. The vendor,
 * start date and accounts are fixed once created. There is no "generate now"
 * endpoint; generation is Celery-only.
 *
 * Templates are not financial documents themselves and have no idempotent
 * create on the backend, so an ordinary mutation is used; a duplicate would
 * be a visible second template, never a second posting.
 */

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const FREQUENCIES = Object.keys(RECURRING_FREQUENCY_LABELS) as [RecurringFrequency, ...RecurringFrequency[]];

const lineSchema = purchaseLineSchema.extend({ expense_account_id: z.string().nullable() });

const schema = z
  .object({
    vendor_id: z.string().min(1, "Choose a vendor."),
    frequency: z.enum(FREQUENCIES),
    start_date: z.string().regex(DATE, "Enter the first run date."),
    end_date: z.union([z.literal(""), z.string().regex(DATE, "Enter a valid date.")]),
    due_days: z.number({ error: "Enter a number of days." }).int("Enter whole days.").min(0, "Cannot be negative."),
    payable_account_id: z.string().min(1, "Choose the payable account."),
    tax_recoverable_account_id: z.string().nullable(),
    reference: z.string().max(255, "At most 255 characters."),
    notes: z.string().max(4000),
    is_active: z.boolean(),
    lines: z.array(lineSchema).min(1, "Add at least one line."),
  })
  // services/recurring.py :: recurring_end_before_start.
  .refine((values) => values.end_date === "" || values.end_date >= values.start_date, {
    message: "The end date cannot be before the start date.",
    path: ["end_date"],
  });

type Values = z.infer<typeof schema>;

const EMPTY_LINE: Values["lines"][number] = { ...EMPTY_PRICED_LINE, expense_account_id: null };

const FIELDS = [
  "vendor_id",
  "frequency",
  "start_date",
  "end_date",
  "due_days",
  "payable_account_id",
  "tax_recoverable_account_id",
  "reference",
  "notes",
] as const;

export function RecurringBillForm({ template, vendorId }: { template?: RecurringBillTemplate; vendorId?: string }) {
  const router = useRouter();
  const toast = useToast();
  const org = useOrg();
  const isEdit = Boolean(template);
  const items = useItemIndex();
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: template
      ? {
          vendor_id: template.vendor,
          frequency: template.frequency,
          start_date: template.start_date,
          end_date: template.end_date ?? "",
          due_days: template.due_days,
          payable_account_id: template.payable_account,
          tax_recoverable_account_id: template.tax_recoverable_account,
          reference: template.reference,
          notes: template.notes,
          is_active: template.is_active,
          lines: template.lines.map((line) => ({
            item_id: line.item,
            description: line.description,
            quantity: line.quantity,
            unit_price: line.unit_price,
            discount_percent: line.discount_percent,
            tax_rate: line.tax_rate,
            expense_account_id: line.expense_account,
          })),
        }
      : {
          vendor_id: vendorId ?? "",
          frequency: "monthly",
          start_date: todayInZone(org.timeZone),
          end_date: "",
          due_days: 0,
          payable_account_id: "",
          tax_recoverable_account_id: null,
          reference: "",
          notes: "",
          is_active: true,
          lines: [{ ...EMPTY_LINE }],
        },
  });
  const {
    control,
    register,
    handleSubmit,
    setError,
    setValue,
    getValues,
    formState: { errors, isDirty, isSubmitting },
  } = form;

  useUnsavedChanges(isDirty && !isSubmitting);

  const mutation = useApiMutation<RecurringBillTemplate, RecurringBillTemplateInput | RecurringBillTemplateUpdateInput>(
    "purchases/recurring-bills",
    (input) =>
      template
        ? api.patch<RecurringBillTemplate>(`purchases/recurring-bills/${template.id}`, input)
        : api.post<RecurringBillTemplate>("purchases/recurring-bills", input),
    {
      onSuccess: (saved) => {
        toast.push({ tone: "success", title: isEdit ? "Recurring bill updated" : "Recurring bill created" });
        router.push(`/purchases/recurring-bills/${saved.id}`);
        router.refresh();
      },
      onError: (error) => {
        setFormError({ message: formErrorOf(error), reference: referenceOf(error) });
        const pairs = serverFieldErrors(error, FIELDS, {
          recurring_end_before_start: "end_date",
          vendor_inactive: "vendor_id",
        });
        for (const [field, message] of pairs) {
          setError(field as (typeof FIELDS)[number], { type: "server", message });
        }
      },
    },
  );

  function onSubmit(values: Values) {
    setFormError({ message: null, reference: null });

    let blocked = false;
    values.lines.forEach((line, index) => {
      const item = items.get(line.item_id);
      if (item && isInventoried(item)) {
        setError(`lines.${index}.item_id`, {
          type: "client",
          message: "Stock-tracked items cannot recur. Receive them on a goods receipt instead.",
        });
        blocked = true;
      } else if (needsExpenseAccount(item, line.expense_account_id)) {
        setError(`lines.${index}.expense_account_id`, {
          type: "client",
          message: "This item has no purchase account. Choose an expense account.",
        });
        blocked = true;
      }
    });
    if (blocked) return;

    const lines = values.lines.map((line) => ({ ...toPricedLineInput(line), expense_account_id: line.expense_account_id }));
    const endDate = values.end_date === "" ? null : values.end_date;

    if (template) {
      mutation.mutate({
        frequency: values.frequency,
        end_date: endDate,
        due_days: values.due_days,
        reference: values.reference,
        notes: values.notes,
        lines,
      });
      return;
    }
    mutation.mutate({
      vendor_id: values.vendor_id,
      frequency: values.frequency,
      start_date: values.start_date,
      end_date: endDate,
      due_days: values.due_days,
      payable_account_id: values.payable_account_id,
      tax_recoverable_account_id: values.tax_recoverable_account_id,
      reference: values.reference,
      notes: values.notes,
      is_active: values.is_active,
      lines,
    });
  }

  const lockedHint = isEdit ? "Fixed once the template is created." : undefined;

  return (
    <FormProvider {...form}>
      <form method="post" noValidate onSubmit={(event) => void handleSubmit(onSubmit)(event)} className="flex flex-col gap-4">
        <FormError message={formError.message} reference={formError.reference} />

        <Card>
          <CardHeader title="Schedule" />
          <CardBody className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <FormField
              label="Vendor"
              required
              error={errors.vendor_id?.message ?? null}
              className="sm:col-span-2 lg:col-span-1"
              {...(lockedHint ? { hint: lockedHint } : {})}
            >
              <Controller
                control={control}
                name="vendor_id"
                render={({ field }) => (
                  <VendorPicker
                    value={field.value || null}
                    onChange={(value) => field.onChange(value ?? "")}
                    onSelectVendor={(vendor) => {
                      if (vendor?.default_payable_account && !getValues("payable_account_id")) {
                        setValue("payable_account_id", vendor.default_payable_account, { shouldDirty: true });
                      }
                    }}
                    disabled={isEdit}
                  />
                )}
              />
            </FormField>

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
              hint={lockedHint ?? "The first bill is generated on this date."}
            >
              <Input type="date" disabled={isEdit} {...register("start_date")} />
            </FormField>

            <FormField label="End date" error={errors.end_date?.message ?? null} hint="Leave blank to run until deactivated.">
              <Input type="date" {...register("end_date")} />
            </FormField>

            <FormField
              label="Due after (days)"
              required
              error={errors.due_days?.message ?? null}
              hint="Each generated bill is due this many days after its bill date."
            >
              <Input type="number" min={0} numeric {...register("due_days", { valueAsNumber: true })} />
            </FormField>

            <FormField label="Reference" error={errors.reference?.message ?? null}>
              <Input {...register("reference")} />
            </FormField>

            <FormField
              label="Payable account"
              required
              error={errors.payable_account_id?.message ?? null}
              hint={lockedHint ?? "A liability account, copied to each generated bill."}
            >
              <Controller
                control={control}
                name="payable_account_id"
                render={({ field }) => (
                  <AccountPicker
                    accountType="liability"
                    value={field.value || null}
                    onChange={(value) => field.onChange(value ?? "")}
                    disabled={isEdit}
                  />
                )}
              />
            </FormField>

            <FormField
              label="Tax recoverable account"
              error={errors.tax_recoverable_account_id?.message ?? null}
              hint={lockedHint ?? "An asset account. Needed to post generated bills whose lines carry tax."}
            >
              <Controller
                control={control}
                name="tax_recoverable_account_id"
                render={({ field }) => (
                  <AccountPicker accountType="asset" value={field.value} onChange={field.onChange} disabled={isEdit} />
                )}
              />
            </FormField>

            {isEdit ? null : (
              <div className="flex items-end pb-2">
                <Checkbox label="Active" hint="Inactive templates generate nothing." {...register("is_active")} />
              </div>
            )}
          </CardBody>
        </Card>

        <PricedLinesEditor
          usage="purchase"
          emptyLine={EMPTY_LINE}
          description="Services and untracked items only. Each generated bill is priced by the accounting engine when it is created."
          renderExtra={(index) => <LineExpenseAccountField index={index} items={items} />}
        />

        <div className="grid gap-4 lg:grid-cols-5">
          <Card className="lg:col-span-3">
            <CardHeader title="Notes" />
            <CardBody>
              <FormField label="Notes" error={errors.notes?.message ?? null} hint="Copied to each generated bill.">
                <Textarea rows={3} {...register("notes")} />
              </FormField>
            </CardBody>
          </Card>
          <div className="lg:col-span-2">
            <EstimatedTotals currency={org.currency} />
          </div>
        </div>

        <div className="flex items-center justify-end gap-2">
          <Button variant="secondary" onClick={() => router.back()} disabled={mutation.isPending}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" loading={mutation.isPending} loadingLabel="Saving">
            {isEdit ? "Save changes" : "Create recurring bill"}
          </Button>
        </div>
      </form>
    </FormProvider>
  );
}
