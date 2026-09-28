import type { DateTimeString, DecimalString, UUID } from "@/lib/api/types";

/** items.ItemSerializer */
export type ItemType = "product" | "service";

export interface Item {
  id: UUID;
  sku: string;
  name: string;
  description: string;
  item_type: ItemType;
  unit: UUID;
  /**
   * Only a product can track inventory. A service with stock actions is a
   * category error, and the UI hides them on that basis (spec §37).
   */
  track_inventory: boolean;
  is_sellable: boolean;
  is_purchasable: boolean;
  sales_price: DecimalString | null;
  purchase_price: DecimalString | null;
  reorder_level: DecimalString;
  hsn_sac_code: UUID | null;
  tax_category: string;
  sales_account: UUID | null;
  purchase_account: UUID | null;
  inventory_account: UUID | null;
  cogs_account: UUID | null;
  is_active: boolean;
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

export interface ItemInput {
  sku: string;
  name: string;
  description?: string;
  item_type: ItemType;
  unit: UUID;
  track_inventory?: boolean;
  is_sellable?: boolean;
  is_purchasable?: boolean;
  sales_price?: DecimalString | null;
  purchase_price?: DecimalString | null;
  reorder_level?: DecimalString;
  hsn_sac_code?: UUID | null;
  tax_category?: string;
  sales_account?: UUID | null;
  purchase_account?: UUID | null;
  inventory_account?: UUID | null;
  cogs_account?: UUID | null;
  is_active?: boolean;
}

/** items.UnitOfMeasureSerializer */
export interface UnitOfMeasure {
  id: UUID;
  code: string;
  name: string;
  symbol: string;
  /** System units are seeded and cannot be deleted. */
  is_system: boolean;
  is_active: boolean;
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

export interface UnitOfMeasureInput {
  code: string;
  name: string;
  symbol?: string;
  is_active?: boolean;
}
