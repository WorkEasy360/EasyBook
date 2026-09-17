import { z } from "zod";
import { pricedLineSchema } from "@/features/documents/priced-lines";

/**
 * The shared priced-line schema, narrowed to the purchases models' limits.
 *
 * PurchaseOrderLine / BillLine / VendorCreditLine / RecurringBillTemplateLine
 * store `description` as max_length=255, but PricedLineInputSerializer
 * declares no max_length — a longer description would pass DRF and fail at
 * the database. Caught here instead, with a message.
 */
export const purchaseLineSchema = pricedLineSchema.extend({
  description: z.string().max(255, "At most 255 characters."),
});
