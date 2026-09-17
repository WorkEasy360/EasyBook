"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Controller, useFieldArray, useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button, IconButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { FormError, FormField, Input, Textarea } from "@/components/ui/field";
import { NumericInput, QuantityInput } from "@/components/ui/numeric-input";
import { Icons } from "@/components/ui/icons";
import { useToast } from "@/components/ui/toast";
import { useOrg } from "@/components/providers/org-provider";
import { ItemPicker, VendorPicker, WarehousePicker } from "@/features/shared/pickers";
import { useUnsavedChanges } from "@/lib/hooks/use-unsaved-changes";
import { useIdempotentMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { formErrorOf, referenceOf } from "@/lib/api/errors";
import { isValidDecimal, money } from "@/lib/money";
import { todayInZone } from "@/lib/datetime";
import { serverFieldErrors } from "./form-errors";
import type {
  GoodsReceipt,
  GoodsReceiptInput,
  GoodsReceiptLineInput,
  GoodsReceiptUpdateInput,
} from "@/types/api/purchases";

/**
 * Create or edit a DRAFT goods receipt — goods that have physically arrived.
 *
 * Saving moves no stock. Receiving is a separate, confirmed action on the
 * receipt page (services/goods_receipts.py :: receive_goods), which writes
 * the stock movements. No journal is posted by a receipt: the
 * Inventory/Payables entry belongs to the bill that later bills it.
 *
 * Backend rules mirrored for immediacy (the service re-checks all of them):
 *  - only inventory-tracked products can be received (item_not_receivable);
 *  - a line linked to a purchase order line may leave the unit cost blank and
 *    takes the ordered price; an unlinked line must state its cost
 *    (receipt_unit_cost_required) — a silent zero would corrupt valuation;
 *  - a linked line cannot receive more than was ordered (over_receipt).
 *
 * On edit, GoodsReceiptDetailView.update accepts the date, delivery note
 * number, notes and lines; vendor, warehouse and order are fixed.
 */

const DATE = /^\d{4}-\d{2}-\d{2}$/;

const lineSchema = z
  .object({
    item_id: z.string().min(1, "Choose an item."),
    // GoodsReceiptLine.description is max_length=255.
    description: z.string().max(255, "At most 255 characters."),
    quantity: z.string().refine((value) => isValidDecimal(value) && money(value).gt(0), "Enter a quantity above zero."),
    unit_cost: z.string(),
    source_order_line_id: z.string().nullable(),
  })
  .refine((line) => line.unit_cost.trim() === "" || (isValidDecimal(line.unit_cost) && money(line.unit_cost).gte(0)), {
    message: "Enter a cost of zero or more.",
    path: ["unit_cost"],
  })
  .refine((line) => line.source_order_line_id !== null || line.unit_cost.trim() !== "", {
    message: "Enter the unit cost. Only lines from a purchase order can take the ordered price.",
    path: ["unit_cost"],
  });

const schema = z.object({
  vendor_id: z.string().min(1, "Choose a vendor."),
  warehouse_id: z.string().min(1, "Choose the warehouse the goods arrived at."),
  receipt_date: z.string().regex(DATE, "Enter the receipt date."),
  vendor_document_number: z.string().max(64, "At most 64 characters."),
  notes: z.string().max(4000),
  lines: z.array(lineSchema).min(1, "Add at least one line."),
});

type Values = z.infer<typeof schema>;
export type GoodsReceiptLineValues = Values["lines"][number];

export interface GoodsReceiptFormInitial {
  vendor_id?: string;
  warehouse_id?: string;
  source_purchase_order_id?: string;
  /** Shown in the header card; the order itself is fixed by the page. */
  order_number?: string;
  lines?: GoodsReceiptLineValues[];
}

const EMPTY_LINE: GoodsReceiptLineValues = {
  item_id: "",
  description: "",
  quantity: "",
  unit_cost: "",
  source_order_line_id: null,
};

const FIELDS = ["vendor_id", "warehouse_id", "receipt_date", "vendor_document_number", "notes"] as const;

function toLineInput(line: GoodsReceiptLineValues): GoodsReceiptLineInput {
  return {
    item_id: line.item_id,
    description: line.description.trim(),
    quantity: line.quantity,
    // Blank means "take the ordered price" — only valid on a linked line,
    // which the schema has already enforced.
    unit_cost: line.unit_cost.trim() === "" ? null : line.unit_cost,
    source_order_line_id: line.source_order_line_id,
  };
}

export function GoodsReceiptForm({ receipt, initial = {} }: { receipt?: GoodsReceipt; initial?: GoodsReceiptFormInitial }) {
  const router = useRouter();
  const toast = useToast();
  const org = useOrg();
  const isEdit = Boolean(receipt);
  const fromOrder = Boolean(receipt?.source_purchase_order ?? initial.source_purchase_order_id);
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
    defaultValues: receipt
      ? {
          vendor_id: receipt.vendor,
          warehouse_id: receipt.warehouse,
          receipt_date: receipt.receipt_date,
          vendor_document_number: receipt.vendor_document_number,
          notes: receipt.notes,
          lines: receipt.lines.map((line) => ({
            item_id: line.item,
            description: line.description,
            quantity: line.quantity,
            unit_cost: line.unit_cost,
            source_order_line_id: line.source_order_line,
          })),
        }
      : {
          vendor_id: initial.vendor_id ?? "",
          warehouse_id: initial.warehouse_id ?? "",
          receipt_date: todayInZone(org.timeZone),
          vendor_document_number: "",
          notes: "",
          lines: initial.lines?.length ? initial.lines : [{ ...EMPTY_LINE }],
        },
  });

  const { fields, append, remove } = useFieldArray({ control, name: "lines" });
  const lineValues = useWatch({ control, name: "lines" });
  useUnsavedChanges(isDirty && !isSubmitting);

  const mutation = useIdempotentMutation<GoodsReceipt, GoodsReceiptInput | GoodsReceiptUpdateInput>(
    "purchases/goods-receipts",
    (input, idempotencyKey) =>
      receipt
        ? api.patch<GoodsReceipt>(`purchases/goods-receipts/${receipt.id}`, input)
        : api.post<GoodsReceipt>("purchases/goods-receipts", input, { idempotencyKey }),
    {
      onSuccess: (saved) => {
        toast.push({ tone: "success", title: isEdit ? "Draft receipt saved" : "Draft receipt created" });
        router.push(`/purchases/goods-receipts/${saved.id}`);
        router.refresh();
      },
      onError: (error) => {
        setFormError({ message: formErrorOf(error), reference: referenceOf(error) });
        const pairs = serverFieldErrors(error, FIELDS, { vendor_inactive: "vendor_id" });
        for (const [field, message] of pairs) {
          setError(field as (typeof FIELDS)[number], { type: "server", message });
        }
      },
    },
  );

  function onSubmit(values: Values) {
    setFormError({ message: null, reference: null });
    const lines = values.lines.map(toLineInput);
    if (receipt) {
      mutation.mutate({
        receipt_date: values.receipt_date,
        vendor_document_number: values.vendor_document_number,
        notes: values.notes,
        lines,
      });
      return;
    }
    mutation.mutate({
      vendor_id: values.vendor_id,
      warehouse_id: values.warehouse_id,
      receipt_date: values.receipt_date,
      source_purchase_order_id: initial.source_purchase_order_id ?? null,
      vendor_document_number: values.vendor_document_number,
      notes: values.notes,
      lines,
    });
  }

  const lockedHint = isEdit ? "Fixed once the draft is saved." : undefined;
  const listError = errors.lines?.root?.message ?? errors.lines?.message;

  return (
    <form method="post" noValidate onSubmit={(event) => void handleSubmit(onSubmit)(event)} className="flex flex-col gap-4">
      <FormError message={formError.message} reference={formError.reference} />

      <Card>
        <CardHeader
          title="Goods receipt"
          {...(initial.order_number ? { description: `Receiving against purchase order ${initial.order_number}.` } : {})}
        />
        <CardBody className="grid gap-4 sm:grid-cols-2">
          <FormField
            label="Vendor"
            required
            error={errors.vendor_id?.message ?? null}
            {...(lockedHint ? { hint: lockedHint } : fromOrder ? { hint: "The purchase order's vendor." } : {})}
          >
            <Controller
              control={control}
              name="vendor_id"
              render={({ field }) => (
                <VendorPicker
                  value={field.value || null}
                  onChange={(value) => field.onChange(value ?? "")}
                  disabled={isEdit || fromOrder}
                />
              )}
            />
          </FormField>

          <FormField
            label="Warehouse"
            required
            error={errors.warehouse_id?.message ?? null}
            hint={lockedHint ?? "Stock is received into this warehouse when the receipt is marked received."}
          >
            <Controller
              control={control}
              name="warehouse_id"
              render={({ field }) => (
                <WarehousePicker value={field.value || null} onChange={(value) => field.onChange(value ?? "")} disabled={isEdit} />
              )}
            />
          </FormField>

          <FormField label="Receipt date" required error={errors.receipt_date?.message ?? null} hint="The stock movement date.">
            <Input type="date" {...register("receipt_date")} />
          </FormField>

          <FormField
            label="Vendor delivery note"
            error={errors.vendor_document_number?.message ?? null}
            hint="The vendor's delivery note or challan number."
          >
            <Input {...register("vendor_document_number")} />
          </FormField>

          <FormField label="Notes" className="sm:col-span-2" error={errors.notes?.message ?? null}>
            <Textarea rows={2} {...register("notes")} />
          </FormField>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Lines"
          description={
            fromOrder
              ? "Quantities start at what is still outstanding on the order. Leave the unit cost blank to use the ordered price."
              : "Only products that track inventory can be received. Each line needs its unit cost."
          }
          actions={
            fromOrder ? null : (
              <Button
                variant="secondary"
                size="sm"
                leadingIcon={<Icons.plus className="size-3.5" />}
                onClick={() => append({ ...EMPTY_LINE })}
              >
                Add line
              </Button>
            )
          }
        />
        <CardBody className="flex flex-col gap-3">
          {listError ? (
            <p role="alert" className="text-xs text-danger-600">
              {listError}
            </p>
          ) : null}

          {fields.map((field, index) => {
            const lineErrors = errors.lines?.[index];
            const linked = Boolean(lineValues[index]?.source_order_line_id);
            return (
              <fieldset key={field.id} className="grid gap-3 rounded-md border border-ink-200 p-3 sm:grid-cols-12">
                <legend className="sr-only">Line {index + 1}</legend>

                <FormField
                  label="Item"
                  required
                  error={lineErrors?.item_id?.message ?? null}
                  className="sm:col-span-5"
                  {...(linked ? { hint: "From the purchase order line." } : {})}
                >
                  <Controller
                    control={control}
                    name={`lines.${index}.item_id`}
                    render={({ field: item }) => (
                      <ItemPicker
                        usage="purchase"
                        trackedOnly
                        value={item.value || null}
                        onChange={(value) => item.onChange(value ?? "")}
                        // A linked line's item must match its order line (purchase_order_line_item_mismatch).
                        disabled={linked}
                      />
                    )}
                  />
                </FormField>

                <FormField label="Quantity" required error={lineErrors?.quantity?.message ?? null} className="sm:col-span-3">
                  <Controller
                    control={control}
                    name={`lines.${index}.quantity`}
                    render={({ field: quantity }) => (
                      <QuantityInput value={quantity.value} onValueChange={quantity.onChange} onBlur={quantity.onBlur} />
                    )}
                  />
                </FormField>

                <FormField
                  label="Unit cost"
                  required={!linked}
                  error={lineErrors?.unit_cost?.message ?? null}
                  className="sm:col-span-3"
                  {...(linked ? { hint: "Blank uses the ordered price." } : {})}
                >
                  <Controller
                    control={control}
                    name={`lines.${index}.unit_cost`}
                    render={({ field: cost }) => (
                      <NumericInput scale={4} nonNegative value={cost.value} onValueChange={cost.onChange} onBlur={cost.onBlur} />
                    )}
                  />
                </FormField>

                <div className="flex items-end justify-end sm:col-span-1">
                  <IconButton
                    label={`Remove line ${index + 1}`}
                    icon={<Icons.close className="size-4" />}
                    disabled={fields.length === 1}
                    onClick={() => remove(index)}
                  />
                </div>

                <FormField label="Description" error={lineErrors?.description?.message ?? null} className="sm:col-span-12">
                  <Input placeholder="Optional" {...register(`lines.${index}.description`)} />
                </FormField>
              </fieldset>
            );
          })}
        </CardBody>
      </Card>

      <div className="flex items-center justify-end gap-2">
        <Button variant="secondary" onClick={() => router.back()} disabled={mutation.isPending}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={mutation.isPending} loadingLabel="Saving">
          {isEdit ? "Save draft" : "Save as draft"}
        </Button>
      </div>
    </form>
  );
}
