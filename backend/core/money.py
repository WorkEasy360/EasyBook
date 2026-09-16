"""The single reusable line/document total calculation service (see root
CLAUDE.md - rounding logic must not be duplicated across modules,
serializers, models or views, and totals are never trusted from the client).

Lives in `core` rather than in one domain app because it is pure Decimal
money math with no domain concepts in it: every priced document in every
module - sales (Quote, SalesOrder, Invoice, CreditNote) and purchases
(PurchaseOrder, Bill, VendorCredit) alike - computes its line and header
totals through these two functions only. It started life in
`sales/services/calculations.py` and moved here when `purchases` needed it:
a sideways purchases -> sales import would couple two peer modules (root
CLAUDE.md module dependency rule), and copying the rounding policy into a
second module is exactly what the "one rounding policy" rule forbids.
"""

from decimal import ROUND_HALF_UP, Decimal

from core.exceptions import ApplicationError

MONEY_QUANTUM = Decimal("0.01")


def round_money(value: Decimal) -> Decimal:
    """The single money-rounding policy: 2 dp, ROUND_HALF_UP.

    Public because `tax.services.computation` splits an already-rounded line
    tax into CGST/SGST/IGST/cess and must round the halves the same way this
    module rounds everything else. A second `quantize` over there would be a
    second rounding policy in all but name - exactly what this module exists
    to prevent (see the module docstring).
    """
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


# Retained so the existing call sites in this module read unchanged.
_round_money = round_money


def calculate_line(
    *,
    quantity: Decimal,
    unit_price: Decimal,
    discount_percent: Decimal = Decimal("0"),
    tax_rate: Decimal = Decimal("0"),
) -> dict:
    """Computes one line's amounts from its inputs. `discount_percent` and
    `tax_rate` are percentages (0-100), applied in that order — discount
    reduces the taxable base, tax is calculated on the post-discount amount.

    Returns Decimal amounts already rounded to the money quantum (2 dp) at
    the line level — document totals (calculate_document_totals) sum these
    already-rounded values rather than rounding a second time, so line and
    header totals always agree to the cent.
    """
    if quantity <= 0:
        raise ApplicationError("Line quantity must be positive.", code="line_quantity_invalid")
    if unit_price < 0:
        raise ApplicationError("Line unit price cannot be negative.", code="line_unit_price_invalid")
    if discount_percent < 0 or discount_percent > 100:
        raise ApplicationError("Line discount percent must be between 0 and 100.", code="line_discount_invalid")
    if tax_rate < 0:
        raise ApplicationError("Line tax rate cannot be negative.", code="line_tax_rate_invalid")

    line_base = _round_money(quantity * unit_price)
    discount_amount = _round_money(line_base * discount_percent / Decimal("100"))
    taxable_amount = line_base - discount_amount
    tax_amount = _round_money(taxable_amount * tax_rate / Decimal("100"))
    line_total = taxable_amount + tax_amount

    return {
        "line_base": line_base,
        "discount_amount": discount_amount,
        "taxable_amount": taxable_amount,
        "tax_amount": tax_amount,
        "line_total": line_total,
    }


def calculate_document_totals(lines: list[dict]) -> dict:
    """Sums already-computed line dicts (as returned by calculate_line, merged
    into the caller's per-line data) into header-level totals."""
    subtotal = sum((line["line_base"] for line in lines), Decimal("0"))
    discount = sum((line["discount_amount"] for line in lines), Decimal("0"))
    tax = sum((line["tax_amount"] for line in lines), Decimal("0"))
    total = sum((line["line_total"] for line in lines), Decimal("0"))
    return {"subtotal": subtotal, "discount": discount, "tax": tax, "total": total}
