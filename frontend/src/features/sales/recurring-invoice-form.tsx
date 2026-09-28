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
import { AccountPicker, CustomerPicker, WarehousePicker } from "@/features/shared/pickers";
import {
  EMPTY_PRICED_LINE,
  EstimatedTotals,
  PricedLinesEditor,
  fromPricedLine,
  pricedLineSchema,
  toPricedLineInput,
} from "@/features/documents/priced-lines-editor";
import { useUnsavedChanges } from "@/lib/hooks/use-unsaved-changes";
import { useIdempotentMutation, useLookup } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import { todayInZone } from "@/lib/datetime";
import type { Item } from "@/types/api/items";
import type {
  RecurringFrequency,
  RecurringInvoiceTemplate,
  RecurringInvoiceTemplateInput,
  RecurringInvoiceTemplateUpdateInput,
} from "@/types/api/sales";

/**
 * Create or edit a recurring invoice template.
 *
 * A template posts nothing itself. The scheduler (sales/tasks.py, hourly)
 * creates one DRAFT invoice per due occurrence from it; each draft is posted
 * by a person like any other invoice (services/recurring_invoices.py ::
 * generate_due_invoices).
 *
 * Once created, only due days, end date, reference, notes, terms and lines
 * can change (RecurringInvoiceTemplateUpdateSerializer) — the backend
 * silently ignores anything else, so those fields are shown read-only rather
 * than accepted and dropped. Activation has its own actions on the template
 * page. The update does not re-check end date against start date, so the
 * form does.
 *
 * Nor does the backend require a warehouse on the template — but every
 * invoice generated from it is created with the template's warehouse, and
 * create_invoice refuses a tracked-product line without one
 * (warehouse_required). A template missing it would be accepted today and
 * fail at every scheduled run, so the form requires it up front.
 */

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const FREQUENCIES = Object.keys(RECURRING_FREQUENCY_LABELS) as [RecurringFrequency, ...RecurringFrequency[]];

const schema = z
  .object({
    customer_id: z.string().min(1, "Choose a customer."),
    frequency: z.enum(FREQUENCIES),
    start_date: z.string().regex(DATE, "Enter the first invoice date."),
    end_date: z.string().refine((value) => value === "" || DATE.test(value), "Enter a valid date or leave it blank."),
    due_days: z.string().regex(/^\d{1,4}$/, "Enter a whole number of days."),
    is_active: z.boolean(),
    receivable_account_id: z.string().min(1, "Choose the receivable account."),
    tax_payable_account_id: z.string().nullable(),
    warehouse_id: z.string().nullable(),
    reference: z.string().max(100),
    notes: z.string().max(4000),
    terms: z.string().max(4000),
    lines: z.array(pricedLineSchema).min(1, "Add at least one line."),
  })
  .refine((values) => values.end_date === "" || values.end_date >= values.start_date, {
    // recurring_end_before_start — enforced on create only by the backend.
    message: "The end date cannot be before the start date.",
    path: ["end_date"],
  });

type Values = z.infer<typeof schema>;

export interface RecurringInvoiceFormInitial {
  customer_id?: string;
  receivable_account_id?: string | null;
  tax_payable_account_id?: string | null;
  warehouse_id?: string | null;
  rememberedFrom?: string | null;
}

export function RecurringInvoiceForm({
  template,
  initial = {},
}: {
  template?: RecurringInvoiceTemplate;
  initial?: RecurringInvoiceFormInitial;
}) {
  const router = useRouter();
  const toast = useToast();
  const org = useOrg();
  const isEdit = Boolean(template);
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: template
      ? {
          customer_id: template.customer,
          frequency: template.frequency,
          start_date: template.start_date,
          end_date: template.end_date ?? "",
          due_days: String(template.due_days),
          is_active: template.is_active,
          receivable_account_id: template.receivable_account,
          tax_payable_account_id: template.tax_payable_account,
          warehouse_id: template.warehouse,
          reference: template.reference,
          notes: template.notes,
          terms: template.terms,
          lines: template.lines.map(fromPricedLine),
        }
      : {
          customer_id: initial.customer_id ?? "",
          frequency: "monthly",
          start_date: todayInZone(org.timeZone),
          end_date: "",
          due_days: "0",
          is_active: true,
          receivable_account_id: initial.receivable_account_id ?? "",
          tax_payable_account_id: initial.tax_payable_account_id ?? null,
          warehouse_id: initial.warehouse_id ?? null,
          reference: "",
          notes: "",
          terms: "",
          lines: [{ ...EMPTY_PRICED_LINE }],
        },
  });
  const {
    control,
    register,
    handleSubmit,
    setValue,
    setError,
    getFieldState,
    formState: { errors, isDirty, isSubmitting },
  } = form;

  useUnsavedChanges(isDirty && !isSubmitting);

  // Same query key as ItemPicker's, so this reads the picker's cached page.
  const items = useLookup<Item>("items", "items", "", { extraParams: { page_size: 200, is_active: "true" } });

  const mutation = useIdempotentMutation<
    RecurringInvoiceTemplate,
    RecurringInvoiceTemplateInput | RecurringInvoiceTemplateUpdateInput
  >(
    "sales/recurring-invoices",
    (input, idempotencyKey) =>
      template
        ? api.patch<RecurringInvoiceTemplate>(`sales/recurring-invoices/${template.id}`, input)
        : api.post<RecurringInvoiceTemplate>("sales/recurring-invoices", input, { idempotencyKey }),
    {
      onSuccess: (saved) => {
        toast.push({ tone: "success", title: isEdit ? "Recurring invoice saved" : "Recurring invoice created" });
        router.push(`/sales/recurring-invoices/${saved.id}`);
        router.refresh();
      },
      onError: (error) => {
        setFormError({ message: formErrorOf(error), reference: referenceOf(error) });
        for (const [field, messages] of Object.entries(fieldErrorsOf(error))) {
          if (field !== "lines" && field in schema.shape && messages[0]) {
            setError(field as keyof Values, { type: "server", message: messages[0] });
          }
        }
      },
    },
  );

  function onSubmit(values: Values) {
    setFormError({ message: null, reference: null });
    const itemById = new Map((items.data?.results ?? []).map((item) => [item.id, item]));
    const tracked = values.lines.some((line) => {
      const item = itemById.get(line.item_id);
      return item?.item_type === "product" && item.track_inventory;
    });
    if (tracked && !values.warehouse_id) {
      if (template) {
        setFormError({
          message: "This template has no warehouse, so it cannot bill products that track inventory. Remove those lines or create a new template with a warehouse.",
          reference: null,
        });
      } else {
        setError("warehouse_id", {
          type: "validate",
          message: "Choose a warehouse: tracked products on each generated invoice are issued from it.",
        });
      }
      return;
    }
    const editable = {
      due_days: Number(values.due_days),
      end_date: values.end_date || null,
      reference: values.reference,
      notes: values.notes,
      terms: values.terms,
      lines: values.lines.map(toPricedLineInput),
    };
    if (template) {
      mutation.mutate(editable);
      return;
    }
    mutation.mutate({
      ...editable,
      customer_id: values.customer_id,
      currency: org.currency,
      frequency: values.frequency,
      start_date: values.start_date,
      is_active: values.is_active,
      receivable_account_id: values.receivable_account_id,
      tax_payable_account_id: values.tax_payable_account_id,
      warehouse_id: values.warehouse_id,
    });
  }

  const lockedHint = isEdit ? "Fixed once the template exists." : undefined;

  return (
    <FormProvider {...form}>
      <form method="post" noValidate onSubmit={(event) => void handleSubmit(onSubmit)(event)} className="flex flex-col gap-4">
        <FormError message={formError.message} reference={formError.reference} />

        <Card>
          <CardHeader title="Schedule" />
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
                  <CustomerPicker
                    value={field.value || null}
                    onChange={(value) => field.onChange(value ?? "")}
                    onSelectCustomer={(customer) => {
                      // Customer terms are the natural default for due days,
                      // until the user types their own.
                      if (customer && !getFieldState("due_days").isDirty) {
                        setValue("due_days", String(customer.payment_terms_days), { shouldValidate: true });
                      }
                    }}
                    disabled={isEdit}
                  />
                )}
              />
            </FormField>

            <FormField label="Frequency" required error={errors.frequency?.message ?? null} {...(lockedHint ? { hint: lockedHint } : {})}>
              {/* Not registered when locked: RHF reads a natively disabled select as undefined. */}
              {isEdit ? (
                <Select defaultValue={template?.frequency} disabled>
                  {FREQUENCIES.map((frequency) => (
                    <option key={frequency} value={frequency}>
                      {RECURRING_FREQUENCY_LABELS[frequency]}
                    </option>
                  ))}
                </Select>
              ) : (
                <Select {...register("frequency")}>
                  {FREQUENCIES.map((frequency) => (
                    <option key={frequency} value={frequency}>
                      {RECURRING_FREQUENCY_LABELS[frequency]}
                    </option>
                  ))}
                </Select>
              )}
            </FormField>

            <FormField
              label="Start date"
              required
              error={errors.start_date?.message ?? null}
              hint={lockedHint ?? "The first invoice is dated this day."}
            >
              <Input type="date" readOnly={isEdit} {...register("start_date")} />
            </FormField>

            <FormField label="End date" error={errors.end_date?.message ?? null} hint="Optional. No invoices are generated after it.">
              <Input type="date" {...register("end_date")} />
            </FormField>

            <FormField label="Due after (days)" required error={errors.due_days?.message ?? null} hint="Each invoice's due date is its date plus this many days.">
              <Input inputMode="numeric" className="tabular" {...register("due_days")} />
            </FormField>

            <FormField label="Reference" error={errors.reference?.message ?? null} hint="Copied to every generated invoice.">
              <Input {...register("reference")} />
            </FormField>

            {isEdit ? null : (
              <div className="sm:col-span-2 lg:col-span-3">
                <Checkbox
                  label="Active"
                  hint="An inactive template generates nothing until it is activated."
                  {...register("is_active")}
                />
              </div>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Posting accounts" description="Applied to every invoice this template generates." />
          <CardBody className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <FormField
              label="Receivable account"
              required
              error={errors.receivable_account_id?.message ?? null}
              hint={lockedHint ?? (initial.rememberedFrom ? `Taken from invoice ${initial.rememberedFrom}.` : "The asset account invoices are owed into.")}
            >
              <Controller
                control={control}
                name="receivable_account_id"
                render={({ field }) => (
                  <AccountPicker
                    accountType="asset"
                    value={field.value || null}
                    onChange={(value) => field.onChange(value ?? "")}
                    disabled={isEdit}
                  />
                )}
              />
            </FormField>

            <FormField
              label="Tax payable account"
              error={errors.tax_payable_account_id?.message ?? null}
              hint={lockedHint ?? "Needed to post the generated invoices when any line carries tax."}
            >
              <Controller
                control={control}
                name="tax_payable_account_id"
                render={({ field }) => (
                  <AccountPicker accountType="liability" value={field.value} onChange={field.onChange} disabled={isEdit} />
                )}
              />
            </FormField>

            <FormField
              label="Warehouse"
              error={errors.warehouse_id?.message ?? null}
              hint={lockedHint ?? "Required for products that track inventory; each generated invoice issues them from here when posted."}
            >
              <Controller
                control={control}
                name="warehouse_id"
                render={({ field }) => <WarehousePicker value={field.value} onChange={field.onChange} disabled={isEdit} />}
              />
            </FormField>
          </CardBody>
        </Card>

        <PricedLinesEditor
          usage="sales"
          description="Every generated invoice gets these lines. Amounts are calculated on each invoice when it is created."
        />

        <div className="grid gap-4 lg:grid-cols-5">
          <Card className="lg:col-span-3">
            <CardHeader title="Notes and terms" description="Copied to every generated invoice." />
            <CardBody className="flex flex-col gap-4">
              <FormField label="Notes" error={errors.notes?.message ?? null}>
                <Textarea rows={3} {...register("notes")} />
              </FormField>
              <FormField label="Terms" error={errors.terms?.message ?? null}>
                <Textarea rows={3} {...register("terms")} />
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
            {isEdit ? "Save changes" : "Create recurring invoice"}
          </Button>
        </div>
      </form>
    </FormProvider>
  );
}
