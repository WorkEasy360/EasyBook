"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Controller, FormProvider, useForm, useFormContext, useWatch, type FieldErrors } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Checkbox, FormError, FormField, Input, Select, Textarea } from "@/components/ui/field";
import { NumericInput } from "@/components/ui/numeric-input";
import { VENDOR_CREDIT_REASON_LABELS } from "@/components/ui/status-badge";
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
import { isValidDecimal, money } from "@/lib/money";
import { todayInZone } from "@/lib/datetime";
import { serverFieldErrors } from "./form-errors";
import { purchaseLineSchema } from "./line-schema";
import { LineExpenseAccountField, LineSourceNote, isInventoried, needsExpenseAccount, useItemIndex } from "./line-extras";
import type { Item } from "@/types/api/items";
import type {
  VendorCredit,
  VendorCreditInput,
  VendorCreditLineInput,
  VendorCreditReason,
  VendorCreditUpdateInput,
} from "@/types/api/purchases";

/**
 * Create or edit a DRAFT vendor credit — a correction in our favour from a
 * vendor (purchases/services/vendor_credits.py).
 *
 * Saving posts nothing. Issuing, from the credit page, posts one journal:
 * it debits payables (netted against the source bill's remaining balance)
 * and any excess to the unapplied-credit asset account, and credits the
 * inventory or expense account each line originally hit plus the input tax.
 * A line marked "return to vendor" also takes that quantity OUT of stock at
 * the unit cost given — that flag is the only thing that moves stock.
 *
 * Backend rules mirrored for immediacy:
 *  - a returned line needs a unit cost (return_cost_required), a warehouse
 *    (warehouse_required) and a stock-tracked product (service_cannot_return_stock,
 *    item_not_tracked);
 *  - a line linked to a bill line cannot credit more than was billed
 *    (credit_quantity_exceeds_bill).
 *
 * VendorCreditDetailView.update accepts the date, reference, vendor credit
 * number, reason, notes and lines; vendor, bill, accounts and warehouse are
 * fixed once the draft exists.
 */

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const REASONS = Object.keys(VENDOR_CREDIT_REASON_LABELS) as [VendorCreditReason, ...VendorCreditReason[]];

const lineSchema = purchaseLineSchema
  .extend({
    expense_account_id: z.string().nullable(),
    source_bill_line_id: z.string().nullable(),
    return_stock: z.boolean(),
    unit_cost: z.string(),
  })
  .refine((line) => !line.return_stock || (isValidDecimal(line.unit_cost) && money(line.unit_cost).gte(0)), {
    message: "Enter the unit cost the returned goods leave stock at.",
    path: ["unit_cost"],
  });

const schema = z
  .object({
    vendor_id: z.string().min(1, "Choose a vendor."),
    credit_date: z.string().regex(DATE, "Enter the credit date."),
    reason: z.enum(REASONS),
    vendor_credit_number: z.string().max(64, "At most 64 characters."),
    reference: z.string().max(255, "At most 255 characters."),
    payable_account_id: z.string().nullable(),
    tax_recoverable_account_id: z.string().nullable(),
    unapplied_credit_account_id: z.string().nullable(),
    warehouse_id: z.string().nullable(),
    notes: z.string().max(4000),
    lines: z.array(lineSchema).min(1, "Add at least one line."),
  })
  .refine((values) => !values.lines.some((line) => line.return_stock) || Boolean(values.warehouse_id), {
    message: "Choose the warehouse the returned goods leave from.",
    path: ["warehouse_id"],
  });

export type VendorCreditFormValues = z.infer<typeof schema>;
export type VendorCreditLineValues = VendorCreditFormValues["lines"][number];
export type VendorCreditFormInitial = Partial<Omit<VendorCreditFormValues, "lines">> & {
  lines?: VendorCreditLineValues[];
  source_bill_id?: string;
  /** Header description only. */
  bill_number?: string;
};

const EMPTY_LINE: VendorCreditLineValues = {
  ...EMPTY_PRICED_LINE,
  expense_account_id: null,
  source_bill_line_id: null,
  return_stock: false,
  unit_cost: "",
};

const FIELDS = [
  "vendor_id",
  "credit_date",
  "reason",
  "vendor_credit_number",
  "reference",
  "payable_account_id",
  "tax_recoverable_account_id",
  "unapplied_credit_account_id",
  "warehouse_id",
  "notes",
] as const;

function toValues(credit: VendorCredit | undefined, initial: VendorCreditFormInitial, today: string): VendorCreditFormValues {
  if (credit) {
    return {
      vendor_id: credit.vendor,
      credit_date: credit.credit_date,
      reason: credit.reason,
      vendor_credit_number: credit.vendor_credit_number,
      reference: credit.reference,
      payable_account_id: credit.payable_account,
      tax_recoverable_account_id: credit.tax_recoverable_account,
      unapplied_credit_account_id: credit.unapplied_credit_account,
      warehouse_id: credit.warehouse,
      notes: credit.notes,
      lines: credit.lines.map((line) => ({
        ...fromPricedLine(line),
        expense_account_id: line.expense_account,
        source_bill_line_id: line.source_bill_line,
        return_stock: line.return_stock,
        unit_cost: line.unit_cost ?? "",
      })),
    };
  }
  return {
    vendor_id: initial.vendor_id ?? "",
    credit_date: initial.credit_date ?? today,
    reason: initial.reason ?? "other",
    vendor_credit_number: initial.vendor_credit_number ?? "",
    reference: initial.reference ?? "",
    payable_account_id: initial.payable_account_id ?? null,
    tax_recoverable_account_id: initial.tax_recoverable_account_id ?? null,
    unapplied_credit_account_id: initial.unapplied_credit_account_id ?? null,
    warehouse_id: initial.warehouse_id ?? null,
    notes: initial.notes ?? "",
    lines: initial.lines?.length ? initial.lines : [{ ...EMPTY_LINE }],
  };
}

function toLineInput(line: VendorCreditLineValues): VendorCreditLineInput {
  return {
    ...toPricedLineInput(line),
    expense_account_id: line.expense_account_id,
    source_bill_line_id: line.source_bill_line_id,
    return_stock: line.return_stock,
    unit_cost: line.return_stock && line.unit_cost.trim() !== "" ? line.unit_cost : null,
  };
}

export function VendorCreditForm({ credit, initial = {} }: { credit?: VendorCredit; initial?: VendorCreditFormInitial }) {
  const router = useRouter();
  const toast = useToast();
  const org = useOrg();
  const isEdit = Boolean(credit);
  const fromBill = Boolean(credit?.source_bill ?? initial.source_bill_id);
  const items = useItemIndex();
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });

  const form = useForm<VendorCreditFormValues>({
    resolver: zodResolver(schema),
    defaultValues: toValues(credit, initial, todayInZone(org.timeZone)),
  });
  const {
    control,
    register,
    handleSubmit,
    setError,
    formState: { errors, isDirty, isSubmitting },
  } = form;

  useUnsavedChanges(isDirty && !isSubmitting);
  const warehouse = useWatch({ control, name: "warehouse_id" });
  const lines = useWatch({ control, name: "lines" });
  const returnsStock = (lines ?? []).some((line) => line.return_stock);

  const mutation = useIdempotentMutation<VendorCredit, VendorCreditInput | VendorCreditUpdateInput>(
    "purchases/vendor-credits",
    (input, idempotencyKey) =>
      credit
        ? api.patch<VendorCredit>(`purchases/vendor-credits/${credit.id}`, input)
        : api.post<VendorCredit>("purchases/vendor-credits", input, { idempotencyKey }),
    {
      onSuccess: (saved) => {
        toast.push({ tone: "success", title: isEdit ? "Draft vendor credit saved" : "Draft vendor credit created" });
        router.push(`/purchases/vendor-credits/${saved.id}`);
        router.refresh();
      },
      onError: (error) => {
        setFormError({ message: formErrorOf(error), reference: referenceOf(error) });
        const pairs = serverFieldErrors(error, FIELDS, {
          warehouse_required: "warehouse_id",
          vendor_inactive: "vendor_id",
        });
        for (const [field, message] of pairs) {
          setError(field as (typeof FIELDS)[number], { type: "server", message });
        }
      },
    },
  );

  function onSubmit(values: VendorCreditFormValues) {
    setFormError({ message: null, reference: null });

    let blocked = false;
    values.lines.forEach((line, index) => {
      const item = items.get(line.item_id);
      if (needsExpenseAccount(item, line.expense_account_id)) {
        setError(`lines.${index}.expense_account_id`, {
          type: "client",
          message: "This item has no purchase account. Choose the expense account to credit.",
        });
        blocked = true;
      }
      if (line.return_stock && item && !isInventoried(item)) {
        setError(`lines.${index}.return_stock`, { type: "client", message: "Only stock-tracked products can be returned." });
        blocked = true;
      }
    });
    if (blocked) return;

    const lineInputs = values.lines.map(toLineInput);
    if (credit) {
      mutation.mutate({
        credit_date: values.credit_date,
        reference: values.reference,
        vendor_credit_number: values.vendor_credit_number.trim(),
        reason: values.reason,
        notes: values.notes,
        lines: lineInputs,
      });
      return;
    }
    mutation.mutate({
      vendor_id: values.vendor_id,
      credit_date: values.credit_date,
      source_bill_id: initial.source_bill_id ?? null,
      reason: values.reason,
      vendor_credit_number: values.vendor_credit_number.trim(),
      reference: values.reference,
      payable_account_id: values.payable_account_id,
      tax_recoverable_account_id: values.tax_recoverable_account_id,
      unapplied_credit_account_id: values.unapplied_credit_account_id,
      warehouse_id: values.warehouse_id,
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
            title="Vendor credit"
            {...(initial.bill_number ? { description: `Crediting bill ${initial.bill_number}.` } : {})}
          />
          <CardBody className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <FormField
              label="Vendor"
              required
              error={errors.vendor_id?.message ?? null}
              className="sm:col-span-2 lg:col-span-1"
              {...(lockedHint ? { hint: lockedHint } : fromBill ? { hint: "The bill's vendor." } : {})}
            >
              <Controller
                control={control}
                name="vendor_id"
                render={({ field }) => (
                  <VendorPicker
                    value={field.value || null}
                    onChange={(value) => field.onChange(value ?? "")}
                    disabled={isEdit || fromBill}
                  />
                )}
              />
            </FormField>

            <FormField label="Credit date" required error={errors.credit_date?.message ?? null} hint="The posting date when issued.">
              <Input type="date" {...register("credit_date")} />
            </FormField>

            <FormField label="Reason" required error={errors.reason?.message ?? null}>
              <Select {...register("reason")}>
                {REASONS.map((reason) => (
                  <option key={reason} value={reason}>
                    {VENDOR_CREDIT_REASON_LABELS[reason]}
                  </option>
                ))}
              </Select>
            </FormField>

            <FormField
              label="Vendor credit note number"
              error={errors.vendor_credit_number?.message ?? null}
              hint="The number on the vendor's credit note."
            >
              <Input {...register("vendor_credit_number")} />
            </FormField>

            <FormField label="Reference" error={errors.reference?.message ?? null}>
              <Input {...register("reference")} />
            </FormField>

            <FormField
              label="Return from warehouse"
              required={returnsStock}
              error={errors.warehouse_id?.message ?? null}
              hint={
                lockedHint
                  ? warehouse
                    ? lockedHint
                    : "No warehouse was set when this draft was saved, so its lines cannot return stock."
                  : "Needed only when a line returns goods to the vendor."
              }
            >
              <Controller
                control={control}
                name="warehouse_id"
                render={({ field }) => <WarehousePicker value={field.value} onChange={field.onChange} disabled={isEdit} />}
              />
            </FormField>

            <FormField
              label="Payable account"
              error={errors.payable_account_id?.message ?? null}
              hint={lockedHint ?? (fromBill ? "Leave blank to use the bill's payable account." : "A liability account.")}
            >
              <Controller
                control={control}
                name="payable_account_id"
                render={({ field }) => (
                  <AccountPicker accountType="liability" value={field.value} onChange={field.onChange} disabled={isEdit} />
                )}
              />
            </FormField>

            <FormField
              label="Tax recoverable account"
              error={errors.tax_recoverable_account_id?.message ?? null}
              hint={lockedHint ?? (fromBill ? "Leave blank to use the bill's." : "Needed to issue a credit with tax. An asset account.")}
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
              label="Unapplied credit account"
              error={errors.unapplied_credit_account_id?.message ?? null}
              hint={
                lockedHint ??
                (fromBill
                  ? "Needed only if the credit is larger than the bill's remaining balance. An asset account."
                  : "Required to issue: with no bill to net against, the whole credit is held here. An asset account.")
              }
            >
              <Controller
                control={control}
                name="unapplied_credit_account_id"
                render={({ field }) => (
                  <AccountPicker accountType="asset" value={field.value} onChange={field.onChange} disabled={isEdit} />
                )}
              />
            </FormField>
          </CardBody>
        </Card>

        <PricedLinesEditor
          usage="purchase"
          emptyLine={EMPTY_LINE}
          description="What the vendor is crediting. Each line reverses the account the purchase was charged to."
          renderExtra={(index) => (
            <div className="flex flex-col gap-3">
              <div className="grid gap-3 sm:grid-cols-2">
                <LineExpenseAccountField index={index} items={items} />
                <ReturnStockFields index={index} items={items} warehouseLocked={isEdit && !warehouse} />
              </div>
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

/** "Return to vendor" and its unit cost — only offered for stock-tracked products. */
function ReturnStockFields({ index, items, warehouseLocked }: { index: number; items: Map<string, Item>; warehouseLocked: boolean }) {
  const { control, register, formState } = useFormContext<VendorCreditFormValues>();
  const itemId = useWatch({ control, name: `lines.${index}.item_id` });
  const returning = useWatch({ control, name: `lines.${index}.return_stock` });
  const item = itemId ? items.get(itemId) : undefined;
  const errors = formState.errors as FieldErrors<VendorCreditFormValues>;
  const lineErrors = errors.lines?.[index];

  if (!item || !isInventoried(item)) {
    return (
      <p className="self-end text-xs text-ink-500">
        {item ? "Services and untracked items cannot be returned to stock." : "Choose an item to see stock options."}
      </p>
    );
  }

  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <div className="flex flex-col justify-end gap-1 pb-1">
        <Checkbox
          label="Return goods to vendor"
          hint={warehouseLocked ? "This draft has no warehouse to return from." : "Takes the quantity out of stock on issue."}
          disabled={warehouseLocked}
          {...register(`lines.${index}.return_stock`)}
        />
        {lineErrors?.return_stock?.message ? <p className="text-xs text-danger-600">{lineErrors.return_stock.message}</p> : null}
      </div>
      {returning ? (
        <FormField
          label="Unit cost"
          required
          error={lineErrors?.unit_cost?.message ?? null}
          hint="The cost the goods leave stock at, normally what they were received at."
        >
          <Controller
            control={control}
            name={`lines.${index}.unit_cost`}
            render={({ field }) => (
              <NumericInput scale={4} nonNegative value={field.value} onValueChange={field.onChange} onBlur={field.onBlur} />
            )}
          />
        </FormField>
      ) : null}
    </div>
  );
}
