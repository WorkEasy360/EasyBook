import { z } from "zod";
import { isValidDecimal, money } from "@/lib/money";

/**
 * Priced-line values, validation and API mapping — the pure half of the line
 * editor.
 *
 * Kept out of priced-lines-editor.tsx on purpose: that module is
 * "use client", and a function exported from a client module is only a client
 * reference on the server. A Server Component that builds initial lines (a
 * credit note from an invoice, a bill from an order) must import these from
 * here to be able to call them.
 */

export type PricedLineValues = {
  item_id: string;
  description: string;
  quantity: string;
  unit_price: string;
  discount_percent: string;
  tax_rate: string;
};

export const EMPTY_PRICED_LINE: PricedLineValues = {
  item_id: "",
  description: "",
  quantity: "1",
  unit_price: "",
  discount_percent: "",
  tax_rate: "",
};

const optionalPercent = (label: string, max?: number) =>
  z.string().refine((value) => {
    if (value.trim() === "") return true;
    if (!isValidDecimal(value)) return false;
    const amount = money(value);
    return amount.gte(0) && (max === undefined || amount.lte(max));
  }, max === undefined ? `${label} cannot be negative.` : `${label} must be between 0 and ${max}.`);

/**
 * Validation matching core/money.py :: calculate_line's guards, so the common
 * mistakes are caught before a round trip. The backend re-validates all of it.
 */
export const pricedLineSchema = z.object({
  item_id: z.string().min(1, "Choose an item."),
  description: z.string().max(500),
  quantity: z
    .string()
    .refine((value) => isValidDecimal(value) && money(value).gt(0), "Enter a quantity above zero."),
  unit_price: z
    .string()
    .refine((value) => isValidDecimal(value) && money(value).gte(0), "Enter a price of zero or more."),
  discount_percent: optionalPercent("Discount", 100),
  tax_rate: optionalPercent("Tax rate"),
});

/** Strips form-only blanks into the API's line input shape. */
export function toPricedLineInput(line: PricedLineValues) {
  return {
    item_id: line.item_id,
    description: line.description.trim(),
    quantity: line.quantity,
    unit_price: line.unit_price,
    discount_percent: line.discount_percent.trim() === "" ? "0" : line.discount_percent,
    tax_rate: line.tax_rate.trim() === "" ? "0" : line.tax_rate,
  };
}

/** Read-side line → editable values, for editing a draft. */
export function fromPricedLine(line: {
  item: string;
  description: string;
  quantity: string;
  unit_price: string;
  discount_percent: string;
  tax_rate: string;
}): PricedLineValues {
  return {
    item_id: line.item,
    description: line.description,
    quantity: line.quantity,
    unit_price: line.unit_price,
    discount_percent: line.discount_percent,
    tax_rate: line.tax_rate,
  };
}
