"""Vendor credits — a financial correction in our favour from a vendor.

The mirror of `sales/services/credit_notes.py`, with the debit/credit sides
swapped: issuing one DEBITS Accounts Payable (we owe less) and CREDITS
whatever the original purchase was charged to (Inventory Asset for an
inventoried line, the expense account otherwise), plus a credit reversing
any input tax we had claimed.

Where the sales side nets an excess into a customer-credit LIABILITY, this
nets it into a vendor-credit ASSET — value we hold with the supplier.

`return_stock=True` is the ONLY trigger for a stock movement, and it moves
stock OUT (the goods go back to the vendor) where the sales-side restock
moves it IN.
"""

from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from accounting.models.account import AccountType
from accounting.services.currency import assert_base_currency
from accounting.services.posting import reverse_journal
from accounts.services import allocate_sequence_number
from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError
from core.money import calculate_document_totals
from inventory.models.stock_movement import MovementType
from inventory.services.movements import record_stock_movement
from items.models.item import ItemType
from purchases.accounting_bridge import post_purchase_journal
from purchases.models.bill import Bill
from purchases.models.vendor import Vendor
from purchases.models.vendor_credit import VendorCredit, VendorCreditLine, VendorCreditStatus
from purchases.selectors import get_bill_amount_due
from purchases.services.line_items import build_line_snapshot, is_inventoried, resolve_expense_account
from purchases.services.vendors import assert_vendor_usable_for_new_transaction
from tax.enums import SupplyNature, TaxDirection
from tax.services.computation import apply_tax_components, component_totals
from tax.services.documents import carry_forward_tax, resolve_document_tax
from tax.services.posting import build_tax_journal_lines

VENDOR_CREDIT_NUMBER_SEQUENCE_KEY = "vendor_credit"


def _validate_account(*, organization, account, expected_type, field_name):
    if account.organization_id != organization.id:
        raise ApplicationError(f"{field_name} must belong to the posting organization.", code="cross_org_reference")
    if account.account_type != expected_type:
        raise ApplicationError(
            f"{field_name} must reference an account of type '{expected_type}'.", code="invalid_account_type"
        )


def _build_vendor_credit_line_row(
    *, organization, credit_warehouse, line: dict, line_number: int,
    supply_nature: str = SupplyNature.UNSPECIFIED,
) -> dict:
    row = build_line_snapshot(organization=organization, line=line, line_number=line_number)
    row = apply_tax_components(
        row, supply_nature=supply_nature, cess_rate=line.get("cess_rate", Decimal("0"))
    )
    item = row["item"]
    source_bill_line = line.get("source_bill_line")
    expense_account = line.get("expense_account")
    return_stock = line.get("return_stock", False)
    unit_cost = line.get("unit_cost")

    if source_bill_line is not None:
        if source_bill_line.organization_id != organization.id:
            raise ApplicationError("Bill line must belong to the posting organization.", code="bill_line_cross_org")
        if source_bill_line.item_id != item.id:
            raise ApplicationError(
                "Vendor credit line item must match the linked bill line's item.", code="bill_line_item_mismatch"
            )
        if row["quantity"] > source_bill_line.quantity:
            raise ApplicationError(
                f"Credited quantity ({row['quantity']}) cannot exceed the billed quantity "
                f"({source_bill_line.quantity}).",
                code="credit_quantity_exceeds_bill",
            )

    if expense_account is not None:
        _validate_account(
            organization=organization, account=expense_account, expected_type=AccountType.EXPENSE,
            field_name="expense_account",
        )

    if return_stock:
        if item.item_type == ItemType.SERVICE:
            # Explicit per-line flag, so reject rather than silently ignore —
            # same treatment as sales.CreditNoteLine.restock.
            raise ApplicationError("Service items cannot be returned to stock.", code="service_cannot_return_stock")
        if not item.track_inventory:
            raise ApplicationError(
                "Item does not track inventory and cannot be returned.", code="item_not_tracked"
            )
        if unit_cost is None:
            raise ApplicationError("unit_cost is required for a returned line.", code="return_cost_required")
        if credit_warehouse is None:
            raise ApplicationError(
                "A warehouse is required to return inventory on a vendor credit.", code="warehouse_required"
            )

    # Which account this line's value comes back OUT of must be resolvable
    # now, while the credit is still a fixable draft.
    if is_inventoried(item):
        if item.inventory_account_id is None:
            raise ApplicationError(
                f"Item '{item.name}' tracks inventory but has no inventory_account configured.",
                code="item_missing_inventory_account",
            )
    elif resolve_expense_account(item=item, expense_account=expense_account) is None:
        raise ApplicationError(
            f"Item '{item.name}' has no purchase_account configured and the line supplies no expense_account.",
            code="item_missing_purchase_account",
        )

    row["source_bill_line"] = source_bill_line
    row["expense_account"] = expense_account
    row["return_stock"] = return_stock
    row["unit_cost"] = unit_cost
    return row


def _get_vendor_credit_for_update(*, vendor_credit_id, organization) -> VendorCredit:
    try:
        return VendorCredit.objects.select_for_update().get(id=vendor_credit_id, organization=organization)
    except VendorCredit.DoesNotExist:
        raise ApplicationError("Vendor credit not found.", code="vendor_credit_not_found", status_code=404)


@transaction.atomic
def create_vendor_credit(
    *,
    organization,
    vendor: Vendor,
    credit_date,
    lines: list[dict],
    source_bill: Bill | None = None,
    reason: str = "other",
    currency=None,
    exchange_rate: Decimal = Decimal("1"),
    reference: str = "",
    vendor_credit_number: str = "",
    payable_account=None,
    tax_recoverable_account=None,
    unapplied_credit_account=None,
    warehouse=None,
    notes: str = "",
    place_of_supply=None,
    tax_treatment: dict | None = None,
    actor=None,
) -> VendorCredit:
    if vendor.organization_id != organization.id:
        raise ApplicationError("Vendor must belong to the posting organization.", code="vendor_cross_org")
    assert_vendor_usable_for_new_transaction(vendor=vendor)

    if source_bill is not None:
        if source_bill.organization_id != organization.id:
            raise ApplicationError("Bill must belong to the posting organization.", code="bill_cross_org")
        if source_bill.vendor_id != vendor.id:
            raise ApplicationError("Bill does not belong to this vendor.", code="bill_vendor_mismatch")

    for account, expected_type, field_name in (
        (payable_account, AccountType.LIABILITY, "payable_account"),
        (tax_recoverable_account, AccountType.ASSET, "tax_recoverable_account"),
        # Credit we hold WITH a vendor is an asset — the mirror of the sales
        # side's unapplied customer credit being a liability.
        (unapplied_credit_account, AccountType.ASSET, "unapplied_credit_account"),
    ):
        if account is not None:
            _validate_account(
                organization=organization, account=account, expected_type=expected_type, field_name=field_name
            )
    if warehouse is not None and warehouse.organization_id != organization.id:
        raise ApplicationError("warehouse must belong to the posting organization.", code="warehouse_cross_org")
    if not lines:
        raise ApplicationError("A vendor credit needs at least one line.", code="vendor_credit_no_lines")

    currency = currency or (source_bill.currency if source_bill else vendor.currency)
    assert_base_currency(organization=organization, currency=currency, exchange_rate=exchange_rate)
    # A credit against a bill inherits that bill's treatment - crediting under
    # a different one would leave the two unable to net in the return.
    if tax_treatment is None:
        tax_treatment = (
            {k: v for k, v in carry_forward_tax(source_bill).items() if k != "is_reverse_charge"}
            if source_bill is not None
            else resolve_document_tax(
                organization=organization, party=vendor, place_of_supply=place_of_supply
            )
        )
    line_rows = [
        _build_vendor_credit_line_row(
            organization=organization, credit_warehouse=warehouse, line=line, line_number=index,
            supply_nature=tax_treatment["supply_nature"],
        )
        for index, line in enumerate(lines, start=1)
    ]
    totals = calculate_document_totals(line_rows)
    components = component_totals(line_rows)

    vendor_credit = VendorCredit.objects.create(
        organization=organization,
        vendor=vendor,
        vendor_credit_number=vendor_credit_number,
        source_bill=source_bill,
        reason=reason,
        credit_date=credit_date,
        reference=reference,
        currency=currency,
        exchange_rate=exchange_rate,
        payable_account=payable_account,
        tax_recoverable_account=tax_recoverable_account,
        unapplied_credit_account=unapplied_credit_account,
        warehouse=warehouse,
        subtotal=totals["subtotal"],
        discount_total=totals["discount"],
        tax_total=totals["tax"],
        total=totals["total"],
        notes=notes,
        created_by=actor,
        **components,
        **tax_treatment,
    )
    for row in line_rows:
        VendorCreditLine.objects.create(organization=organization, vendor_credit=vendor_credit, **row)

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="purchases.VendorCredit",
        object_id=vendor_credit.id,
        changes={"total": str(totals["total"])},
    )
    return vendor_credit


@transaction.atomic
def replace_vendor_credit_lines(*, vendor_credit: VendorCredit, lines: list[dict], actor=None) -> VendorCredit:
    """Replaces all lines on a DRAFT vendor credit and recomputes header
    totals. Raises otherwise — issued credits are immutable."""
    if vendor_credit.status != VendorCreditStatus.DRAFT:
        raise ApplicationError("Only draft vendor credits can be modified.", code="vendor_credit_not_draft")
    if not lines:
        raise ApplicationError("A vendor credit needs at least one line.", code="vendor_credit_no_lines")

    line_rows = [
        _build_vendor_credit_line_row(
            organization=vendor_credit.organization, credit_warehouse=vendor_credit.warehouse,
            line=line, line_number=index, supply_nature=vendor_credit.supply_nature,
        )
        for index, line in enumerate(lines, start=1)
    ]
    totals = calculate_document_totals(line_rows)
    components = component_totals(line_rows)

    vendor_credit.lines.all().delete()
    for row in line_rows:
        VendorCreditLine.objects.create(organization=vendor_credit.organization, vendor_credit=vendor_credit, **row)

    vendor_credit.subtotal = totals["subtotal"]
    vendor_credit.discount_total = totals["discount"]
    vendor_credit.tax_total = totals["tax"]
    vendor_credit.total = totals["total"]
    for field, value in components.items():
        setattr(vendor_credit, field, value)
    vendor_credit.save(
        update_fields=[
            "subtotal", "discount_total", "tax_total", "total", *components.keys(), "updated_at",
        ]
    )

    record_audit(
        organization_id=vendor_credit.organization_id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="purchases.VendorCredit",
        object_id=vendor_credit.id,
        changes={"total": str(totals["total"])},
    )
    return vendor_credit


@transaction.atomic
def issue_vendor_credit(*, vendor_credit_id, organization, actor=None) -> VendorCredit:
    """The single authoritative path from DRAFT to an issued correction.
    Idempotent like post_bill.

    Posts ONE journal: Dr AP (netted against the source bill's remaining
    balance, if any) and/or Dr vendor-credit ASSET for any excess, against
    Cr Inventory Asset / Cr expense account (the original charge, reversed)
    and Cr recoverable tax. Lines with return_stock=True additionally issue
    an outbound stock movement. The original bill is never mutated.
    """
    vendor_credit = _get_vendor_credit_for_update(vendor_credit_id=vendor_credit_id, organization=organization)

    if vendor_credit.status == VendorCreditStatus.ISSUED:
        return vendor_credit
    if vendor_credit.status != VendorCreditStatus.DRAFT:
        raise ApplicationError(
            f"Cannot issue a vendor credit in status '{vendor_credit.status}'.",
            code="vendor_credit_invalid_status",
        )

    lines = list(
        VendorCreditLine.objects.select_for_update().select_related("item")
        .filter(vendor_credit=vendor_credit).order_by("line_number")
    )
    if not lines:
        raise ApplicationError("Cannot issue a vendor credit with no lines.", code="vendor_credit_no_lines")

    payable_account = vendor_credit.payable_account
    if vendor_credit.source_bill_id:
        source_bill = Bill.objects.select_for_update().get(pk=vendor_credit.source_bill_id)
        payable_account = payable_account or source_bill.payable_account
    else:
        source_bill = None

    tax_recoverable_account_id = vendor_credit.tax_recoverable_account_id or (
        source_bill.tax_recoverable_account_id if source_bill else None
    )
    # The account requirement is enforced by build_tax_journal_lines below,
    # which only demands one when the components cannot carry the tax.

    # Credit side: reverse whatever the original purchase was charged to.
    credit_by_account = defaultdict(lambda: Decimal("0"))
    for line in lines:
        item = line.item
        if is_inventoried(item):
            credit_by_account[item.inventory_account_id] += line.taxable_amount
        else:
            account = resolve_expense_account(item=item, expense_account=line.expense_account)
            credit_by_account[account.id] += line.taxable_amount

    # Net against the source bill's remaining balance first — any excess
    # becomes vendor credit we hold, never a negative AP balance.
    applied_to_ap = Decimal("0")
    unapplied = vendor_credit.total
    if source_bill is not None:
        due = get_bill_amount_due(bill=source_bill)
        applied_to_ap = min(vendor_credit.total, due) if due > 0 else Decimal("0")
        unapplied = vendor_credit.total - applied_to_ap

    if unapplied > 0 and vendor_credit.unapplied_credit_account_id is None:
        raise ApplicationError(
            "unapplied_credit_account is required when the vendor credit exceeds the bill's remaining balance.",
            code="unapplied_credit_account_required",
        )
    if applied_to_ap > 0 and payable_account is None:
        raise ApplicationError(
            "payable_account is required to issue this vendor credit.", code="payable_account_required"
        )

    journal_lines = []
    if applied_to_ap > 0:
        journal_lines.append({"account_id": payable_account.id, "debit": applied_to_ap})
    if unapplied > 0:
        journal_lines.append({"account_id": vendor_credit.unapplied_credit_account_id, "debit": unapplied})
    for account_id, amount in credit_by_account.items():
        if amount != 0:
            journal_lines.append({"account_id": account_id, "credit": amount})
    # A vendor credit reverses input tax, so each component is CREDITED here -
    # the mirror of post_bill's debits, through the same builder so the two
    # cannot disagree about which account a component belongs to.
    journal_lines.extend(
        build_tax_journal_lines(
            organization=organization,
            document=vendor_credit,
            direction=TaxDirection.INPUT,
            fallback_account_id=tax_recoverable_account_id,
            side="credit",
            account_required_message=(
                "tax_recoverable_account is required to issue a vendor credit with tax, unless "
                "every tax component is mapped to an account."
            ),
        )
    )

    for line in lines:
        if line.return_stock:
            record_stock_movement(
                organization=organization,
                item=line.item,
                warehouse=vendor_credit.warehouse,
                # OUT: the goods physically go back to the vendor. The exact
                # inverse of sales.CreditNote's restock ADJUSTMENT_IN.
                movement_type=MovementType.ADJUSTMENT_OUT,
                quantity=line.quantity,
                unit_cost=line.unit_cost,
                movement_date=vendor_credit.credit_date,
                source_type="purchases.VendorCredit",
                source_id=str(vendor_credit.id),
                notes=line.description,
                created_by=actor,
            )

    credit_number = allocate_sequence_number(
        organization_id=organization.id, key=VENDOR_CREDIT_NUMBER_SEQUENCE_KEY, prefix="VC-"
    )

    journal = post_purchase_journal(
        organization=organization,
        posting_date=vendor_credit.credit_date,
        currency=vendor_credit.currency,
        lines=journal_lines,
        memo=f"Vendor Credit {credit_number}",
        source_type="purchases.VendorCredit",
        source_id=str(vendor_credit.id),
        actor=actor,
    )

    vendor_credit.credit_number = credit_number
    vendor_credit.status = VendorCreditStatus.ISSUED
    vendor_credit.issued_by = actor
    vendor_credit.issued_at = timezone.now()
    vendor_credit.accounting_journal = journal
    vendor_credit.amount_applied_to_bill = applied_to_ap
    vendor_credit.save(
        update_fields=[
            "credit_number", "status", "issued_by", "issued_at", "accounting_journal",
            "amount_applied_to_bill", "updated_at",
        ]
    )

    if source_bill is not None:
        from purchases.services.bills import refresh_bill_payment_status

        refresh_bill_payment_status(bill=source_bill, actor=actor)

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.POST,
        object_type="purchases.VendorCredit",
        object_id=vendor_credit.id,
        changes={
            "credit_number": credit_number, "total": str(vendor_credit.total),
            "applied_to_ap": str(applied_to_ap), "unapplied": str(unapplied),
        },
    )
    return vendor_credit


@transaction.atomic
def void_vendor_credit(*, vendor_credit_id, organization, actor=None) -> VendorCredit:
    """Reverses an ISSUED vendor credit's accounting AND any stock return it
    performed — never touching the original bill."""
    vendor_credit = _get_vendor_credit_for_update(vendor_credit_id=vendor_credit_id, organization=organization)

    if vendor_credit.status == VendorCreditStatus.VOID:
        return vendor_credit
    if vendor_credit.status != VendorCreditStatus.ISSUED:
        raise ApplicationError(
            f"Cannot void a vendor credit in status '{vendor_credit.status}'.",
            code="vendor_credit_invalid_status",
        )

    if vendor_credit.accounting_journal_id:
        reverse_journal(
            journal_id=vendor_credit.accounting_journal_id,
            organization=organization,
            actor=actor,
            posting_date=timezone.now().date(),
            memo=f"Void of vendor credit {vendor_credit.credit_number}",
        )

    for line in vendor_credit.lines.select_related("item").filter(return_stock=True):
        record_stock_movement(
            organization=organization,
            item=line.item,
            warehouse=vendor_credit.warehouse,
            # ADJUSTMENT_IN reverses the ADJUSTMENT_OUT the return created —
            # the IN/OUT flip convention from
            # inventory.services.adjustments.reverse_stock_adjustment.
            movement_type=MovementType.ADJUSTMENT_IN,
            quantity=line.quantity,
            unit_cost=line.unit_cost,
            movement_date=timezone.now().date(),
            source_type="purchases.VendorCredit.void",
            source_id=str(vendor_credit.id),
            notes=f"Void reversal: {line.description}",
            created_by=actor,
        )

    vendor_credit.status = VendorCreditStatus.VOID
    vendor_credit.voided_by = actor
    vendor_credit.voided_at = timezone.now()
    vendor_credit.save(update_fields=["status", "voided_by", "voided_at", "updated_at"])

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.REVERSE,
        object_type="purchases.VendorCredit",
        object_id=vendor_credit.id,
        changes={"credit_number": vendor_credit.credit_number},
    )
    return vendor_credit
