# INVENTORY

PURPOSE
Authoritative inventory movement system: warehouses, stock movements, stock adjustments, transfers, weighted-average valuation, reorder detection. Financial effects only ever go through Phase 1's accounting posting engine.

OWNS
- `models/warehouse.py` — `Warehouse`. At most one `is_default=True` per org, enforced by a partial `UniqueConstraint`.
- `models/stock_movement.py` — `StockMovement`: the ONLY authoritative record of a quantity change. Append-only (`save()`/`delete()` raise after creation, same pattern as `audit.AuditLog`).
- `models/stock_adjustment.py` — `StockAdjustment`/`StockAdjustmentLine`: DRAFT→POSTED→REVERSED lifecycle mirroring `accounting.JournalEntry` deliberately (immutability guard, idempotent posting, reversal-by-counter-entry via a `OneToOneField`).
- `models/settings.py` — `InventorySettings`: one row per org, holds `allow_negative_stock` (default `False` = BLOCK). Lives here rather than on `accounts.Organization` because this phase does not modify Phase 0 models.
- `services/movements.py` — `record_stock_movement()`: the single authoritative path to change stock-on-hand. No other code path may create a `StockMovement`.
- `services/adjustments.py`, `services/opening_stock.py`, `services/transfers.py`, `services/accounting_bridge.py` (the only place that calls into `accounting.services.journals`/`posting`).
- `selectors.py` — stock-on-hand, weighted-average cost, low-stock detection — all derived on demand from `StockMovement`, never a cached balance.

INVARIANTS
- No mutable `item.quantity`-style field anywhere. Stock-on-hand = SUM(inbound) − SUM(outbound) over `StockMovement`, always computed on demand.
- Decimal only for quantities/costs (`quantity`: 4 decimal places; money fields as elsewhere: 2).
- Negative-stock policy: **BLOCK by default** (`InventorySettings.allow_negative_stock=False`). Enforced in `record_stock_movement` by locking every existing movement row for the `(item, warehouse)` pair via `select_for_update()` before evaluating the outbound quantity against on-hand stock — this is what makes concurrent issues against the same item/warehouse safe without a separate mutable balance/lock row. See the function's docstring before changing this.
- `StockMovement.sequence` (a DB-sequence-backed `BigIntegerField`, NOT the UUID primary key) is the deterministic chronological tiebreaker used by `get_weighted_average_cost` and adjustment-reversal cost lookups. **Never** fall back to `id`/`created_at` alone for this ordering — a random UUID tiebreaker made valuation non-deterministic in testing (ties happen often enough on real clocks) and was the reason this field exists. Django's `AutoField` requires `primary_key=True`, hence the raw-SQL Postgres sequence in migration `0007` plus `StockMovement.save()` populating it via `nextval()`.
- Backdating is **supported, not restricted**: valuation/balance are always full replays ordered by `(movement_date, sequence)`, never cached, so a backdated movement is picked up correctly the next time it's queried. Known limitation: the negative-stock guard checks the aggregate as of *now*, not a full historical minimum-balance check across the whole timeline.
- Weighted-average valuation: issues never change `average_cost` (only inbound movements do); if quantity reaches zero (or goes negative under `ALLOW_NEGATIVE_STOCK`), `average_cost` freezes until the next inbound movement.
- Posted `StockAdjustment`s (and their lines) are immutable except through `reverse_stock_adjustment()` — never a destructive edit.
- A `StockAdjustment`'s accounting journal (if `contra_account` is set) is posted only through `inventory.services.accounting_bridge.post_inventory_journal`, which itself only calls `accounting.services.journals.create_draft_journal` + `accounting.services.posting.post_journal`. Nothing in this app constructs a `JournalLine` directly.
- Omitting `contra_account` on an adjustment/opening-stock posts a quantity-only change with no accounting journal — deliberate (no implicit "shrinkage account" is invented), not an oversight.
- Transfers have no separate header table: a matched `TRANSFER_OUT`/`TRANSFER_IN` pair of `StockMovement`s sharing `source_type="stock_transfer"` + a generated `source_id` IS the transfer record.
- Tenant isolation mandatory — every model here is `TenantScopedModel` with a matching RLS migration.

READ FIRST
- `models/stock_movement.py`, `services/movements.py`, `selectors.py`

TOKEN DISCIPLINE
- Do not duplicate root, `core`, or `accounting` CLAUDE.md content here.
