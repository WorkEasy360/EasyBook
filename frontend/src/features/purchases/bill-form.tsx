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
import { AccountPicker, VendorPicker, WarehousePicker } from "@/features/shared/pickers";
import {
  EMPTY_PRICED_LINE,
  EstimatedTotals,
  PricedLinesEditor,
  fromPricedLine,
  toPricedLineInput,
} from "@/features/documents/priced-lines-editor";
import { useUnsavedChanges } from "@/lib/hooks/use-unsaved-changes";
import { useIdempotentMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { formErrorOf, referenceOf } from "@/lib/api/errors";
import { addDays, todayInZone } from "@/lib/datetime";
import { serverFieldErrors } from "./form-errors";
import { purchaseLineSchema } from "./line-schema";
import { LineExpenseAccountField, LineSourceNote, isInventoried, needsExpenseAccount, useItemIndex } from "./line-extras";
import type { Bill, BillInput, BillLineInput, BillUpdateInput, Vendor } from "@/types/api/purchases";

/**
 * Create or edit a DRAFT bill — what a vendor says we owe.
 *
 * Saving records nothing in the ledger and moves no stock. Posting is a
 * separate, confirmed action on the bill page (services/bills.py ::
 * post_bill): it credits the payable account, debits inventory or expense
 * per line plus recoverable tax, and receives stock for inventoried lines
 * that are not already linked to a goods receipt line.
 *
 * Backend rules surfaced here rather than discovered on save:
 *  - an inventoried line with no linked receipt line needs the bill's
 *    warehouse (warehouse_required);
 *  - a non-inventoried line needs an expense account from the line or the
 *    item (item_missing_purchase_account) — see line-extras.tsx;
 *  - the vendor's bill number must be unique per vendor
 *    (duplicate_vendor_bill_number).
 *
 * Once a draft exists BillDetailView.update accepts only its dates,
 * reference, vendor bill number, notes and lines; the vendor, accounts,
 * warehouse and purchase order are shown read-only.
 */

const DATE = /^\d{4}-\d{2}-\d{2}$/;

const lineSchema = purchaseLineSchema.extend({
  expense_account_id: z.string().nullable(),
  source_order_line_id: z.string().nullable(),
  source_goods_receipt_line_id: z.string().nullable(),
});

const schema = z
  .object({
    vendor_id: z.string().min(1, "Choose a vendor."),
    bill_date: z.string().regex(DATE, "Enter the bill date."),
    due_date: z.string().regex(DATE, "Enter the due date."),
    vendor_bill_number: z.string().max(64, "At most 64 characters."),
    reference: z.string().max(255, "At most 255 characters."),
    payable_account_id: z.string().min(1, "Choose the payable account."),
    tax_recoverable_account_id: z.string().nullable(),
    price_variance_account_id: z.string().nullable(),
    warehouse_id: z.string().nullable(),
    notes: z.string().max(4000),
    lines: z.array(lineSchema).min(1, "Add at least one line."),
  })
  .refine((values) => values.due_date >= values.bill_date, {
    message: "The due date cannot be before the bill date.",
    path: ["due_date"],
  });

export type BillFormValues = z.infer<typeof schema>;
export type BillLineValues = BillFormValues["lines"][number];
export type BillFormInitial = Partial<Omit<BillFormValues, "lines">> & {
  lines?: BillLineValues[];
  source_purchase_order_id?: string;
  /** For the header description only. */
  order_number?: string;
};

const EMPTY_LINE: BillLineValues = {
  ...EMPTY_PRICED_LINE,
  expense_account_id: null,
  source_order_line_id: null,
  source_goods_receipt_line_id: null,
};

const FIELDS = [
  "vendor_id",
  "bill_date",
  "due_date",
  "vendor_bill_number",
  "reference",
  "payable_account_id",
  "tax_recoverable_account_id",
  "price_variance_account_id",
  "warehouse_id",
  "notes",
] as const;

function toValues(bill: Bill | undefined, initial: BillFormInitial, today: string): BillFormValues {
  if (bill) {
    return {
      vendor_id: bill.vendor,
      bill_date: bill.bill_date,
      due_date: bill.due_date,
      vendor_bill_number: bill.vendor_bill_number,
      reference: bill.reference,
      payable_account_id: bill.payable_account,
      tax_recoverable_account_id: bill.tax_recoverable_account,
      price_variance_account_id: bill.price_variance_account,
      warehouse_id: bill.warehouse,
      notes: bill.notes,
      lines: bill.lines.map((line) => ({
        ...fromPricedLine(line),
        expense_account_id: line.expense_account,
        source_order_line_id: line.source_order_line,
        source_goods_receipt_line_id: line.source_goods_receipt_line,
      })),
    };
  }
  return {
    vendor_id: initial.vendor_id ?? "",
    bill_date: initial.bill_date ?? today,
    due_date: initial.due_date ?? today,
    vendor_bill_number: initial.vendor_bill_number ?? "",
    reference: initial.reference ?? "",
    payable_account_id: initial.payable_account_id ?? "",
    tax_recoverable_account_id: initial.tax_recoverable_account_id ?? null,
    price_variance_account_id: initial.price_variance_account_id ?? null,
    warehouse_id: initial.warehouse_id ?? null,
    notes: initial.notes ?? "",
    lines: initial.lines?.length ? initial.lines : [{ ...EMPTY_LINE }],
  };
}

function toLineInput(line: BillLineValues): BillLineInput {
  return {
    ...toPricedLineInput(line),
    expense_account_id: line.expense_account_id,
    source_order_line_id: line.source_order_line_id,
    source_goods_receipt_line_id: line.source_goods_receipt_line_id,
  };
}

export function BillForm({
  bill,
  initial = {},
  rememberedFrom,
}: {
  bill?: Bill;
  initial?: BillFormInitial;
  /** Bill number the default accounts were taken from, for the hint. */
  rememberedFrom?: string | null;
}) {
  const router = useRouter();
  const toast = useToast();
  const org = useOrg();
  const isEdit = Boolean(bill);
  const fromOrder = Boolean(bill?.source_purchase_order ?? initial.source_purchase_order_id);
  const items = useItemIndex();
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });

  const form = useForm<BillFormValues>({
    resolver: zodResolver(schema),
    defaultValues: toValues(bill, initial, todayInZone(org.timeZone)),
  });
  const {
    control,
    register,
    handleSubmit,
    setValue,
    setError,
    getValues,
    getFieldState,
    formState: { errors, isDirty, isSubmitting },
  } = form;

  useUnsavedChanges(isDirty && !isSubmitting);

  // Vendor terms drive the due date until the user sets one; the vendor's
  // default payable account fills an empty account field. Both are
  // suggestions — the bill stores its own values.
  const [termsDays, setTermsDays] = React.useState<number | null>(null);
  const billDate = useWatch({ control, name: "bill_date" });
  const lines = useWatch({ control, name: "lines" });
  const warehouse = useWatch({ control, name: "warehouse_id" });

  function applyTerms(days: number | null, date: string) {
    if (days === null || getFieldState("due_date").isDirty) return;
    const due = addDays(date, days);
    if (due) setValue("due_date", due, { shouldValidate: true });
  }

  function onVendorSelected(vendor: Vendor | null) {
    const days = vendor?.payment_terms_days ?? null;
    setTermsDays(days);
    applyTerms(days, billDate);
    if (vendor?.default_payable_account && !getValues("payable_account_id")) {
      setValue("payable_account_id", vendor.default_payable_account, { shouldDirty: true });
    }
  }

  // Shown before saving: stocked lines with no receipt link will be received
  // on posting and need somewhere to go (warehouse_required).
  const receivesStock = (lines ?? []).some((line) => {
    const item = line.item_id ? items.get(line.item_id) : undefined;
    return item ? isInventoried(item) && !line.source_goods_receipt_line_id : false;
  });

  const mutation = useIdempotentMutation<Bill, BillInput | BillUpdateInput>(
    "purchases/bills",
    (input, idempotencyKey) =>
      bill ? api.patch<Bill>(`purchases/bills/${bill.id}`, input) : api.post<Bill>("purchases/bills", input, { idempotencyKey }),
    {
      onSuccess: (saved) => {
        toast.push({ tone: "success", title: isEdit ? "Draft bill saved" : "Draft bill created" });
        router.push(`/purchases/bills/${saved.id}`);
        router.refresh();
      },
      onError: (error) => {
        setFormError({ message: formErrorOf(error), reference: referenceOf(error) });
        const pairs = serverFieldErrors(error, FIELDS, {
          duplicate_vendor_bill_number: "vendor_bill_number",
          warehouse_required: "warehouse_id",
          vendor_inactive: "vendor_id",
        });
        for (const [field, message] of pairs) {
          setError(field as (typeof FIELDS)[number], { type: "server", message });
        }
      },
    },
  );

  function onSubmit(values: BillFormValues) {
    setFormError({ message: null, reference: null });

    // item_missing_purchase_account, caught before the round trip when the
    // item list is loaded. The server re-checks regardless.
    let blocked = false;
    values.lines.forEach((line, index) => {
      if (needsExpenseAccount(items.get(line.item_id), line.expense_account_id)) {
        setError(`lines.${index}.expense_account_id`, {
          type: "client",
          message: "This item has no purchase account. Choose an expense account.",
        });
        blocked = true;
      }
    });
    if (blocked) return;

    const lineInputs = values.lines.map(toLineInput);
    if (bill) {
      mutation.mutate({
        bill_date: values.bill_date,
        due_date: values.due_date,
        reference: values.reference,
        vendor_bill_number: values.vendor_bill_number.trim(),
        notes: values.notes,
        lines: lineInputs,
      });
      return;
    }
    mutation.mutate({
      vendor_id: values.vendor_id,
      bill_date: values.bill_date,
      due_date: values.due_date,
      vendor_bill_number: values.vendor_bill_number.trim(),
      reference: values.reference,
      payable_account_id: values.payable_account_id,
      tax_recoverable_account_id: values.tax_recoverable_account_id,
      price_variance_account_id: values.price_variance_account_id,
      warehouse_id: values.warehouse_id,
      source_purchase_order_id: initial.source_purchase_order_id ?? null,
      notes: values.notes,
      lines: lineInputs,
    });
  }

  const lockedHint = isEdit ? "Fixed once the draft is saved." : undefined;

  return (
    <FormProvider {...form}>
      <form method="post" noValidate onSubmit={(event) => void handleSubmit(onSubmit)(event)} className="flex flex-col gap-4">
        <FormError message={formError.message} reference={formError.reference} />

        <Card>
          <CardHeader
            title="Bill"
            {...(initial.order_number ? { description: `Billing purchase order ${initial.order_number}.` } : {})}
          />
          <CardBody className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <FormField
              label="Vendor"
              required
              error={errors.vendor_id?.message ?? null}
              className="sm:col-span-2 lg:col-span-1"
              {...(lockedHint ? { hint: lockedHint } : fromOrder ? { hint: "The purchase order's vendor." } : {})}
            >
              <Controller
                control={control}
                name="vendor_id"
                render={({ field }) => (
                  <VendorPicker
                    value={field.value || null}
                    onChange={(value) => field.onChange(value ?? "")}
                    onSelectVendor={onVendorSelected}
                    disabled={isEdit || fromOrder}
                  />
                )}
              />
            </FormField>

            <FormField label="Bill date" required error={errors.bill_date?.message ?? null}>
              <Input
                type="date"
                {...register("bill_date", {
                  onChange: (event: React.ChangeEvent<HTMLInputElement>) => applyTerms(termsDays, event.target.value),
                })}
              />
            </FormField>

            <FormField
              label="Due date"
              required
              error={errors.due_date?.message ?? null}
              {...(termsDays !== null ? { hint: termsDays === 0 ? "Vendor terms: due on receipt." : `Vendor terms: net ${termsDays} days.` } : {})}
            >
              <Input type="date" {...register("due_date")} />
            </FormField>

            <FormField
              label="Vendor bill number"
              error={errors.vendor_bill_number?.message ?? null}
              hint="The number on the vendor's invoice. Unique per vendor."
            >
              <Input {...register("vendor_bill_number")} />
            </FormField>

            <FormField label="Reference" error={errors.reference?.message ?? null} hint="Your own reference.">
              <Input {...register("reference")} />
            </FormField>

            <FormField
              label="Payable account"
              required
              error={errors.payable_account_id?.message ?? null}
              hint={lockedHint ?? (rememberedFrom ? `Taken from bill ${rememberedFrom}.` : "The liability account this bill is owed from.")}
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
              hint={lockedHint ?? "An asset account for input tax. Needed to post when any line carries tax."}
            >
              <Controller
                control={control}
                name="tax_recoverable_account_id"
                render={({ field }) => (
                  <AccountPicker accountType="asset" value={field.value} onChange={field.onChange} disabled={isEdit} />
                )}
              />
            </FormField>

            <FormField
              label="Price variance account"
              error={errors.price_variance_account_id?.message ?? null}
              hint={lockedHint ?? "An expense account. Needed only when a line billed from a goods receipt is priced differently from the receipt's cost."}
            >
              <Controller
                control={control}
                name="price_variance_account_id"
                render={({ field }) => (
                  <AccountPicker accountType="expense" value={field.value} onChange={field.onChange} disabled={isEdit} />
                )}
              />
            </FormField>

            <FormField
              label="Receive into warehouse"
              required={receivesStock}
              error={errors.warehouse_id?.message ?? null}
              hint={
                lockedHint ??
                (receivesStock
                  ? warehouse
                    ? "Stocked lines not linked to a goods receipt are received here on posting."
                    : "Required: stocked lines not linked to a goods receipt are received on posting."
                  : "Only needed when a stocked item is billed without a goods receipt.")
              }
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
          usage="purchase"
          emptyLine={EMPTY_LINE}
          description="Enter the tax rate on each line as the vendor charged it. Stocked items are capitalised to inventory; everything else is expensed."
          renderExtra={(index) => (
            <div className="flex flex-col gap-2">
              <LineExpenseAccountField index={index} items={items} />
              <LineSourceNote index={index} />
            </div>
          )}
        />

        <div className="grid gap-4 lg:grid-cols-5">
          <Card className="lg:col-span-3">
            <CardHeader title="Notes" />
            <CardBody>
              <FormField label="Notes" error={errors.notes?.message ?? null}>
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
            {isEdit ? "Save draft" : "Save as draft"}
          </Button>
        </div>
      </form>
    </FormProvider>
  );
}
