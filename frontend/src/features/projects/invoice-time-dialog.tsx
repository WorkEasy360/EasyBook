"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Controller, useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FormError, FormField, Input, Textarea } from "@/components/ui/field";
import { PercentInput } from "@/components/ui/numeric-input";
import { useToast } from "@/components/ui/toast";
import { useOrg } from "@/components/providers/org-provider";
import { AccountPicker } from "@/features/shared/pickers";
import { useIdempotentMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import { formatDate, todayInZone } from "@/lib/datetime";
import { isValidDecimal, money } from "@/lib/money";
import type { Invoice } from "@/types/api/sales";
import type { InvoiceProjectTimeInput } from "@/types/api/projects";

/**
 * Bill a project's approved, unbilled, billable time.
 *
 * POST /projects/{id}/invoice-time/ creates ONE DRAFT sales invoice — one line
 * per (task, rate) — and marks the entries invoiced at once, so the same hours
 * can never reach a second draft (projects/services/billing.py). It posts
 * nothing: the draft is reviewed and posted from the invoice page like any
 * other. The response is the invoice (InvoiceSerializer), so success goes
 * straight to it.
 */

const DATE = /^\d{4}-\d{2}-\d{2}$/;

const schema = z
  .object({
    invoice_date: z.string().regex(DATE, "Enter the invoice date."),
    due_date: z.string().regex(DATE, "Enter the due date."),
    receivable_account_id: z.string().min(1, "Choose the receivable account."),
    tax_rate: z
      .string()
      .refine((value) => value.trim() === "" || (isValidDecimal(value) && money(value).gte(0)), "The tax rate cannot be negative."),
    tax_payable_account_id: z.string().nullable(),
    reference: z.string().max(100),
    notes: z.string().max(4000),
  })
  .refine((values) => values.due_date >= values.invoice_date, {
    message: "The due date cannot be before the invoice date.",
    path: ["due_date"],
  })
  .refine((values) => values.tax_rate.trim() === "" || money(values.tax_rate).eq(0) || Boolean(values.tax_payable_account_id), {
    message: "Choose where output tax is credited.",
    path: ["tax_payable_account_id"],
  });

type Values = z.infer<typeof schema>;

export function InvoiceTimeDialogButton({
  projectId,
  projectCode,
  upToDate,
  entryCount,
}: {
  projectId: string;
  projectCode: string;
  /** The preview's cut-off, so the invoice bills exactly the rows on screen. */
  upToDate: string | null;
  entryCount: number;
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
  const today = todayInZone(org.timeZone);

  const {
    control,
    register,
    handleSubmit,
    setError,
    formState: { errors },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      invoice_date: today,
      due_date: today,
      receivable_account_id: "",
      tax_rate: "",
      tax_payable_account_id: null,
      reference: "",
      notes: "",
    },
  });
  const taxRate = useWatch({ control, name: "tax_rate" });

  const mutation = useIdempotentMutation<Invoice, InvoiceProjectTimeInput>(
    // Billing writes a sales invoice and flips time entries to invoiced.
    "projects",
    (input, idempotencyKey) => api.post<Invoice>(`projects/${projectId}/invoice-time`, input, { idempotencyKey }),
    {
      onSuccess: (invoice) => {
        setOpen(false);
        toast.push({ tone: "success", title: "Draft invoice created", description: "Review it, then post it to bill the customer." });
        router.push(`/sales/invoices/${invoice.id}`);
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
    const hasTax = values.tax_rate.trim() !== "" && !money(values.tax_rate).eq(0);
    mutation.mutate({
      invoice_date: values.invoice_date,
      due_date: values.due_date,
      receivable_account_id: values.receivable_account_id,
      up_to_date: upToDate,
      tax_rate: hasTax ? values.tax_rate : "0",
      tax_payable_account_id: hasTax ? values.tax_payable_account_id : null,
      reference: values.reference.trim(),
      notes: values.notes,
    });
  }

  const hasTax = taxRate.trim() !== "" && isValidDecimal(taxRate) && !money(taxRate).eq(0);

  return (
    <>
      <Button
        variant="primary"
        size="sm"
        onClick={() => {
          setFormError({ message: null, reference: null });
          setOpen(true);
        }}
      >
        Invoice time
      </Button>

      <Dialog
        open={open}
        onClose={() => {
          if (!mutation.isPending) setOpen(false);
        }}
        title="Invoice approved time"
        description={`Bills the ${entryCount} ${entryCount === 1 ? "entry" : "entries"} listed${upToDate ? ` up to ${formatDate(upToDate)}` : ""} as a draft invoice.`}
        dismissible={!mutation.isPending}
        size="lg"
        footer={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button type="submit" form={formId} variant="primary" loading={mutation.isPending}>
              Create draft invoice
            </Button>
          </>
        }
      >
        <form id={formId} method="post" noValidate onSubmit={(event) => void handleSubmit(onSubmit)(event)} className="flex flex-col gap-4">
          <p className="text-sm text-ink-700">
            The entries are marked invoiced as soon as the draft exists, so they cannot be billed twice. Nothing reaches
            the ledger until the invoice is posted.
          </p>
          <FormError message={formError.message} reference={formError.reference} />
          <div className="grid gap-4 sm:grid-cols-2">
            <FormField label="Invoice date" required error={errors.invoice_date?.message ?? null}>
              <Input type="date" {...register("invoice_date")} />
            </FormField>
            <FormField label="Due date" required error={errors.due_date?.message ?? null}>
              <Input type="date" {...register("due_date")} />
            </FormField>
            <FormField
              label="Receivable account"
              required
              error={errors.receivable_account_id?.message ?? null}
              className="sm:col-span-2"
              hint="The asset account the invoice is owed into."
            >
              <Controller
                control={control}
                name="receivable_account_id"
                render={({ field }) => (
                  <AccountPicker accountType="asset" value={field.value || null} onChange={(value) => field.onChange(value ?? "")} />
                )}
              />
            </FormField>
            <FormField label="Tax rate" error={errors.tax_rate?.message ?? null} hint="Applied to every line. Leave blank for none.">
              <Controller
                control={control}
                name="tax_rate"
                render={({ field }) => (
                  <PercentInput nonNegative value={field.value} onValueChange={field.onChange} onBlur={field.onBlur} />
                )}
              />
            </FormField>
            <FormField
              label="Tax payable account"
              required={hasTax}
              error={errors.tax_payable_account_id?.message ?? null}
              hint={hasTax ? "Output tax is credited here." : "Needed only when a tax rate is set."}
            >
              <Controller
                control={control}
                name="tax_payable_account_id"
                render={({ field }) => (
                  <AccountPicker accountType="liability" value={field.value} onChange={field.onChange} disabled={!hasTax} />
                )}
              />
            </FormField>
            <FormField
              label="Reference"
              error={errors.reference?.message ?? null}
              hint={`Left blank, the invoice is referenced ${projectCode}.`}
            >
              <Input {...register("reference")} />
            </FormField>
            <FormField label="Notes" error={errors.notes?.message ?? null} className="sm:col-span-2">
              <Textarea rows={2} {...register("notes")} />
            </FormField>
          </div>
        </form>
      </Dialog>
    </>
  );
}
