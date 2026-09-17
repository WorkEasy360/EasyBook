"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Controller, useFieldArray, useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button, IconButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { FormError, FormField, Input, Select, Textarea } from "@/components/ui/field";
import { NumericInput, QuantityInput } from "@/components/ui/numeric-input";
import { Icons } from "@/components/ui/icons";
import { useToast } from "@/components/ui/toast";
import { useOrg } from "@/components/providers/org-provider";
import { AccountPicker, ItemPicker, WarehousePicker } from "@/features/shared/pickers";
import { useUnsavedChanges } from "@/lib/hooks/use-unsaved-changes";
import { useIdempotentMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import { isValidDecimal, money } from "@/lib/money";
import { todayInZone } from "@/lib/datetime";
import {
  ADJUSTMENT_REASON_LABELS,
  type AdjustmentReason,
  type StockAdjustment,
  type StockAdjustmentInput,
} from "@/types/api/inventory";

/**
 * Create or edit a DRAFT stock adjustment.
 *
 * Saving never moves stock: the draft is inert until someone posts it from
 * the detail page, where the confirmation says exactly what will happen. That
 * two-step shape is the backend's (inventory/services/adjustments.py), and
 * the form mirrors it rather than offering a one-click "save and post".
 */

const REASONS = Object.keys(ADJUSTMENT_REASON_LABELS) as [AdjustmentReason, ...AdjustmentReason[]];

const lineSchema = z
  .object({
    item_id: z.string().min(1, "Choose an item."),
    direction: z.enum(["adjustment_in", "adjustment_out"]),
    quantity: z
      .string()
      .refine((value) => isValidDecimal(value) && money(value).gt(0), "Enter a quantity above zero."),
    unit_cost: z.string(),
    notes: z.string().max(500),
  })
  // inventory/services/adjustments.py :: adjustment_line_cost_required. Stock
  // coming IN must say what it cost; stock going out is costed by the
  // backend at moving average, so asking for it would be ignored input.
  .refine((line) => line.direction === "adjustment_out" || (line.unit_cost !== "" && isValidDecimal(line.unit_cost)), {
    message: "Inbound stock needs a unit cost.",
    path: ["unit_cost"],
  });

const schema = z.object({
  warehouse_id: z.string().min(1, "Choose a warehouse."),
  adjustment_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "Enter a date."),
  reason: z.enum(REASONS),
  contra_account_id: z.string().nullable(),
  memo: z.string().max(2000),
  lines: z.array(lineSchema).min(1, "Add at least one line."),
});

type Values = z.infer<typeof schema>;

const EMPTY_LINE: Values["lines"][number] = {
  item_id: "",
  direction: "adjustment_in",
  quantity: "",
  unit_cost: "",
  notes: "",
};

export function AdjustmentForm({ adjustment }: { adjustment?: StockAdjustment }) {
  const router = useRouter();
  const toast = useToast();
  const org = useOrg();
  const isEdit = Boolean(adjustment);
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });

  const {
    control,
    register,
    handleSubmit,
    formState: { errors, isDirty, isSubmitting },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: adjustment
      ? {
          warehouse_id: adjustment.warehouse,
          adjustment_date: adjustment.adjustment_date,
          reason: adjustment.reason,
          contra_account_id: adjustment.contra_account,
          memo: adjustment.memo,
          lines: adjustment.lines.map((line) => ({
            item_id: line.item,
            direction: line.direction,
            quantity: line.quantity,
            unit_cost: line.unit_cost ?? "",
            notes: line.notes,
          })),
        }
      : {
          warehouse_id: "",
          adjustment_date: todayInZone(org.timeZone),
          reason: "physical_count",
          contra_account_id: null,
          memo: "",
          lines: [{ ...EMPTY_LINE }],
        },
  });

  const { fields, append, remove } = useFieldArray({ control, name: "lines" });
  useUnsavedChanges(isDirty && !isSubmitting);

  const mutation = useIdempotentMutation<StockAdjustment, StockAdjustmentInput>(
    "inventory/adjustments",
    (input, idempotencyKey) =>
      adjustment
        ? // The detail endpoint takes the same line shape but ignores
          // warehouse/contra changes on update (StockAdjustmentDetailView).
          api.patch<StockAdjustment>(`inventory/adjustments/${adjustment.id}`, {
            adjustment_date: input.adjustment_date,
            reason: input.reason,
            memo: input.memo,
            lines: input.lines,
          })
        : api.post<StockAdjustment>("inventory/adjustments", input, { idempotencyKey }),
    {
      onSuccess: (saved) => {
        toast.push({ tone: "success", title: isEdit ? "Draft adjustment saved" : "Draft adjustment created" });
        router.push(`/inventory/adjustments/${saved.id}`);
        router.refresh();
      },
      onError: (error) => {
        const fieldErrors = fieldErrorsOf(error);
        const lineMessages = fieldErrors["lines"];
        setFormError({
          message: lineMessages?.length ? `Lines: ${lineMessages.join(" ")}` : formErrorOf(error),
          reference: referenceOf(error),
        });
      },
    },
  );

  function onSubmit(values: Values) {
    setFormError({ message: null, reference: null });
    mutation.mutate({
      warehouse_id: values.warehouse_id,
      adjustment_date: values.adjustment_date,
      reason: values.reason,
      memo: values.memo,
      contra_account_id: values.contra_account_id,
      lines: values.lines.map((line) => ({
        item_id: line.item_id,
        direction: line.direction,
        quantity: line.quantity,
        unit_cost: line.direction === "adjustment_in" && line.unit_cost !== "" ? line.unit_cost : null,
        notes: line.notes,
      })),
    });
  }

  const contraAccount = useWatch({ control, name: "contra_account_id" });
  const lineValues = useWatch({ control, name: "lines" });

  return (
    <form
      method="post"
      noValidate
      onSubmit={(event) => void handleSubmit(onSubmit)(event)}
      className="flex flex-col gap-4"
    >
      <FormError message={formError.message} reference={formError.reference} />

      <Card>
        <CardHeader title="Adjustment" />
        <CardBody className="grid gap-4 sm:grid-cols-2">
          <FormField
            label="Warehouse"
            required
            error={errors.warehouse_id?.message ?? null}
            {...(isEdit ? { hint: "The warehouse of a saved draft cannot be changed." } : {})}
          >
            <Controller
              control={control}
              name="warehouse_id"
              render={({ field }) => (
                <WarehousePicker value={field.value || null} onChange={(value) => field.onChange(value ?? "")} disabled={isEdit} />
              )}
            />
          </FormField>

          <FormField label="Date" required error={errors.adjustment_date?.message ?? null}>
            <Input type="date" {...register("adjustment_date")} />
          </FormField>

          <FormField label="Reason" required error={errors.reason?.message ?? null}>
            <Select {...register("reason")}>
              {REASONS.map((reason) => (
                <option key={reason} value={reason}>
                  {ADJUSTMENT_REASON_LABELS[reason]}
                </option>
              ))}
            </Select>
          </FormField>

          <FormField
            label="Contra account"
            hint={
              contraAccount
                ? "Posting books the value change against this account."
                : "Without one, stock changes but no journal is posted — the ledger's inventory balance will not follow."
            }
            {...(isEdit ? { disabled: true } : {})}
          >
            <Controller
              control={control}
              name="contra_account_id"
              render={({ field }) => (
                <AccountPicker value={field.value} onChange={field.onChange} disabled={isEdit} />
              )}
            />
          </FormField>

          <FormField label="Memo" className="sm:col-span-2" error={errors.memo?.message ?? null}>
            <Textarea rows={2} {...register("memo")} />
          </FormField>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Lines"
          description="Only products that track inventory can be adjusted. Outbound lines are costed by the system at moving average."
          actions={
            <Button
              variant="secondary"
              size="sm"
              leadingIcon={<Icons.plus className="size-3.5" />}
              onClick={() => append({ ...EMPTY_LINE })}
            >
              Add line
            </Button>
          }
        />
        <CardBody className="flex flex-col gap-3">
          {errors.lines?.root?.message || errors.lines?.message ? (
            <p role="alert" className="text-xs text-danger-600">
              {errors.lines?.root?.message ?? errors.lines?.message}
            </p>
          ) : null}

          {fields.map((field, index) => {
            const lineErrors = errors.lines?.[index];
            const direction = lineValues[index]?.direction ?? "adjustment_in";
            return (
              <fieldset
                key={field.id}
                className="grid gap-3 rounded-md border border-ink-200 p-3 sm:grid-cols-12"
              >
                <legend className="sr-only">Line {index + 1}</legend>

                <FormField label="Item" required error={lineErrors?.item_id?.message ?? null} className="sm:col-span-4">
                  <Controller
                    control={control}
                    name={`lines.${index}.item_id`}
                    render={({ field: item }) => (
                      <ItemPicker trackedOnly value={item.value || null} onChange={(value) => item.onChange(value ?? "")} />
                    )}
                  />
                </FormField>

                <FormField label="Direction" required className="sm:col-span-2">
                  <Select {...register(`lines.${index}.direction`)}>
                    <option value="adjustment_in">In (+)</option>
                    <option value="adjustment_out">Out (−)</option>
                  </Select>
                </FormField>

                <FormField label="Quantity" required error={lineErrors?.quantity?.message ?? null} className="sm:col-span-2">
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
                  required={direction === "adjustment_in"}
                  error={lineErrors?.unit_cost?.message ?? null}
                  className="sm:col-span-3"
                  {...(direction === "adjustment_out" ? { hint: "Set by the system." } : {})}
                >
                  <Controller
                    control={control}
                    name={`lines.${index}.unit_cost`}
                    render={({ field: cost }) => (
                      <NumericInput
                        scale={4}
                        nonNegative
                        value={direction === "adjustment_out" ? "" : cost.value}
                        onValueChange={cost.onChange}
                        onBlur={cost.onBlur}
                        disabled={direction === "adjustment_out"}
                      />
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

                <FormField label="Line note" className="sm:col-span-12">
                  <Input {...register(`lines.${index}.notes`)} />
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
        <Button type="submit" variant="primary" loading={mutation.isPending}>
          {isEdit ? "Save draft" : "Create draft"}
        </Button>
      </div>
    </form>
  );
}
