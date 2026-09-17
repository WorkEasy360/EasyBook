"use client";

import * as React from "react";
import { Controller, useFieldArray, useFormContext, useWatch, type FieldErrors } from "react-hook-form";
import { Button, IconButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { FormField, Input } from "@/components/ui/field";
import { NumericInput, PercentInput, QuantityInput } from "@/components/ui/numeric-input";
import { TotalsPanel } from "@/components/ui/detail";
import { Money } from "@/components/ui/money";
import { Icons } from "@/components/ui/icons";
import { ItemPicker } from "@/features/shared/pickers";
import { estimateLine, estimateTotals } from "./estimate";
import { EMPTY_PRICED_LINE, type PricedLineValues } from "./priced-lines";
import type { Item } from "@/types/api/items";

/**
 * The line editor every priced document shares — quote, order, invoice,
 * credit note, purchase order, bill, vendor credit.
 *
 * It collects the INPUTS the backend prices from (item, quantity, unit
 * price, discount %, tax %) and shows an estimate beside them. It never sends
 * a computed amount: base, discount, tax and totals are recalculated by
 * backend/core/money.py on save, and the saved document is what the user
 * sees next.
 *
 * The tax rate is typed per line. There is no tax-rate API to choose from
 * (tax/api is empty — see src/lib/navigation.ts), and hard-coding a list of
 * GST slabs here would be inventing compliance data that changes by
 * notification. The backend applies the CGST/SGST vs IGST split itself.
 *
 * The parent form must be wrapped in <FormProvider> and hold its lines under
 * `lines`, each shaped like PricedLineValues (plus any extra fields rendered
 * through `renderExtra`).
 */

// Re-exported so client forms can keep importing everything from here.
export {
  EMPTY_PRICED_LINE,
  fromPricedLine,
  pricedLineSchema,
  toPricedLineInput,
  type PricedLineValues,
} from "./priced-lines";

type LinesForm = { lines: Array<PricedLineValues & Record<string, unknown>> };

export function PricedLinesEditor({
  usage,
  title = "Lines",
  description,
  emptyLine = EMPTY_PRICED_LINE,
  renderExtra,
  disabled = false,
  itemFilter,
}: {
  /** Picks the sellable or purchasable items and which price to prefill. */
  usage: "sales" | "purchase";
  title?: string;
  description?: string;
  emptyLine?: PricedLineValues & Record<string, unknown>;
  /** Extra per-line fields (restock, expense account…), rendered under the line. */
  renderExtra?: (index: number) => React.ReactNode;
  disabled?: boolean;
  itemFilter?: { trackedOnly?: boolean };
}) {
  // The generic form is narrowed to its `lines` array here. RHF's Control is
  // invariant in the form type, so each caller's richer form cannot be passed
  // as LinesForm without this cast; the paths used below all exist on it.
  const { control, register, setValue, getValues, formState } = useFormContext<LinesForm>();
  const { fields, append, remove } = useFieldArray({ control, name: "lines" });
  const lines = useWatch({ control, name: "lines" });
  const errors = formState.errors as FieldErrors<LinesForm>;

  function onItemChosen(index: number, item: Item | null) {
    if (!item) return;
    // Prefill only what the user has not already typed. Overwriting a price
    // they entered because they re-picked the item would be a silent edit.
    const current = getValues(`lines.${index}`);
    if (!current.description?.trim()) {
      setValue(`lines.${index}.description`, item.name, { shouldDirty: true });
    }
    const price = usage === "sales" ? item.sales_price : item.purchase_price;
    if (!current.unit_price?.trim() && price) {
      setValue(`lines.${index}.unit_price`, price, { shouldDirty: true });
    }
  }

  const listError = errors.lines?.root?.message ?? errors.lines?.message;

  return (
    <Card>
      <CardHeader
        title={title}
        {...(description ? { description } : {})}
        actions={
          disabled ? null : (
            <Button
              variant="secondary"
              size="sm"
              leadingIcon={<Icons.plus className="size-3.5" />}
              onClick={() => append({ ...emptyLine })}
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
          const estimate = lines?.[index] ? estimateLine(lines[index]) : null;

          return (
            <fieldset key={field.id} className="grid gap-3 rounded-md border border-ink-200 p-3 sm:grid-cols-12" disabled={disabled}>
              <legend className="sr-only">Line {index + 1}</legend>

              <FormField label="Item" required error={lineErrors?.item_id?.message ?? null} className="sm:col-span-5">
                <Controller
                  control={control}
                  name={`lines.${index}.item_id`}
                  render={({ field: item }) => (
                    <ItemPicker
                      usage={usage}
                      trackedOnly={itemFilter?.trackedOnly ?? false}
                      value={item.value || null}
                      onChange={(value) => item.onChange(value ?? "")}
                      onSelectItem={(selected) => onItemChosen(index, selected)}
                      disabled={disabled}
                    />
                  )}
                />
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

              <FormField label="Unit price" required error={lineErrors?.unit_price?.message ?? null} className="sm:col-span-3">
                <Controller
                  control={control}
                  name={`lines.${index}.unit_price`}
                  render={({ field: price }) => (
                    <NumericInput nonNegative value={price.value} onValueChange={price.onChange} onBlur={price.onBlur} />
                  )}
                />
              </FormField>

              <div className="flex items-end justify-end sm:col-span-2">
                <IconButton
                  label={`Remove line ${index + 1}`}
                  icon={<Icons.close className="size-4" />}
                  disabled={disabled || fields.length === 1}
                  onClick={() => remove(index)}
                />
              </div>

              <FormField label="Description" error={lineErrors?.description?.message ?? null} className="sm:col-span-5">
                <Input placeholder="Defaults to the item name" {...register(`lines.${index}.description`)} />
              </FormField>

              <FormField label="Discount" error={lineErrors?.discount_percent?.message ?? null} className="sm:col-span-2">
                <Controller
                  control={control}
                  name={`lines.${index}.discount_percent`}
                  render={({ field: discount }) => (
                    <PercentInput nonNegative value={discount.value} onValueChange={discount.onChange} onBlur={discount.onBlur} />
                  )}
                />
              </FormField>

              <FormField label="Tax rate" error={lineErrors?.tax_rate?.message ?? null} className="sm:col-span-2">
                <Controller
                  control={control}
                  name={`lines.${index}.tax_rate`}
                  render={({ field: rate }) => (
                    <PercentInput nonNegative value={rate.value} onValueChange={rate.onChange} onBlur={rate.onBlur} />
                  )}
                />
              </FormField>

              <div className="flex flex-col justify-end sm:col-span-3">
                <p className="text-2xs font-medium tracking-wide text-ink-500 uppercase">Line total (est.)</p>
                <p className="flex h-9 items-center justify-end text-sm">
                  {estimate ? <Money value={estimate.line_total} /> : <span className="text-ink-400">—</span>}
                </p>
              </div>

              {renderExtra ? <div className="sm:col-span-12">{renderExtra(index)}</div> : null}
            </fieldset>
          );
        })}
      </CardBody>
    </Card>
  );
}

/**
 * The running totals beside a document form, labelled as the estimate it is.
 */
export function EstimatedTotals({ currency }: { currency?: string }) {
  const { control } = useFormContext<LinesForm>();
  const lines = useWatch({ control, name: "lines" });
  const totals = estimateTotals(lines ?? []);
  const moneyProps = currency ? { currency } : {};

  return (
    <Card>
      <CardHeader
        title="Totals"
        description="Estimated while you type. The saved document shows the figures calculated by the accounting engine."
      />
      <CardBody className="flex flex-col items-end gap-2">
        <TotalsPanel
          rows={[
            { label: "Subtotal", value: <Money value={totals.subtotal} {...moneyProps} /> },
            { label: "Discount", value: <Money value={totals.discount_total} {...moneyProps} />, muted: true },
            { label: "Tax", value: <Money value={totals.tax_total} {...moneyProps} /> },
            { label: "Total (estimate)", value: <Money value={totals.total} strong {...moneyProps} />, emphasis: true },
          ]}
        />
        {!totals.complete && (lines?.length ?? 0) > 0 ? (
          <p className="text-xs text-ink-500">Incomplete lines are not included yet.</p>
        ) : null}
      </CardBody>
    </Card>
  );
}
