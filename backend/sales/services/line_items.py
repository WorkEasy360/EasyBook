"""Shared line-building logic for every sales document (Quote, SalesOrder,
and later Invoice/CreditNote) — item validation and the item-time snapshot
are identical across documents; only the target model differs. See
sales/CLAUDE.md snapshot principle and services/calculations.py.
"""

from decimal import Decimal

from core.exceptions import ApplicationError
from items.models.item import Item
from sales.services.calculations import calculate_line


def validate_item_for_line(*, organization, item: Item) -> None:
    if item.organization_id != organization.id:
        raise ApplicationError("Item must belong to the posting organization.", code="item_cross_org")
    if not item.is_active:
        raise ApplicationError("Inactive items cannot be used in new sales documents.", code="item_inactive")
    if not item.is_sellable:
        raise ApplicationError("This item is not sellable.", code="item_not_sellable")


def build_line_snapshot(*, organization, line: dict, line_number: int) -> dict:
    """Validates `line["item"]` and returns a dict of the fields shared by
    every sales document line model (QuoteLine, SalesOrderLine, ...):
    line_number, item, snapshot fields, and calculate_line's computed amounts.
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
