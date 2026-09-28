"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Controller, FormProvider, useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { FormError, FormField, Input, Textarea } from "@/components/ui/field";
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
import { useIdempotentMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import { addDays, todayInZone } from "@/lib/datetime";
import type { Customer, Invoice, InvoiceInput, InvoiceUpdateInput } from "@/types/api/sales";

/**
 * Create or edit a DRAFT invoice.
 *
 * Saving creates a draft and nothing else: no ledger entry, no stock issue, no
 * number consumed. Posting is a separate, confirmed action on the invoice page
 * (sales/services/invoices.py :: post_invoice), so the draft can be reviewed
 * with the engine's real totals before it becomes a legal document.
 *
 * Once a draft exists the backend only lets its dates, reference, notes,
 * terms and lines change (InvoiceDetailView.update); the customer, accounts
 * and warehouse are shown read-only rather than offered and then ignored.
 */

const DATE = /^\d{4}-\d{2}-\d{2}$/;

const schema = z
  .object({
    customer_id: z.string().min(1, "Choose a customer."),
    invoice_date: z.string().regex(DATE, "Enter the invoice date."),
    due_date: z.string().regex(DATE, "Enter the due date."),
    reference: z.string().max(100),
    receivable_account_id: z.string().min(1, "Choose the receivable account."),
    tax_payable_account_id: z.string().nullable(),
    warehouse_id: z.string().nullable(),
    notes: z.string().max(4000),
    terms: z.string().max(4000),
    lines: z
      .array(pricedLineSchema.extend({ source_delivery_challan_line_id: z.string().nullable() }))
      .min(1, "Add at least one line."),
  })
  .refine((values) => values.due_date >= values.invoice_date, {
    // ISO dates compare correctly as strings.
    message: "The due date cannot be before the invoice date.",
    path: ["due_date"],
  });

export type InvoiceFormValues = z.infer<typeof schema>;

/** Defaults a page can supply: a preselected customer, remembered accounts, lines from a delivery. */
export type InvoiceFormInitial = Partial<Omit<InvoiceFormValues, "lines">> & {
  lines?: InvoiceFormValues["lines"];
};

function toValues(invoice: Invoice | undefined, initial: InvoiceFormInitial, today: string): InvoiceFormValues {
  if (invoice) {
    return {
      customer_id: invoice.customer,
      invoice_date: invoice.invoice_date,
      due_date: invoice.due_date,
      reference: invoice.reference,
      receivable_account_id: invoice.receivable_account,
      tax_payable_account_id: invoice.tax_payable_account,
      warehouse_id: invoice.warehouse,
      notes: invoice.notes,
      terms: invoice.terms,
      lines: invoice.lines.map((line) => ({
        ...fromPricedLine(line),
        source_delivery_challan_line_id: line.source_delivery_challan_line,
      })),
    };
  }
  return {
    customer_id: initial.customer_id ?? "",
    invoice_date: initial.invoice_date ?? today,
    due_date: initial.due_date ?? today,
    reference: initial.reference ?? "",
    receivable_account_id: initial.receivable_account_id ?? "",
    tax_payable_account_id: initial.tax_payable_account_id ?? null,
    warehouse_id: initial.warehouse_id ?? null,
    notes: initial.notes ?? "",
    terms: initial.terms ?? "",
    lines: initial.lines?.length ? initial.lines : [{ ...EMPTY_PRICED_LINE, source_delivery_challan_line_id: null }],
  };
}

export function InvoiceForm({
  invoice,
  initial = {},
  rememberedFrom,
}: {
  invoice?: Invoice;
  initial?: InvoiceFormInitial;
  /** Invoice number the default accounts were taken from, for the hint. */
  rememberedFrom?: string | null;
}) {
  const router = useRouter();
  const toast = useToast();
  const org = useOrg();
  const isEdit = Boolean(invoice);
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });

  const form = useForm<InvoiceFormValues>({
    resolver: zodResolver(schema),
    defaultValues: toValues(invoice, initial, todayInZone(org.timeZone)),
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

  // Payment terms drive the due date until the user sets one themselves.
  const [termsDays, setTermsDays] = React.useState<number | null>(null);
  const invoiceDate = useWatch({ control, name: "invoice_date" });
  const tax = useWatch({ control, name: "tax_payable_account_id" });

  function applyTerms(days: number | null, date: string) {
    if (days === null || getFieldState("due_date").isDirty) return;
    const due = addDays(date, days);
    if (due) setValue("due_date", due, { shouldValidate: true });
  }

  function onCustomerSelected(customer: Customer | null) {
    const days = customer?.payment_terms_days ?? null;
    setTermsDays(days);
    applyTerms(days, invoiceDate);
  }

  const mutation = useIdempotentMutation<Invoice, InvoiceInput | InvoiceUpdateInput>(
    "sales/invoices",
    (input, idempotencyKey) =>
      invoice
        ? api.patch<Invoice>(`sales/invoices/${invoice.id}`, input)
        : api.post<Invoice>("sales/invoices", input, { idempotencyKey }),
    {
      onSuccess: (saved) => {
        toast.push({ tone: "success", title: isEdit ? "Draft invoice saved" : "Draft invoice created" });
        router.push(`/sales/invoices/${saved.id}`);
        router.refresh();
      },
      onError: (error) => {
        setFormError({ message: formErrorOf(error), reference: referenceOf(error) });
        for (const [field, messages] of Object.entries(fieldErrorsOf(error))) {
          if (field !== "lines" && field in schema.shape && messages[0]) {
            setError(field as keyof InvoiceFormValues, { type: "server", message: messages[0] });
          }
        }
      },
    },
  );

  function onSubmit(values: InvoiceFormValues) {
    setFormError({ message: null, reference: null });
    const lines = values.lines.map((line) => ({
      ...toPricedLineInput(line),
      ...(line.source_delivery_challan_line_id
        ? { source_delivery_challan_line_id: line.source_delivery_challan_line_id }
        : {}),
    }));

    if (invoice) {
      mutation.mutate({
        invoice_date: values.invoice_date,
        due_date: values.due_date,
        reference: values.reference,
        notes: values.notes,
        terms: values.terms,
        lines,
      });
      return;
    }

    mutation.mutate({
      customer_id: values.customer_id,
      currency: org.currency,
      invoice_date: values.invoice_date,
      due_date: values.due_date,
      reference: values.reference,
      receivable_account_id: values.receivable_account_id,
      tax_payable_account_id: values.tax_payable_account_id,
      warehouse_id: values.warehouse_id,
      notes: values.notes,
      terms: values.terms,
      lines,
    });
  }

  const lockedHint = isEdit ? "Fixed once the draft is saved." : undefined;

  return (
    <FormProvider {...form}>
      <form method="post" noValidate onSubmit={(event) => void handleSubmit(onSubmit)(event)} className="flex flex-col gap-4">
        <FormError message={formError.message} reference={formError.reference} />

        <Card>
          <CardHeader title="Invoice" />
          <CardBody className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <FormField
              label="Customer"
              required
              error={errors.customer_id?.message ?? null}
              className="sm:col-span-2 lg:col-span-1"
              {...(lockedHint ? { hint: lockedHint } : {})}
            >
              <Controller
                control={control}
                name="customer_id"
                render={({ field }) => (
                  <CustomerPicker
                    value={field.value || null}
                    onChange={(value) => field.onChange(value ?? "")}
                    onSelectCustomer={onCustomerSelected}
                    disabled={isEdit}
                  />
                )}
              />
            </FormField>

            <FormField label="Invoice date" required error={errors.invoice_date?.message ?? null}>
              <Input
                type="date"
                {...register("invoice_date", {
                  onChange: (event: React.ChangeEvent<HTMLInputElement>) => applyTerms(termsDays, event.target.value),
                })}
              />
            </FormField>

            <FormField
              label="Due date"
              required
              error={errors.due_date?.message ?? null}
              {...(termsDays !== null ? { hint: `Customer terms: net ${termsDays} days.` } : {})}
            >
              <Input type="date" {...register("due_date")} />
            </FormField>

            <FormField label="Reference" error={errors.reference?.message ?? null} hint="A PO number or your own reference.">
              <Input {...register("reference")} />
            </FormField>

            <FormField
              label="Receivable account"
              required
              error={errors.receivable_account_id?.message ?? null}
              hint={
                lockedHint ??
                (rememberedFrom ? `Taken from invoice ${rememberedFrom}.` : "The asset account this invoice is owed into.")
              }
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
              hint={lockedHint ?? (tax ? "Output tax is credited here." : "Needed when any line carries tax.")}
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
              hint={lockedHint ?? "Stock is issued from here on posting, for tracked products not already delivered."}
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
          emptyLine={{ ...EMPTY_PRICED_LINE, source_delivery_challan_line_id: null }}
          description="Enter the tax rate that applies to each line. CGST/SGST or IGST is decided on posting from the place of supply."
        />

        <div className="grid gap-4 lg:grid-cols-5">
          <Card className="lg:col-span-3">
            <CardHeader title="Notes and terms" description="Printed on the invoice." />
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
            {isEdit ? "Save draft" : "Save as draft"}
          </Button>
        </div>
      </form>
    </FormProvider>
  );
}
