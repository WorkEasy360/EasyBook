"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Controller, useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FormError, FormField, Input, Select } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { AccountPicker, WarehousePicker } from "@/features/shared/pickers";
import { useIdempotentMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import type { QuoteConvertInput, QuoteConvertResult } from "@/types/api/sales";

/**
 * Converts an ACCEPTED quote into a draft sales order or a draft invoice
 * (POST sales/quotes/{id}/convert/).
 *
 * Not a <DocumentAction>: an invoice target needs dates and accounts, which a
 * plain confirm cannot collect. It keeps the same guarantees — one
 * Idempotency-Key per submission, errors kept inside the dialog — and then
 * navigates to the document the server created, so everything seen next is
 * the backend's.
 *
 * The backend copies lines, prices, tax treatment, notes and terms from the
 * quote (convert_quote_to_invoice / convert_quote_to_sales_order). A quote
 * converts at most once per target, so a target already used is offered
 * disabled.
 */

const DATE = /^\d{4}-\d{2}-\d{2}$/;

const schema = z
  .object({
    target: z.enum(["sales_order", "invoice"]),
    order_date: z.string(),
    invoice_date: z.string(),
    due_date: z.string(),
    receivable_account_id: z.string(),
    tax_payable_account_id: z.string().nullable(),
    warehouse_id: z.string().nullable(),
  })
  .superRefine((values, ctx) => {
    const date = (field: "order_date" | "invoice_date" | "due_date", required: boolean) => {
      const value = values[field];
      if (value === "" ? required : !DATE.test(value)) {
        ctx.addIssue({ code: "custom", path: [field], message: required ? "Enter a date." : "Enter a valid date." });
      }
    };
    if (values.target === "sales_order") {
      date("order_date", false);
      return;
    }
    date("invoice_date", false);
    date("due_date", true);
    if (!values.receivable_account_id) {
      ctx.addIssue({ code: "custom", path: ["receivable_account_id"], message: "Choose the receivable account." });
    }
    if (values.invoice_date && values.due_date && values.due_date < values.invoice_date) {
      ctx.addIssue({ code: "custom", path: ["due_date"], message: "The due date cannot be before the invoice date." });
    }
  });

type Values = z.infer<typeof schema>;

export interface QuoteConvertDefaults {
  today: string;
  dueDate: string;
  receivableAccountId: string | null;
  taxPayableAccountId: string | null;
  warehouseId: string | null;
  /** Invoice number the default accounts were taken from, for the hint. */
  rememberedFrom: string | null;
  /** Customer payment terms, for the due-date hint. */
  termsDays: number | null;
}

export function QuoteConvertDialog({
  quoteId,
  quoteNumber,
  issueDate,
  defaults,
  converted,
}: {
  quoteId: string;
  quoteNumber: string;
  issueDate: string;
  defaults: QuoteConvertDefaults;
  /** Targets this quote has already been converted to, when known. */
  converted: { sales_order: boolean; invoice: boolean };
}) {
  const router = useRouter();
  const toast = useToast();
  const [open, setOpen] = React.useState(false);
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });

  const {
    control,
    register,
    handleSubmit,
    setError,
    formState: { errors },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      target: converted.invoice ? "sales_order" : "invoice",
      order_date: defaults.today,
      invoice_date: defaults.today,
      due_date: defaults.dueDate,
      receivable_account_id: defaults.receivableAccountId ?? "",
      tax_payable_account_id: defaults.taxPayableAccountId,
      warehouse_id: defaults.warehouseId,
    },
  });
  const target = useWatch({ control, name: "target" });

  const mutation = useIdempotentMutation<QuoteConvertResult, QuoteConvertInput>(
    "sales/quotes",
    (input, idempotencyKey) =>
      api.post<QuoteConvertResult>(`sales/quotes/${quoteId}/convert`, input, { idempotencyKey }),
    {
      // "sales/quotes" already cascades to orders and invoices (query-keys.ts).
      onSuccess: (created) => {
        setOpen(false);
        const isInvoice = "invoice_number" in created;
        toast.push({ tone: "success", title: isInvoice ? "Draft invoice created" : `Sales order ${created.order_number} created` });
        router.push(isInvoice ? `/sales/invoices/${created.id}` : `/sales/orders/${created.id}`);
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
    if (values.target === "sales_order") {
      // A null date is rejected by the serializer; omit it to default to the quote date.
      mutation.mutate({ target: "sales_order", ...(values.order_date ? { order_date: values.order_date } : {}) });
      return;
    }
    mutation.mutate({
      target: "invoice",
      ...(values.invoice_date ? { invoice_date: values.invoice_date } : {}),
      due_date: values.due_date,
      receivable_account_id: values.receivable_account_id,
      tax_payable_account_id: values.tax_payable_account_id,
      warehouse_id: values.warehouse_id,
    });
  }

  const formId = React.useId();
  const bothUsed = converted.invoice && converted.sales_order;

  return (
    <>
      <Button variant="primary" onClick={() => setOpen(true)} disabled={bothUsed}>
        Convert
      </Button>

      <Dialog
        open={open}
        onClose={() => {
          if (!mutation.isPending) setOpen(false);
        }}
        title={`Convert ${quoteNumber}`}
        description="Creates a new draft with this quote's lines, prices, tax treatment, notes and terms. Nothing is posted until that draft is confirmed or posted."
        size="md"
        dismissible={!mutation.isPending}
        footer={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button type="submit" form={formId} variant="primary" loading={mutation.isPending} loadingLabel="Converting">
              {target === "invoice" ? "Create draft invoice" : "Create sales order"}
            </Button>
          </>
        }
      >
        <form
          id={formId}
          method="post"
          noValidate
          onSubmit={(event) => void handleSubmit(onSubmit)(event)}
          className="flex flex-col gap-4"
        >
          <FormError message={formError.message} reference={formError.reference} />

          <FormField
            label="Convert to"
            required
            error={errors.target?.message ?? null}
            hint="A quote can become one sales order and one invoice, each once."
          >
            <Select {...register("target")}>
              <option value="invoice" disabled={converted.invoice}>
                Invoice{converted.invoice ? " (already converted)" : ""}
              </option>
              <option value="sales_order" disabled={converted.sales_order}>
                Sales order{converted.sales_order ? " (already converted)" : ""}
              </option>
            </Select>
          </FormField>

          {target === "sales_order" ? (
            <FormField
              label="Order date"
              error={errors.order_date?.message ?? null}
              hint={`Leave blank to use the quote date (${issueDate}).`}
            >
              <Input type="date" {...register("order_date")} />
            </FormField>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2">
              <FormField
                label="Invoice date"
                error={errors.invoice_date?.message ?? null}
                hint="Leave blank to use the quote date."
              >
                <Input type="date" {...register("invoice_date")} />
              </FormField>
              <FormField
                label="Due date"
                required
                error={errors.due_date?.message ?? null}
                {...(defaults.termsDays !== null ? { hint: `Customer terms: net ${defaults.termsDays} days.` } : {})}
              >
                <Input type="date" {...register("due_date")} />
              </FormField>
              <FormField
                label="Receivable account"
                required
                className="sm:col-span-2"
                error={errors.receivable_account_id?.message ?? null}
                hint={
                  defaults.rememberedFrom
                    ? `Taken from invoice ${defaults.rememberedFrom}. Fixed once the invoice exists.`
                    : "The asset account the invoice is owed into. Fixed once the invoice exists."
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
                    />
                  )}
                />
              </FormField>
              <FormField
                label="Tax payable account"
                error={errors.tax_payable_account_id?.message ?? null}
                hint="Needed to post when any line carries tax."
              >
                <Controller
                  control={control}
                  name="tax_payable_account_id"
                  render={({ field }) => (
                    <AccountPicker accountType="liability" value={field.value} onChange={field.onChange} />
                  )}
                />
              </FormField>
              <FormField
                label="Warehouse"
                error={errors.warehouse_id?.message ?? null}
                hint="Required when the quote has tracked products; they are issued from here when the invoice is posted."
              >
                <Controller
                  control={control}
                  name="warehouse_id"
                  render={({ field }) => <WarehousePicker value={field.value} onChange={field.onChange} />}
                />
              </FormField>
            </div>
          )}
        </form>
      </Dialog>
    </>
  );
}
