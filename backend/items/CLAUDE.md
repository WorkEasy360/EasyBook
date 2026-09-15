# ITEMS

PURPOSE
Reusable product/service master data: units of measure, items, HSN/SAC metadata. Owns no stock quantity or GL balance — inventory and accounting derive from posted facts elsewhere.

OWNS
- `models/unit.py` — `UnitOfMeasure`, org-scoped (not global like `accounts.Currency` — see file docstring for why).
- `models/hsn_sac.py` — `HsnSacCode`, manual-entry only. No catalog is seeded; root `CLAUDE.md` forbids inventing compliance data.
- `models/item.py` — `Item` (PRODUCT/SERVICE; COMPOSITE deliberately not offered — nothing implements it yet).
- `services/items.py`, `services/units.py`, `services/hsn_sac.py`.

INVARIANTS
- Tenant scoped, RLS-protected, like every app in this repo.
- SKU unique per organization where provided (partial `UniqueConstraint`, blank SKUs excluded).
- Decimal for all prices — never float.
- A `SERVICE` item cannot have `track_inventory=True` — enforced both in `services/items.py` and as a DB `CheckConstraint` (belt-and-braces, since it's a single-row rule).
- `sales_account`/`purchase_account`/`inventory_account`/`cogs_account` are validated against `accounting.Account.account_type` (INCOME/EXPENSE/ASSET/EXPENSE respectively) and must belong to the same organization — see `services/items.py::_validate_account_fields`.
- Inactive units cannot be assigned to new items; inactive items cannot be used for new operational transactions (inventory enforces the latter at movement time).
- No hard-delete API — accounts/items are archived (`is_active=False`) via `archive_item`/`update_unit`, never destructively deleted; `on_delete=PROTECT` on every referencing FK backs this up at the DB layer.
- `is_system` (units) mirrors `accounting.Account.is_system`: protects a small set of fields from mutation, nothing more.

READ FIRST
- `models/item.py`, `services/items.py`

TOKEN DISCIPLINE
- Do not duplicate root or `core`/`accounting` CLAUDE.md content here.
