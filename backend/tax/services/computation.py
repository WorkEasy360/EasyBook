"""Splitting one line's tax into its GST components.

`core.money.calculate_line` already answers "how much tax is there" - it is
pure, domain-free money math shared by every priced document in the system, and
it stays that way. This module answers the domain question that follows: of
that amount, how much is CGST, how much SGST/UTGST, how much IGST, how much
cess. GST is a domain concept; putting it in `core` would make the shared
rounding helper know about Indian statute.

THE CENT INVARIANT
------------------
`cgst + sgst + igst + cess == tax_amount`, always, exactly. This is not a
rounding nicety: the journal credits each component to its own GL account and
debits the customer the document total, so a cent lost between the halves is a
journal that does not balance and a document that cannot post.

It is guaranteed by construction, not by hope. CGST is computed and rounded,
and SGST is then the REMAINDER - `tax_amount - cgst` - rather than a second
independent rounding of the same half. On a 5% supply of 100.05, half-and-half
would round 2.50125 twice to 2.50 and 2.50, summing to 5.00 against a
tax_amount of 5.00; but on a taxable amount of 101.00 at 5%, the two
independent halves (2.53 each, from 2.525) would sum to 5.06 against a
tax_amount of 5.05. The remainder approach cannot drift.
"""

from decimal import Decimal

from core.exceptions import ApplicationError
from core.money import round_money
from tax.enums import SupplyNature
from tax.services.determination import is_inter_state, is_zero_rated

_HUNDRED = Decimal("100")
_TWO = Decimal("2")
ZERO = Decimal("0")


def split_tax(
    *,
    taxable_amount: Decimal,
    tax_rate: Decimal = ZERO,
    cess_rate: Decimal = ZERO,
    supply_nature: str = SupplyNature.UNSPECIFIED,
) -> dict:
    """Returns {"cgst", "sgst", "igst", "cess", "tax_amount"} as Decimals.

    `tax_amount` is recomputed here from the same inputs `core.money` uses, not
    passed in, so the components and the total can never be derived from
    different bases. The caller asserts the two agree.

    UNSPECIFIED yields the full tax as a single uncomponented amount. That is
    what every document created before this phase reads as, and what a document
    in a non-GST jurisdiction reads as: there is tax, but no GST split applies
    to it. It is a legitimate resting state, not an error.
    """
    if taxable_amount < 0:
        raise ApplicationError(
            "Taxable amount cannot be negative.", code="taxable_amount_invalid"
        )
    if tax_rate < 0:
        raise ApplicationError("Tax rate cannot be negative.", code="line_tax_rate_invalid")
    if cess_rate < 0:
        raise ApplicationError("Cess rate cannot be negative.", code="line_cess_rate_invalid")

    if is_zero_rated(supply_nature):
        # Zero-rated is not "no tax line" - it is a taxable supply at nil. The
        # rate stays on the document for GSTR-1; the amounts are zero.
        return {
            "cgst": ZERO, "sgst": ZERO, "igst": ZERO, "cess": ZERO, "tax_amount": ZERO,
        }

    tax_amount = round_money(taxable_amount * tax_rate / _HUNDRED)
    cess_amount = round_money(taxable_amount * cess_rate / _HUNDRED)

    if supply_nature == SupplyNature.UNSPECIFIED:
        # Cess is a GST levy; charging it on a document whose supply nature was
        # never determined would produce a line whose components cannot sum to
        # its own tax_amount, breaking the invariant the check constraints on
        # every line model rely on. Refuse rather than silently drop it.
        if cess_rate > 0:
            raise ApplicationError(
                "Cess cannot be applied to a document with no determined supply nature. "
                "Set the place of supply first.",
                code="cess_requires_supply_nature",
            )
        return {
            "cgst": ZERO, "sgst": ZERO, "igst": ZERO,
            "cess": ZERO,
            "tax_amount": tax_amount,
        }

    if is_inter_state(supply_nature):
        cgst = sgst = ZERO
        igst = tax_amount
    else:
        # Round one half, then take the other as the remainder. See the module
        # docstring - this is what makes the pair re-sum exactly.
        cgst = round_money(taxable_amount * (tax_rate / _TWO) / _HUNDRED)
        sgst = tax_amount - cgst
        igst = ZERO

    return {
        "cgst": cgst,
        "sgst": sgst,
        "igst": igst,
        "cess": cess_amount,
        "tax_amount": tax_amount + cess_amount,
    }


def apply_tax_components(row: dict, *, supply_nature: str, cess_rate: Decimal = ZERO) -> dict:
    """Adds the GST component columns to one already-built line snapshot.

    Kept separate from `sales`/`purchases` `build_line_snapshot` rather than
    folded into it, because only the documents that POST carry component
    columns. A Quote or Purchase Order has no journal and appears in no return,
    so splitting its tax would mean five more columns on three more tables for
    display-only value. Those callers use the snapshot builder alone; the five
    posting documents run their rows through here afterwards.

    Returns a NEW dict - the caller's row is not mutated - carrying the
    original keys plus `cess_rate`/`cgst_amount`/`sgst_amount`/`igst_amount`/
    `cess_amount`, with `tax_amount` and `line_total` RECOMPUTED.

    That recomputation is the subtle part. `core.money.calculate_line` knows
    nothing about cess, so the `tax_amount` it produced covers GST only. If
    cess applies, the line's real tax is larger and its `line_total` with it -
    leaving the original values would make the document total disagree with
    the sum of what actually posts to the tax accounts.
    """
    split = split_tax(
        taxable_amount=row["taxable_amount"],
        tax_rate=row.get("tax_rate", ZERO),
        cess_rate=cess_rate,
        supply_nature=supply_nature,
    )
    return {
        **row,
        "cess_rate": cess_rate,
        "cgst_amount": split["cgst"],
        "sgst_amount": split["sgst"],
        "igst_amount": split["igst"],
        "cess_amount": split["cess"],
        "tax_amount": split["tax_amount"],
        "line_total": row["taxable_amount"] + split["tax_amount"],
    }


def component_totals(rows: list[dict]) -> dict:
    """Sums already-computed per-line component dicts into header totals.

    Mirrors `core.money.calculate_document_totals`: it sums values that are
    already rounded rather than rounding again, so header and line totals agree
    to the cent by construction.

    Accepts the LINE-MODEL key names (`cgst_amount`, ...) rather than
    `split_tax`'s short keys, because that is the shape the caller has once the
    snapshot dict has been built.
    """
    return {
        "cgst_total": sum((row["cgst_amount"] for row in rows), ZERO),
        "sgst_total": sum((row["sgst_amount"] for row in rows), ZERO),
        "igst_total": sum((row["igst_amount"] for row in rows), ZERO),
        "cess_total": sum((row["cess_amount"] for row in rows), ZERO),
    }


def compute_withholding(*, base_amount: Decimal, section) -> Decimal:
    """TDS/TCS on a base amount. `section` is a `WithholdingSection` or None.

    Deliberately does NOT consult `section.threshold_amount`: whether a payee
    has crossed a cumulative annual threshold is a stateful judgement across
    documents this function cannot see, and silently returning zero because one
    invoice is below the threshold would under-deduct on the invoice that
    crosses it. The organization decides whether the section applies; this
    computes the amount once it does.
    """
    if section is None:
        return ZERO
    if base_amount < 0:
        raise ApplicationError(
            "Withholding base amount cannot be negative.", code="withholding_base_invalid"
        )
    return round_money(base_amount * section.rate / _HUNDRED)
