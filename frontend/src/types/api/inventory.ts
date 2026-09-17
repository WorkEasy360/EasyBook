import type { DateString, DateTimeString, DecimalString, UUID } from "@/lib/api/types";

/** inventory.WarehouseSerializer */
export interface Warehouse {
  id: UUID;
  code: string;
  name: string;
  address: string;
  is_default: boolean;
  is_active: boolean;
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

export interface WarehouseInput {
  code: string;
  name: string;
  address?: string;
  is_default?: boolean;
  is_active?: boolean;
}

/**
 * GET /inventory/stock-summary/ — on-hand quantity per item, from the movement
 * ledger. NOT paginated and NOT enveloped: the view returns a bare array
 * (inventory/api/views.py :: StockSummaryView). `warehouse_id` is null unless
 * the request filtered by one, in which case `on_hand` is for that warehouse.
 *
 * There is deliberately no "set quantity" control anywhere in the UI
 * (spec §38): stock changes only through a posted adjustment or a document.
 */
export interface StockOnHandRow {
  item_id: UUID;
  item_name: string;
  warehouse_id: UUID | null;
  on_hand: DecimalString;
  reorder_level: DecimalString;
  is_low_stock: boolean;
}

/**
 * GET /reports/inventory/summary/ and /reports/inventory/valuation/ rows —
 * one per item × warehouse, valued at moving average cost by the backend.
 * Captured from a live response; `inventory_value` carries 8 decimal places.
 */
export interface InventoryPositionRow {
  item_id: UUID;
  item_name: string;
  item_sku: string;
  warehouse_id: UUID;
  warehouse_name: string;
  quantity_on_hand: DecimalString;
  average_cost: DecimalString;
  inventory_value: DecimalString;
}

/** GET /reports/inventory/summary/ */
export interface InventorySummary {
  rows: InventoryPositionRow[];
  as_of: DateString | null;
}

/** GET /reports/inventory/valuation/ */
export interface InventoryValuation {
  rows: InventoryPositionRow[];
  as_of: DateString | null;
  total_value: DecimalString;
}

/** GET /reports/inventory/low-stock/ — organization-wide, not per warehouse. */
export interface LowStockItemRow {
  item_id: UUID;
  item_name: string;
  item_sku: string;
  on_hand: DecimalString;
  reorder_level: DecimalString;
}

export type MovementType =
  | "opening"
  | "receipt"
  | "issue"
  | "adjustment_in"
  | "adjustment_out"
  | "transfer_in"
  | "transfer_out";

/** inventory.StockMovementSerializer */
export interface StockMovement {
  id: UUID;
  item: UUID;
  warehouse: UUID;
  movement_type: MovementType;
  quantity: DecimalString;
  unit_cost: DecimalString | null;
  movement_date: DateTimeString;
  /** What caused it, e.g. "sales.Invoice". Read-only provenance. */
  source_type: string;
  source_id: string;
  notes: string;
  created_by: UUID | null;
  created_at: DateTimeString;
}

export type AdjustmentReason =
  | "physical_count"
  | "damaged"
  | "shrinkage"
  | "found"
  | "correction"
  | "other";

export type AdjustmentStatus = "draft" | "posted" | "reversed";

export interface StockAdjustmentLine {
  id: UUID;
  line_number: number;
  item: UUID;
  quantity: DecimalString;
  direction: "adjustment_in" | "adjustment_out";
  unit_cost: DecimalString | null;
  notes: string;
}

/** inventory.StockAdjustmentSerializer */
export interface StockAdjustment {
  id: UUID;
  warehouse: UUID;
  adjustment_date: DateString;
  reason: AdjustmentReason;
  status: AdjustmentStatus;
  lines: StockAdjustmentLine[];
  memo: string;
  contra_account: UUID | null;
  accounting_journal: UUID | null;
  /** Set when this adjustment reverses another. */
  reverses: UUID | null;
  posted_at: DateTimeString | null;
  posted_by: UUID | null;
  created_by: UUID | null;
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

export interface StockAdjustmentLineInput {
  item_id: UUID;
  quantity: DecimalString;
  direction: "adjustment_in" | "adjustment_out";
  unit_cost?: DecimalString | null;
  notes?: string;
}

/** inventory.StockAdjustmentCreateSerializer */
export interface StockAdjustmentInput {
  warehouse_id: UUID;
  adjustment_date: DateString;
  reason: AdjustmentReason;
  memo?: string;
  contra_account_id?: UUID | null;
  lines: StockAdjustmentLineInput[];
}

/** inventory.StockTransferSerializer */
export interface StockTransferInput {
  item_id: UUID;
  from_warehouse_id: UUID;
  to_warehouse_id: UUID;
  quantity: DecimalString;
  notes?: string;
}

export const ADJUSTMENT_REASON_LABELS: Record<AdjustmentReason, string> = {
  physical_count: "Physical count",
  damaged: "Damaged",
  shrinkage: "Shrinkage",
  found: "Found",
  correction: "Correction",
  other: "Other",
};

export const MOVEMENT_TYPE_LABELS: Record<MovementType, string> = {
  opening: "Opening",
  receipt: "Receipt",
  issue: "Issue",
  adjustment_in: "Adjustment in",
  adjustment_out: "Adjustment out",
  transfer_in: "Transfer in",
  transfer_out: "Transfer out",
};

/** POST /inventory/transfers/ — two linked movements, never a quantity edit. */
export interface StockTransferResult {
  transfer_id: UUID;
  out_movement: StockMovement;
  in_movement: StockMovement;
}

/**
 * `source_type` values written by the backend services, mapped to the route
 * that shows the source record. Unknown types render as plain text.
 */
export const MOVEMENT_SOURCE_ROUTES: Record<string, { label: string; href: (id: string) => string }> = {
  stock_adjustment: { label: "Stock adjustment", href: (id) => `/inventory/adjustments/${id}` },
};
