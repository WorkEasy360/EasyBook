"use client";

import * as React from "react";
import { Controller, useFormContext, useWatch, type FieldErrors } from "react-hook-form";
import { FormField } from "@/components/ui/field";
import { AccountPicker } from "@/features/shared/pickers";
import { useLookup } from "@/lib/hooks/use-api";
import type { Item } from "@/types/api/items";

/**
 * Per-line additions to the shared PricedLinesEditor for purchase documents.
 *
 * Which account a purchase line hits is decided by the backend
 * (purchases/services/line_items.py, bills.py, vendor_credits.py):
 *  - an INVENTORIED line (a product that tracks inventory) is capitalised to
 *    the item's inventory account — a line expense account is ignored, and the
 *    item must have an inventory account (item_missing_inventory_account);
 *  - any other line is charged to the line's expense account if given, else
 *    to the item's purchase account; with neither, saving fails
 *    (item_missing_purchase_account).
 * The field below says which case applies for the chosen item, so the user
 * is not asked for an account the engine will not use.
 */

/** Same query key as ItemPicker's lookup, so this reads the cached page rather than refetching. */
export function useItemIndex(): Map<string, Item> {
  const query = useLookup<Item>("items", "items", "", {
    extraParams: { page_size: 200, is_active: "true" },
  });
  const rows = query.data?.results;
  return React.useMemo(() => new Map((rows ?? []).map((item) => [item.id, item])), [rows]);
}

export function isInventoried(item: Pick<Item, "item_type" | "track_inventory">): boolean {
  return item.item_type === "product" && item.track_inventory;
}

/** True when a non-inventoried line would have no account to charge. */
export function needsExpenseAccount(item: Item | undefined, expenseAccountId: string | null | undefined): boolean {
  if (!item || isInventoried(item)) return false;
  return !expenseAccountId && !item.purchase_account;
}

type ExtraLine = {
  item_id: string;
  expense_account_id: string | null;
  source_order_line_id?: string | null;
  source_goods_receipt_line_id?: string | null;
  source_bill_line_id?: string | null;
};

export function LineExpenseAccountField({
  index,
  items,
  disabled = false,
}: {
  index: number;
  items: Map<string, Item>;
  disabled?: boolean;
}) {
  const { control, formState } = useFormContext<{ lines: ExtraLine[] }>();
  const itemId = useWatch({ control, name: `lines.${index}.item_id` });
  const item = itemId ? items.get(itemId) : undefined;
  const errors = formState.errors as FieldErrors<{ lines: ExtraLine[] }>;
  const inventoried = item ? isInventoried(item) : false;

  let hint: string;
  if (!item) hint = "Choose an item first.";
  else if (inventoried) hint = "Capitalised to the item's inventory account. An expense account is not used for stocked items.";
  else if (item.purchase_account) hint = "Leave blank to use the item's purchase account.";
  else hint = "Required: this item has no purchase account.";

  return (
    <FormField
      label="Expense account"
      required={Boolean(item && !inventoried && !item.purchase_account)}
      error={errors.lines?.[index]?.expense_account_id?.message ?? null}
      hint={hint}
      className="sm:max-w-md"
    >
      <Controller
        control={control}
        name={`lines.${index}.expense_account_id`}
        render={({ field }) => (
          <AccountPicker
            accountType="expense"
            value={field.value}
            onChange={field.onChange}
            disabled={disabled || inventoried}
          />
        )}
      />
    </FormField>
  );
}

/** A quiet note that a line is linked to the document it came from. */
export function LineSourceNote({ index }: { index: number }) {
  const { control } = useFormContext<{ lines: ExtraLine[] }>();
  const line = useWatch({ control, name: `lines.${index}` });
  const notes: string[] = [];
  if (line?.source_goods_receipt_line_id) notes.push("Bills a goods receipt line — posting will not receive this stock again.");
  else if (line?.source_order_line_id) notes.push("Linked to a purchase order line for matching.");
  if (line?.source_bill_line_id) notes.push("Credits a line of the original bill.");
  if (notes.length === 0) return null;
  return <p className="text-xs text-ink-500">{notes.join(" ")}</p>;
}
