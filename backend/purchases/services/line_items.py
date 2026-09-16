"""Shared line-building logic for every priced purchase document
(PurchaseOrder, Bill, VendorCredit) — item validation and the item-time
snapshot are identical across them; only the target model differs.

The buy-side counterpart of `sales/services/line_items.py`. Deliberately NOT
shared with it: the two differ on the one thing that matters — sales
validates `is_sellable`, purchases validates `is_purchasable` — and the
"required account" a line must resolve is `sales_account` there versus
`purchase_account`/`expense_account` here. Merging them would mean a
`side="sales"|"purchases"` flag threaded through every call, which is the
duplicated-permissions smell root CLAUDE.md warns about rather than reuse.
The genuinely shared part, the rounding, lives in `core/money.py` and is
used by both.
"""

from decimal import Decimal

from core.exceptions import ApplicationError
from core.money import calculate_line
from items.models.item import Item, ItemType


def validate_item_for_line(*, organization, item: Item) -> None:
    if item.organization_id != organization.id:
        raise ApplicationError("Item must belong to the posting organization.", code="item_cross_org")
    if not item.is_active:
        raise ApplicationError("Inactive items cannot be used in new purchase documents.", code="item_inactive")
    if not item.is_purchasable:
        raise ApplicationError("This item is not purchasable.", code="item_not_purchasable")


def is_inventoried(item: Item) -> bool:
    """True when a line for this item moves stock and is capitalised to the
    Inventory Asset account; False when it is expensed on receipt. The single
    definition every purchase service asks — never re-derived inline, so
    "inventoried" cannot come to mean different things in bills, credits and
    receipts."""
    return item.item_type == ItemType.PRODUCT and item.track_inventory


def build_line_snapshot(*, organization, line: dict, line_number: int) -> dict:
    """Validates `line["item"]` and returns a dict of the fields shared by
    every priced purchase document line model (PurchaseOrderLine, BillLine,
    VendorCreditLine): line_number, item, snapshot fields, and
    calculate_line's computed amounts.
    """
    item: Item = line["item"]
    validate_item_for_line(organization=organization, item=item)

    quantity = line["quantity"]
    unit_price = line["unit_price"]
    discount_percent = line.get("discount_percent", Decimal("0"))
    tax_rate = line.get("tax_rate", Decimal("0"))

    computed = calculate_line(
        quantity=quantity, unit_price=unit_price, discount_percent=discount_percent, tax_rate=tax_rate
    )

    return {
        "line_number": line_number,
        "item": item,
        # Snapshot at document time — never re-read live Item fields later.
        "description": line.get("description") or item.name,
        "hsn_sac_snapshot": line.get("hsn_sac_snapshot") or (item.hsn_sac_code.code if item.hsn_sac_code_id else ""),
        "tax_label": line.get("tax_label") or item.tax_category,
        "quantity": quantity,
        "unit_price": unit_price,
        "discount_percent": discount_percent,
        "tax_rate": tax_rate,
        **computed,
    }


def resolve_expense_account(*, item: Item, expense_account=None):
    """Which account a NON-inventoried purchase line is charged to.

    Line-level `expense_account` wins over the item's own `purchase_account`
    — the same item is legitimately coded differently on different bills (see
    BillLine.expense_account). Returns None when neither is set; the caller
    decides whether that is fatal, since an inventoried line never reaches
    here at all.
    """
    if expense_account is not None:
        return expense_account
    return item.purchase_account
