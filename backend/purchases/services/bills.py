"""Bill lifecycle: the authoritative recognition of what we owe a vendor.

BILL INVENTORY LOGIC (the purchase-side mirror of sales' INVOICE INVENTORY,
and the core of this phase's no-double-receipt guarantee):

An inventoried line either

  (a) has `source_goods_receipt_line` set — the stock ALREADY arrived via
      that receipt. Posting does NOT receive it again. Inventory Asset is
      debited at the cost basis THAT RECEIPT recorded, not at the price the
      vendor billed, so the Inventory Asset GL balance and the movement-
      derived valuation can never drift apart. Any difference between the
      two is posted explicitly to `price_variance_account` — a purchase
      price variance, visible, never a silent re-valuation; or

  (b) has none — posting receives the stock itself via
      `record_stock_movement`, using `Bill.warehouse` (required in this case)
      and the bill's own unit price as the cost basis. No variance is
      possible, because the bill IS the cost basis.

A non-inventoried line (service, or a product not tracking inventory) is
charged to `BillLine.expense_account` or the item's `purchase_account`.

Voiding reverses only stock the BILL itself received (as ADJUSTMENT_OUT) —
never stock that genuinely arrived on a Goods Receipt, which remains a
separate physical fact just as a dispatched Delivery Challan does on the
sales side.
"""

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

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
from inventory.models.stock_movement import MovementType, StockMovement
from inventory.services.movements import record_stock_movement
from purchases.accounting_bridge import post_purchase_journal
from purchases.models.bill import Bill, BillLine, BillStatus
from purchases.models.goods_receipt import GoodsReceiptLine, GoodsReceiptStatus
from purchases.models.purchase_order import PurchaseOrder
from purchases.models.vendor import Vendor
from purchases.selectors import get_billed_quantity_for_receipt_line
from purchases.services.line_items import build_line_snapshot, is_inventoried, resolve_expense_account
from purchases.services.vendors import assert_vendor_usable_for_new_transaction
from tax.enums import SupplyNature
from tax.services.computation import apply_tax_components, component_totals, compute_withholding
from tax.services.documents import resolve_document_tax
from tax.services.posting import build_input_tax_lines, build_reverse_charge_lines

BILL_NUMBER_SEQUENCE_KEY = "bill"

MONEY_QUANTUM = Decimal("0.01")
# Matches inventory.StockMovement.unit_cost's decimal_places exactly — a
# per-unit valuation input, not a money amount shown to a user.
COST_QUANTUM = Decimal("0.0001")


def _round_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _round_cost(value: Decimal) -> Decimal:
    return value.quantize(COST_QUANTUM, rounding=ROUND_HALF_UP)


def _validate_account(*, organization, account, expected_type, field_name):
    if account.organization_id != organization.id:
        raise ApplicationError(f"{field_name} must belong to the posting organization.", code="cross_org_reference")
    if account.account_type != expected_type:
        raise ApplicationError(
            f"{field_name} must reference an account of type '{expected_type}'.", code="invalid_account_type"
        )


def _validate_source_goods_receipt_line(*, organization, item, source_line, quantity, bill=None):
    if source_line.organization_id != organization.id:
        raise ApplicationError(
            "Goods receipt line must belong to the posting organization.", code="goods_receipt_line_cross_org"
        )
    if source_line.item_id != item.id:
        raise ApplicationError(
            "Bill line item must match the linked goods receipt line's item.",
            code="goods_receipt_line_item_mismatch",
        )

    # Lock the receipt line before reading how much of it has been billed.
    #
    # Without this the double-BILLING guard below is a read-then-write race:
    # concurrent bills each see "0 already billed", each pass, and each
    # insert — five bills for one delivery, and a vendor paid five times for
    # goods that arrived once. Locking the row serialises every bill claiming
    # the same receipt line, so the second one reads the first one's
    # committed quantity. Same technique as
    # inventory.services.movements.record_stock_movement's negative-stock
    # guard and sales/purchases payment allocation: lock the rows the
    # decision depends on, then decide. `create_bill`/`replace_bill_lines`
    # are both @transaction.atomic, so the lock is held to commit.
    #
    # No `.select_related()` here: FOR UPDATE cannot be applied across the
    # LEFT JOIN a nullable FK produces (see sales/CLAUDE.md).
    locked_line = (
        GoodsReceiptLine.objects.select_for_update()
        .filter(pk=source_line.pk, organization=organization)
        .first()
    )
    if locked_line is None:
        raise ApplicationError(
            "Goods receipt line not found.", code="goods_receipt_line_not_found", status_code=404
        )

    if locked_line.receipt.status != GoodsReceiptStatus.RECEIVED:
        raise ApplicationError(
            "Cannot bill against a goods receipt that has not been received.",
            code="goods_receipt_not_received",
        )
    # The double-BILLING guard (distinct from the double-RECEIPT guard, which
    # is the "do not move stock again" rule at posting time). A receipt line
    # of 10 may be billed 4 then 6, but never 4 then 8.
    already_billed = get_billed_quantity_for_receipt_line(
        goods_receipt_line=locked_line, exclude_bill=bill
    )
    if already_billed + quantity > locked_line.quantity:
        raise ApplicationError(
            f"Billing {quantity} would exceed the received quantity "
            f"({locked_line.quantity}, {already_billed} already billed).",
            code="over_billing",
        )


def _build_bill_line_row(
    *, organization, bill_warehouse, line: dict, line_number: int, bill=None,
    supply_nature: str = SupplyNature.UNSPECIFIED,
) -> dict:
    row = build_line_snapshot(organization=organization, line=line, line_number=line_number)
    row = apply_tax_components(
        row, supply_nature=supply_nature, cess_rate=line.get("cess_rate", Decimal("0"))
    )
    item = row["item"]
    source_goods_receipt_line = line.get("source_goods_receipt_line")
    source_order_line = line.get("source_order_line")
    expense_account = line.get("expense_account")

    if source_goods_receipt_line is not None:
        if not is_inventoried(item):
            raise ApplicationError(
                "Only an inventoried item line can link to a goods receipt line.",
                code="goods_receipt_line_not_inventoried",
            )
        _validate_source_goods_receipt_line(
            organization=organization, item=item, source_line=source_goods_receipt_line,
            quantity=row["quantity"], bill=bill,
        )
    elif is_inventoried(item):
        # No linked receipt — bill posting will receive the stock itself and
        # needs a warehouse to receive it into.
        if bill_warehouse is None:
            raise ApplicationError(
                "A warehouse is required to receive stock directly for an inventoried line "
                "with no linked goods receipt.",
                code="warehouse_required",
            )

    if source_order_line is not None:
        if source_order_line.organization_id != organization.id:
            raise ApplicationError(
                "Purchase order line must belong to the posting organization.",
                code="purchase_order_line_cross_org",
            )
        if source_order_line.item_id != item.id:
            raise ApplicationError(
                "Bill line item must match the linked purchase order line's item.",
                code="purchase_order_line_item_mismatch",
            )

    if expense_account is not None:
        _validate_account(
            organization=organization, account=expense_account, expected_type=AccountType.EXPENSE,
            field_name="expense_account",
        )

    if is_inventoried(item):
        # An inventoried line is capitalised, so it needs an inventory
        # account to capitalise INTO. Checked here at build time rather than
        # at posting so the problem surfaces while the bill is still a draft
        # the user can fix.
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

    row["source_goods_receipt_line"] = source_goods_receipt_line
    row["source_order_line"] = source_order_line
    row["expense_account"] = expense_account
    return row


def _get_bill_for_update(*, bill_id, organization) -> Bill:
    try:
        return Bill.objects.select_for_update().get(id=bill_id, organization=organization)
    except Bill.DoesNotExist:
        raise ApplicationError("Bill not found.", code="bill_not_found", status_code=404)


def _assert_vendor_bill_number_unused(*, organization, vendor, vendor_bill_number, exclude_bill=None):
    """Duplicate-bill defence at the service layer. The database constraint
    `uniq_vendor_bill_number_per_vendor` is the real backstop; this exists so
    the user gets a clear domain error instead of an IntegrityError, the same
    belt-and-braces split used for recurring-run uniqueness."""
    if not vendor_bill_number:
        return
    qs = Bill.objects.filter(organization=organization, vendor=vendor, vendor_bill_number=vendor_bill_number)
    if exclude_bill is not None:
        qs = qs.exclude(pk=exclude_bill.pk)
    if qs.exists():
        raise ApplicationError(
            f"A bill with vendor document number '{vendor_bill_number}' already exists for this vendor.",
            code="duplicate_vendor_bill_number",
        )


@transaction.atomic
def create_bill(
    *,
    organization,
    vendor: Vendor,
    bill_date,
    due_date,
    lines: list[dict],
    payable_account,
    currency=None,
    exchange_rate: Decimal = Decimal("1"),
    vendor_bill_number: str = "",
    reference: str = "",
    warehouse=None,
    tax_recoverable_account=None,
    price_variance_account=None,
    notes: str = "",
    source_purchase_order: PurchaseOrder | None = None,
    place_of_supply=None,
    is_reverse_charge: bool = False,
    withholding_section=None,
    tax_treatment: dict | None = None,
    actor=None,
) -> Bill:
    if vendor.organization_id != organization.id:
        raise ApplicationError("Vendor must belong to the posting organization.", code="vendor_cross_org")
    assert_vendor_usable_for_new_transaction(vendor=vendor)
    _validate_account(
        organization=organization, account=payable_account, expected_type=AccountType.LIABILITY,
        field_name="payable_account",
    )
    if tax_recoverable_account is not None:
        # Input tax is an ASSET (recoverable), the mirror of sales' output
        # tax being a LIABILITY.
        _validate_account(
            organization=organization, account=tax_recoverable_account, expected_type=AccountType.ASSET,
            field_name="tax_recoverable_account",
        )
    if price_variance_account is not None:
        _validate_account(
            organization=organization, account=price_variance_account, expected_type=AccountType.EXPENSE,
            field_name="price_variance_account",
        )
    if warehouse is not None and warehouse.organization_id != organization.id:
        raise ApplicationError("warehouse must belong to the posting organization.", code="warehouse_cross_org")
    if source_purchase_order is not None:
        if source_purchase_order.organization_id != organization.id:
            raise ApplicationError(
                "Purchase order must belong to the posting organization.", code="purchase_order_cross_org"
            )
        if source_purchase_order.vendor_id != vendor.id:
            raise ApplicationError(
                "Purchase order does not belong to this vendor.", code="purchase_order_vendor_mismatch"
            )
    if not lines:
        raise ApplicationError("A bill needs at least one line.", code="bill_no_lines")
    _assert_vendor_bill_number_unused(
        organization=organization, vendor=vendor, vendor_bill_number=vendor_bill_number
    )

    currency = currency or vendor.currency
    assert_base_currency(organization=organization, currency=currency, exchange_rate=exchange_rate)
    tax_treatment = tax_treatment or resolve_document_tax(
        organization=organization, party=vendor, place_of_supply=place_of_supply
    )
    line_rows = [
        _build_bill_line_row(
            organization=organization, bill_warehouse=warehouse, line=line, line_number=index,
            supply_nature=tax_treatment["supply_nature"],
        )
        for index, line in enumerate(lines, start=1)
    ]
    totals = calculate_document_totals(line_rows)
    components = component_totals(line_rows)
    withholding = compute_withholding(
        base_amount=totals["subtotal"] - totals["discount"], section=withholding_section
    )

    bill = Bill.objects.create(
        organization=organization,
        vendor=vendor,
        vendor_bill_number=vendor_bill_number,
        source_purchase_order=source_purchase_order,
        bill_date=bill_date,
        due_date=due_date,
        reference=reference,
        currency=currency,
        exchange_rate=exchange_rate,
        payable_account=payable_account,
        tax_recoverable_account=tax_recoverable_account,
        price_variance_account=price_variance_account,
        warehouse=warehouse,
        subtotal=totals["subtotal"],
        discount_total=totals["discount"],
        tax_total=totals["tax"],
        total=totals["total"],
        withholding_section=withholding_section,
        withholding_amount=withholding,
        notes=notes,
        created_by=actor,
        is_reverse_charge=is_reverse_charge,
        **components,
        **tax_treatment,
    )
    for row in line_rows:
        BillLine.objects.create(organization=organization, bill=bill, **row)

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="purchases.Bill",
        object_id=bill.id,
        changes={"total": str(totals["total"]), "vendor_bill_number": vendor_bill_number},
    )
    return bill


@transaction.atomic
def create_bill_from_goods_receipt(
    *, receipt, bill_date, due_date, payable_account, tax_recoverable_account=None,
    price_variance_account=None, vendor_bill_number: str = "", tax_rate_by_line: dict | None = None, actor=None,
) -> Bill:
    """Builds a DRAFT Bill from a RECEIVED goods receipt, linking every line
    back to the receipt line it bills so posting does not receive the stock a
    second time.

    Prices come from the linked PO line where there is one, else from the
    receipt's own recorded cost — the bill is then a faithful starting point
    the user corrects against the vendor's actual paperwork. Tax is NOT
    inferred: a receipt records no tax, and guessing a rate here would put an
    invented number on an authoritative document (root CLAUDE.md), so the
    caller passes `tax_rate_by_line` keyed by receipt line id or accepts zero.
    """
    if receipt.status != GoodsReceiptStatus.RECEIVED:
        raise ApplicationError(
            "Only a received goods receipt can be converted to a bill.", code="goods_receipt_not_received"
        )
    tax_rate_by_line = tax_rate_by_line or {}

    lines = []
    for receipt_line in receipt.lines.select_related("item").order_by("line_number"):
        remaining = receipt_line.quantity - get_billed_quantity_for_receipt_line(goods_receipt_line=receipt_line)
        if remaining <= 0:
            continue
        source_order_line = receipt_line.source_order_line
        unit_price = (
            source_order_line.unit_price if source_order_line is not None
            else _round_money(receipt_line.unit_cost)
        )
        lines.append({
            "item": receipt_line.item,
            "description": receipt_line.description or receipt_line.item.name,
            "quantity": remaining,
            "unit_price": unit_price,
            "tax_rate": tax_rate_by_line.get(receipt_line.id, Decimal("0")),
            "source_goods_receipt_line": receipt_line,
            "source_order_line": source_order_line,
        })

    if not lines:
        raise ApplicationError(
            "Every line on this goods receipt has already been billed.", code="goods_receipt_fully_billed"
        )

    return create_bill(
        organization=receipt.organization,
        vendor=receipt.vendor,
        bill_date=bill_date,
        due_date=due_date,
        lines=lines,
        payable_account=payable_account,
        tax_recoverable_account=tax_recoverable_account,
        price_variance_account=price_variance_account,
        vendor_bill_number=vendor_bill_number,
        source_purchase_order=receipt.source_purchase_order,
        actor=actor,
    )


@transaction.atomic
def replace_bill_lines(*, bill: Bill, lines: list[dict], actor=None) -> Bill:
    """Replaces all lines on a DRAFT bill and recomputes header totals.
    Raises if the bill is not a draft — posted bills are immutable (see
    Bill._MUTABLE_AFTER_DRAFT_FIELDS)."""
    if bill.status != BillStatus.DRAFT:
        raise ApplicationError("Only draft bills can be modified.", code="bill_not_draft")
    if not lines:
        raise ApplicationError("A bill needs at least one line.", code="bill_no_lines")

    line_rows = [
        _build_bill_line_row(
            organization=bill.organization, bill_warehouse=bill.warehouse, line=line, line_number=index,
            bill=bill, supply_nature=bill.supply_nature,
        )
        for index, line in enumerate(lines, start=1)
    ]
    totals = calculate_document_totals(line_rows)
    components = component_totals(line_rows)

    bill.lines.all().delete()
    for row in line_rows:
        BillLine.objects.create(organization=bill.organization, bill=bill, **row)

    bill.subtotal = totals["subtotal"]
    bill.discount_total = totals["discount"]
    bill.tax_total = totals["tax"]
    bill.total = totals["total"]
    for field, value in components.items():
        setattr(bill, field, value)
    bill.withholding_amount = compute_withholding(
        base_amount=totals["subtotal"] - totals["discount"], section=bill.withholding_section
    )
    bill.save(
        update_fields=[
            "subtotal", "discount_total", "tax_total", "total",
            *components.keys(), "withholding_amount", "updated_at",
        ]
    )

    record_audit(
        organization_id=bill.organization_id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="purchases.Bill",
        object_id=bill.id,
        changes={"total": str(totals["total"])},
    )
    return bill


@transaction.atomic
def post_bill(*, bill_id, organization, actor=None) -> Bill:
    """The single authoritative path from DRAFT to an immutable financial
    record. Idempotent like accounting.services.posting.post_journal: posting
    an already-OPEN bill is a locked no-op.

    Posts exactly one accounting journal covering AP / Inventory-or-Expense /
    recoverable tax / price variance, and receives stock ONLY for inventoried
    lines with no linked Goods Receipt line — see this module's docstring.
    """
    bill = _get_bill_for_update(bill_id=bill_id, organization=organization)

    if bill.status == BillStatus.OPEN:
        return bill
    if bill.status != BillStatus.DRAFT:
        raise ApplicationError(f"Cannot post a bill in status '{bill.status}'.", code="bill_invalid_status")

    # NOT `.select_related("source_goods_receipt_line")` — that FK is
    # nullable, and PostgreSQL rejects SELECT FOR UPDATE across a LEFT JOIN
    # ("FOR UPDATE cannot be applied to the nullable side of an outer join").
    # `item` is a required FK so it joins as INNER and is safe. The receipt
    # line is therefore loaded lazily below, on the minority of lines that
    # have one.
    lines = list(
        BillLine.objects.select_for_update().select_related("item").filter(bill=bill).order_by("line_number")
    )
    if not lines:
        raise ApplicationError("Cannot post a bill with no lines.", code="bill_no_lines")
    # The tax-account requirement is enforced by build_input_tax_lines below,
    # which only demands a document-level account when the components cannot
    # carry the tax themselves.

    debit_by_account = defaultdict(lambda: Decimal("0"))
    price_variance = Decimal("0")

    for line in lines:
        item = line.item
        # The taxable (post-discount, pre-tax) amount is what gets
        # capitalised or expensed; the tax component goes to its own
        # recoverable account, never into the cost of the goods.
        net_amount = line.taxable_amount

        if not is_inventoried(item):
            account = resolve_expense_account(item=item, expense_account=line.expense_account)
            debit_by_account[account.id] += net_amount
            continue

        if line.source_goods_receipt_line_id:
            # Stock already physically received by the Goods Receipt — do NOT
            # receive it again. Capitalise at the cost basis THAT receipt
            # recorded, so the Inventory Asset GL balance matches the
            # movement-derived valuation exactly. The gap between that and
            # what the vendor actually billed is a purchase price variance.
            receipt_line = line.source_goods_receipt_line
            receipted_amount = _round_money(line.quantity * receipt_line.unit_cost)
            debit_by_account[item.inventory_account_id] += receipted_amount
            price_variance += net_amount - receipted_amount
        else:
            # No linked receipt — this bill IS the receipt. It receives the
            # stock itself, at its own price, so no variance is possible.
            #
            # The GL is debited with `net_amount` (the authoritative bill
            # figure, which must tie to the AP credit for the journal to
            # balance) while the movement records a 4dp per-unit cost derived
            # from it. Those two can differ by a sub-cent rounding residue on
            # awkward quantities — inherent to representing any cost per unit,
            # and the same 4dp precision every other inbound movement in the
            # system already uses (opening stock, adjustments).
            unit_cost = _round_cost(net_amount / line.quantity) if line.quantity else Decimal("0")
            record_stock_movement(
                organization=organization,
                item=item,
                warehouse=bill.warehouse,
                movement_type=MovementType.RECEIPT,
                quantity=line.quantity,
                unit_cost=unit_cost,
                movement_date=bill.bill_date,
                source_type="purchases.Bill",
                source_id=str(bill.id),
                notes=line.description,
                created_by=actor,
            )
            debit_by_account[item.inventory_account_id] += net_amount

    if price_variance != 0 and bill.price_variance_account_id is None:
        raise ApplicationError(
            "price_variance_account is required: this bill's price differs from the cost recorded "
            "on the goods receipt it bills.",
            code="price_variance_account_required",
        )

    journal_lines = []
    for account_id, amount in debit_by_account.items():
        if amount != 0:
            journal_lines.append({"account_id": account_id, "debit": amount})
    journal_lines.extend(
        build_input_tax_lines(
            organization=organization,
            document=bill,
            fallback_account_id=bill.tax_recoverable_account_id,
        )
    )
    if bill.is_reverse_charge and bill.tax_total > 0:
        # Under IGST Act s.5(3)/(4) we owe the tax the supplier would normally
        # have collected. The input credit above is only half the entry: without
        # this liability leg the bill would claim a credit for tax nobody ever
        # paid, and the journal would not balance.
        rcm_lines = build_reverse_charge_lines(organization=organization, document=bill)
        if not rcm_lines:
            raise ApplicationError(
                "A reverse-charge bill needs a tax account mapped to the RCM_PAYABLE direction "
                "before it can post.",
                code="rcm_account_required",
            )
        journal_lines.extend(rcm_lines)
    if price_variance > 0:
        # Billed above the receipted cost — an unfavourable variance, an
        # extra expense.
        journal_lines.append({"account_id": bill.price_variance_account_id, "debit": price_variance})
    elif price_variance < 0:
        # Billed below the receipted cost — favourable, so the variance
        # account is credited. A CREDIT on an expense account, which is
        # correct and deliberate: it is a cost reduction, not income.
        journal_lines.append({"account_id": bill.price_variance_account_id, "credit": -price_variance})
    # What the vendor is actually owed, which is NOT the bill total:
    #
    #  - TDS is withheld FROM them, so the payable drops by what we keep back
    #    and remit on their behalf;
    #  - under REVERSE CHARGE the supplier never charged the tax in the first
    #    place (that is what makes it reverse charge), so the tax sitting in
    #    `bill.total` is owed to the government via the RCM leg above, not to
    #    the vendor. Leaving it in the payable would both overstate what they
    #    are owed and unbalance the journal against that leg.
    payable_amount = bill.total - bill.withholding_amount
    if bill.is_reverse_charge:
        payable_amount -= bill.tax_total
    journal_lines.append({"account_id": bill.payable_account_id, "credit": payable_amount})
    if bill.withholding_amount > 0:
        journal_lines.append(
            {"account_id": bill.withholding_section.account_id, "credit": bill.withholding_amount}
        )

    bill_number = allocate_sequence_number(
        organization_id=organization.id, key=BILL_NUMBER_SEQUENCE_KEY, prefix="BILL-"
    )

    journal = post_purchase_journal(
        organization=organization,
        posting_date=bill.bill_date,
        currency=bill.currency,
        lines=journal_lines,
        memo=f"Bill {bill_number}",
        source_type="purchases.Bill",
        source_id=str(bill.id),
        actor=actor,
    )

    bill.bill_number = bill_number
    bill.status = BillStatus.OPEN
    bill.posted_by = actor
    bill.posted_at = timezone.now()
    bill.accounting_journal = journal
    bill.save(update_fields=["bill_number", "status", "posted_by", "posted_at", "accounting_journal", "updated_at"])

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.POST,
        object_type="purchases.Bill",
        object_id=bill.id,
        changes={
            "bill_number": bill_number, "total": str(bill.total), "journal_id": str(journal.id),
            "price_variance": str(price_variance),
        },
    )
    return bill


@transaction.atomic
def void_bill(*, bill_id, organization, actor=None, reason: str = "") -> Bill:
    """Reverses a bill's accounting AND any stock the bill itself received
    (never stock that genuinely arrived on a Goods Receipt — that remains a
    separate physical fact).

    Accepts OPEN and PARTIALLY_PAID, then refuses on the specific reason.
    PARTIALLY_PAID is admitted deliberately rather than rejected on status
    alone, because it is reached by two very different routes: cash actually
    paid out, or a vendor credit netted against the bill. Rejecting the
    status wholesale would answer both with "cannot void a bill in status
    'partially_paid'", which tells the user nothing about which fact is in
    their way. Admitting it lets the two guards below name the real
    obstacle, and it is what makes the `bill_has_payments` check reachable
    at all rather than decorative.

    Either way the correction is a reversal of the blocking document first,
    never a silent overwrite of history.
    """
    bill = _get_bill_for_update(bill_id=bill_id, organization=organization)

    if bill.status == BillStatus.VOID:
        return bill
    if bill.status not in (BillStatus.OPEN, BillStatus.PARTIALLY_PAID):
        raise ApplicationError(f"Cannot void a bill in status '{bill.status}'.", code="bill_invalid_status")

    from purchases.selectors import get_bill_amount_credited, get_bill_amount_paid

    if get_bill_amount_paid(bill=bill) > 0:
        raise ApplicationError("Cannot void a bill that has payments allocated.", code="bill_has_payments")
    if get_bill_amount_credited(bill=bill) > 0:
        raise ApplicationError(
            "Cannot void a bill that has vendor credits applied — void the credit first.",
            code="bill_has_credits",
        )

    if bill.accounting_journal_id:
        reverse_journal(
            journal_id=bill.accounting_journal_id,
            organization=organization,
            actor=actor,
            posting_date=timezone.now().date(),
            memo=f"Void of bill {bill.bill_number}",
        )

    direct_receipt_lines = bill.lines.select_related("item").filter(source_goods_receipt_line__isnull=True)
    for line in direct_receipt_lines:
        item = line.item
        if not is_inventoried(item):
            continue
        original_movement = (
            StockMovement.objects.filter(
                source_type="purchases.Bill", source_id=str(bill.id), item=item, warehouse=bill.warehouse
            )
            .order_by("-sequence")
            .first()
        )
        record_stock_movement(
            organization=organization,
            item=item,
            warehouse=bill.warehouse,
            # The exact counter-pairing of the RECEIPT this bill made, using
            # the IN/OUT flip convention established by
            # inventory.services.adjustments.reverse_stock_adjustment.
            movement_type=MovementType.ADJUSTMENT_OUT,
            quantity=line.quantity,
            unit_cost=original_movement.unit_cost if original_movement else None,
            movement_date=timezone.now().date(),
            source_type="purchases.Bill.void",
            source_id=str(bill.id),
            notes=f"Void reversal: {line.description}",
            created_by=actor,
        )

    bill.status = BillStatus.VOID
    bill.voided_by = actor
    bill.voided_at = timezone.now()
    bill.void_reason = reason
    bill.save(update_fields=["status", "voided_by", "voided_at", "void_reason", "updated_at"])

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.REVERSE,
        object_type="purchases.Bill",
        object_id=bill.id,
        changes={"reason": reason},
    )
    return bill


def refresh_bill_payment_status(*, bill: Bill, actor=None) -> Bill:
    """Called only by services/payments.py (after a payment allocation) and
    services/vendor_credits.py (after a credit is issued against this bill) —
    never by a user-facing endpoint. Derives OPEN -> PARTIALLY_PAID -> PAID
    from the combined effect of payments AND vendor credits (see
    selectors.py::get_bill_amount_due) — either alone can fully settle a
    bill, so both must be considered together. A no-op for any other status.
    """
    if bill.status not in (BillStatus.OPEN, BillStatus.PARTIALLY_PAID):
        return bill

    from purchases.selectors import get_bill_amount_due

    due = get_bill_amount_due(bill=bill)
    if due <= 0:
        new_status = BillStatus.PAID
    elif due < bill.total:
        new_status = BillStatus.PARTIALLY_PAID
    else:
        new_status = bill.status

    if new_status == bill.status:
        return bill

    from_status = bill.status
    bill.status = new_status
    bill.save(update_fields=["status", "updated_at"])
    record_audit(
        organization_id=bill.organization_id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="purchases.Bill",
        object_id=bill.id,
        changes={"status": {"from": from_status, "to": new_status}, "reason": "payment"},
    )
    return bill
