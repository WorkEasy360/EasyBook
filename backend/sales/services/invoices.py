from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from accounting.models.account import AccountType
from accounting.services.posting import reverse_journal
from accounts.services import allocate_sequence_number
from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError
from core.money import calculate_document_totals
from inventory.models.stock_movement import MovementType, StockMovement
from inventory.selectors import get_weighted_average_cost
from inventory.services.movements import record_stock_movement
from items.models.item import ItemType
from sales.accounting_bridge import post_sales_journal
from sales.models.customer import Customer
from sales.models.delivery import DeliveryChallanLine
from sales.models.invoice import Invoice, InvoiceLine, InvoiceStatus
from sales.models.quote import Quote, QuoteStatus
from sales.services.customers import assert_customer_usable_for_new_transaction
from sales.services.line_items import build_line_snapshot
from tax.enums import SupplyNature
from tax.services.computation import apply_tax_components, component_totals, compute_withholding
from tax.services.documents import carry_forward_tax, resolve_document_tax
from tax.services.posting import build_output_tax_lines

INVOICE_NUMBER_SEQUENCE_KEY = "invoice"


def _validate_account(*, organization, account, expected_type, field_name):
    if account.organization_id != organization.id:
        raise ApplicationError(f"{field_name} must belong to the posting organization.", code="cross_org_reference")
    if account.account_type != expected_type:
        raise ApplicationError(
            f"{field_name} must reference an account of type '{expected_type}'.", code="invalid_account_type"
        )


def _build_invoice_line_row(
    *, organization, invoice_warehouse, line: dict, line_number: int,
    supply_nature: str = SupplyNature.UNSPECIFIED,
) -> dict:
    row = build_line_snapshot(organization=organization, line=line, line_number=line_number)
    # The GST split rides on top of the shared snapshot rather than inside it:
    # only documents that post carry component columns (see
    # tax.services.computation.apply_tax_components).
    row = apply_tax_components(
        row, supply_nature=supply_nature, cess_rate=line.get("cess_rate", Decimal("0"))
    )
    item = row["item"]
    source_delivery_challan_line = line.get("source_delivery_challan_line")

    if source_delivery_challan_line is not None:
        if source_delivery_challan_line.organization_id != organization.id:
            raise ApplicationError(
                "Delivery challan line must belong to the posting organization.", code="delivery_line_cross_org"
            )
        if source_delivery_challan_line.item_id != item.id:
            raise ApplicationError(
                "Invoice line item must match the linked delivery challan line's item.",
                code="delivery_line_item_mismatch",
            )
        if row["quantity"] > source_delivery_challan_line.quantity:
            raise ApplicationError(
                f"Invoiced quantity ({row['quantity']}) cannot exceed the delivered quantity "
                f"({source_delivery_challan_line.quantity}).",
                code="invoice_quantity_exceeds_delivery",
            )
    elif item.item_type == ItemType.PRODUCT and item.track_inventory:
        # No linked delivery — invoice posting will issue stock itself and
        # needs a warehouse to issue it from (see services/invoices.py::post_invoice).
        if invoice_warehouse is None:
            raise ApplicationError(
                "A warehouse is required to issue stock directly for a product line with no linked delivery.",
                code="warehouse_required",
            )
    if item.sales_account_id is None:
        raise ApplicationError(f"Item '{item.name}' has no sales_account configured.", code="item_missing_sales_account")

    row["source_delivery_challan_line"] = source_delivery_challan_line
    return row


def _get_invoice_for_update(*, invoice_id, organization) -> Invoice:
    try:
        return Invoice.objects.select_for_update().get(id=invoice_id, organization=organization)
    except Invoice.DoesNotExist:
        raise ApplicationError("Invoice not found.", code="invoice_not_found", status_code=404)


@transaction.atomic
def create_invoice(
    *,
    organization,
    customer: Customer,
    invoice_date,
    due_date,
    lines: list[dict],
    receivable_account,
    currency=None,
    exchange_rate: Decimal = Decimal("1"),
    reference: str = "",
    warehouse=None,
    tax_payable_account=None,
    notes: str = "",
    terms: str = "",
    source_quote: Quote | None = None,
    place_of_supply=None,
    is_reverse_charge: bool = False,
    withholding_section=None,
    tax_treatment: dict | None = None,
    actor=None,
) -> Invoice:
    if customer.organization_id != organization.id:
        raise ApplicationError("Customer must belong to the posting organization.", code="customer_cross_org")
    assert_customer_usable_for_new_transaction(customer=customer)
    _validate_account(
        organization=organization, account=receivable_account, expected_type=AccountType.ASSET,
        field_name="receivable_account",
    )
    if tax_payable_account is not None:
        _validate_account(
            organization=organization, account=tax_payable_account, expected_type=AccountType.LIABILITY,
            field_name="tax_payable_account",
        )
    if warehouse is not None and warehouse.organization_id != organization.id:
        raise ApplicationError("warehouse must belong to the posting organization.", code="warehouse_cross_org")
    if not lines:
        raise ApplicationError("An invoice needs at least one line.", code="invoice_no_lines")

    currency = currency or customer.currency
    tax_treatment = tax_treatment or resolve_document_tax(
        organization=organization, party=customer, place_of_supply=place_of_supply
    )
    line_rows = [
        _build_invoice_line_row(
            organization=organization, invoice_warehouse=warehouse, line=line, line_number=index,
            supply_nature=tax_treatment["supply_nature"],
        )
        for index, line in enumerate(lines, start=1)
    ]
    totals = calculate_document_totals(line_rows)
    components = component_totals(line_rows)
    withholding = compute_withholding(
        base_amount=totals["subtotal"] - totals["discount"], section=withholding_section
    )

    invoice = Invoice.objects.create(
        organization=organization,
        customer=customer,
        source_quote=source_quote,
        invoice_date=invoice_date,
        due_date=due_date,
        reference=reference,
        currency=currency,
        exchange_rate=exchange_rate,
        receivable_account=receivable_account,
        tax_payable_account=tax_payable_account,
        warehouse=warehouse,
        subtotal=totals["subtotal"],
        discount_total=totals["discount"],
        tax_total=totals["tax"],
        total=totals["total"],
        withholding_section=withholding_section,
        withholding_amount=withholding,
        notes=notes,
        terms=terms,
        created_by=actor,
        is_reverse_charge=is_reverse_charge,
        **components,
        **tax_treatment,
    )
    for row in line_rows:
        InvoiceLine.objects.create(organization=organization, invoice=invoice, **row)

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="sales.Invoice",
        object_id=invoice.id,
        changes={"total": str(totals["total"])},
    )
    return invoice


@transaction.atomic
def convert_quote_to_invoice(
    *, quote: Quote, invoice_date, due_date, receivable_account, warehouse=None, tax_payable_account=None, actor=None
) -> Invoice:
    """Builds a DRAFT Invoice from an ACCEPTED quote's lines. A quote
    converts to at most one invoice — see Invoice.source_quote."""
    if quote.status != QuoteStatus.ACCEPTED:
        raise ApplicationError(
            f"Cannot convert a quote in status '{quote.status}' to an invoice.", code="quote_not_acceptable"
        )
    if hasattr(quote, "invoice"):
        raise ApplicationError("This quote has already been converted to an invoice.", code="quote_already_converted")

    lines = [
        {
            "item": line.item,
            "description": line.description,
            "hsn_sac_snapshot": line.hsn_sac_snapshot,
            "tax_label": line.tax_label,
            "quantity": line.quantity,
            "unit_price": line.unit_price,
            "discount_percent": line.discount_percent,
            "tax_rate": line.tax_rate,
        }
        for line in quote.lines.all()
    ]

    carried = carry_forward_tax(quote)
    carried.pop("is_reverse_charge", None)

    return create_invoice(
        organization=quote.organization,
        customer=quote.customer,
        tax_treatment=carried,
        invoice_date=invoice_date,
        due_date=due_date,
        lines=lines,
        receivable_account=receivable_account,
        currency=quote.currency,
        exchange_rate=quote.exchange_rate,
        warehouse=warehouse,
        tax_payable_account=tax_payable_account,
        notes=quote.notes,
        terms=quote.terms,
        source_quote=quote,
        actor=actor,
    )


@transaction.atomic
def replace_invoice_lines(*, invoice: Invoice, lines: list[dict], actor=None) -> Invoice:
    """Replaces all lines on a DRAFT invoice and recomputes header totals.
    Raises if the invoice is not a draft — posted invoices are immutable
    (see Invoice._MUTABLE_AFTER_DRAFT_FIELDS)."""
    if invoice.status != InvoiceStatus.DRAFT:
        raise ApplicationError("Only draft invoices can be modified.", code="invoice_not_draft")
    if not lines:
        raise ApplicationError("An invoice needs at least one line.", code="invoice_no_lines")

    line_rows = [
        _build_invoice_line_row(
            organization=invoice.organization, invoice_warehouse=invoice.warehouse, line=line,
            line_number=index, supply_nature=invoice.supply_nature,
        )
        for index, line in enumerate(lines, start=1)
    ]
    totals = calculate_document_totals(line_rows)
    components = component_totals(line_rows)

    invoice.lines.all().delete()
    for row in line_rows:
        InvoiceLine.objects.create(organization=invoice.organization, invoice=invoice, **row)

    invoice.subtotal = totals["subtotal"]
    invoice.discount_total = totals["discount"]
    invoice.tax_total = totals["tax"]
    invoice.total = totals["total"]
    for field, value in components.items():
        setattr(invoice, field, value)
    invoice.withholding_amount = compute_withholding(
        base_amount=totals["subtotal"] - totals["discount"], section=invoice.withholding_section
    )
    invoice.save(
        update_fields=[
            "subtotal", "discount_total", "tax_total", "total",
            *components.keys(), "withholding_amount", "updated_at",
        ]
    )

    record_audit(
        organization_id=invoice.organization_id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="sales.Invoice",
        object_id=invoice.id,
        changes={"total": str(totals["total"])},
    )
    return invoice


@transaction.atomic
def post_invoice(*, invoice_id, organization, actor=None) -> Invoice:
    """The single authoritative path from DRAFT to an authoritative,
    immutable financial record. Idempotent like
    accounting.services.posting.post_journal: posting an already-SENT
    invoice is a locked no-op. Posts exactly one accounting journal covering
    AR/Revenue/Tax and (where applicable) COGS/Inventory Asset, and issues
    stock only for lines with no linked Delivery Challan line — see
    sales/CLAUDE.md and services/deliveries.py.
    """
    invoice = _get_invoice_for_update(invoice_id=invoice_id, organization=organization)

    if invoice.status == InvoiceStatus.SENT:
        return invoice
    if invoice.status != InvoiceStatus.DRAFT:
        raise ApplicationError(f"Cannot post an invoice in status '{invoice.status}'.", code="invoice_invalid_status")

    lines = list(
        InvoiceLine.objects.select_for_update().select_related("item").filter(invoice=invoice).order_by("line_number")
    )
    if not lines:
        raise ApplicationError("Cannot post an invoice with no lines.", code="invoice_no_lines")
    # The tax-account requirement is checked by build_output_tax_lines below:
    # an organization that has mapped every component does not need a
    # document-level account at all, so demanding one here would refuse a
    # correctly-configured invoice.

    revenue_by_account = defaultdict(lambda: Decimal("0"))
    cogs_by_pair = defaultdict(lambda: Decimal("0"))

    for line in lines:
        item = line.item
        revenue_by_account[item.sales_account_id] += line.line_base - line.discount_amount

        if item.item_type != ItemType.PRODUCT or not item.track_inventory:
            continue

        if line.source_delivery_challan_line_id:
            # Stock already physically issued by the Delivery Challan — do
            # NOT issue again. Replay the average cost as of that dispatch
            # date to recover the exact historical cost basis (outbound
            # movements never change average_cost, so this equals what the
            # original ISSUE movement was valued at).
            challan_line = DeliveryChallanLine.objects.select_related("challan").get(
                pk=line.source_delivery_challan_line_id
            )
            _, unit_cost = get_weighted_average_cost(
                item=item, warehouse=challan_line.challan.warehouse, as_of=challan_line.challan.challan_date
            )
        else:
            _, unit_cost = get_weighted_average_cost(item=item, warehouse=invoice.warehouse)
            record_stock_movement(
                organization=organization,
                item=item,
                warehouse=invoice.warehouse,
                movement_type=MovementType.ISSUE,
                quantity=line.quantity,
                unit_cost=unit_cost,
                movement_date=invoice.invoice_date,
                source_type="sales.Invoice",
                source_id=str(invoice.id),
                notes=line.description,
                created_by=actor,
            )

        if item.inventory_account_id and item.cogs_account_id:
            cogs_amount = (line.quantity * unit_cost).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            cogs_by_pair[(item.cogs_account_id, item.inventory_account_id)] += cogs_amount

    # TCS is collected FROM the customer on top of the invoice, so it raises
    # what they owe: the receivable carries it, and the collected amount sits
    # as a liability until it is remitted.
    receivable_amount = invoice.total + invoice.withholding_amount
    journal_lines = [{"account_id": invoice.receivable_account_id, "debit": receivable_amount}]
    for account_id, amount in revenue_by_account.items():
        if amount != 0:
            journal_lines.append({"account_id": account_id, "credit": amount})
    journal_lines.extend(
        build_output_tax_lines(
            organization=organization,
            document=invoice,
            fallback_account_id=invoice.tax_payable_account_id,
        )
    )
    if invoice.withholding_amount > 0:
        journal_lines.append(
            {"account_id": invoice.withholding_section.account_id, "credit": invoice.withholding_amount}
        )
    for (cogs_account_id, inventory_account_id), amount in cogs_by_pair.items():
        if amount == 0:
            continue
        journal_lines.append({"account_id": cogs_account_id, "debit": amount})
        journal_lines.append({"account_id": inventory_account_id, "credit": amount})

    invoice_number = allocate_sequence_number(
        organization_id=organization.id, key=INVOICE_NUMBER_SEQUENCE_KEY, prefix="INV-"
    )

    journal = post_sales_journal(
        organization=organization,
        posting_date=invoice.invoice_date,
        currency=invoice.currency,
        lines=journal_lines,
        memo=f"Invoice {invoice_number}",
        source_type="sales.Invoice",
        source_id=str(invoice.id),
        actor=actor,
    )

    invoice.invoice_number = invoice_number
    invoice.status = InvoiceStatus.SENT
    invoice.posted_by = actor
    invoice.posted_at = timezone.now()
    invoice.accounting_journal = journal
    invoice.save(
        update_fields=["invoice_number", "status", "posted_by", "posted_at", "accounting_journal", "updated_at"]
    )

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.POST,
        object_type="sales.Invoice",
        object_id=invoice.id,
        changes={"invoice_number": invoice_number, "total": str(invoice.total), "journal_id": str(journal.id)},
    )
    return invoice


@transaction.atomic
def void_invoice(*, invoice_id, organization, actor=None, reason: str = "") -> Invoice:
    """Reverses a SENT invoice's accounting AND any stock it issued directly
    (never touching stock genuinely issued by a linked Delivery Challan —
    that remains a separate physical fact). Refuses if any payment has been
    allocated (root CLAUDE.md: correction sequence, not a silent overwrite —
    see PAYMENTS/CREDIT NOTES in a later slice for that workflow)."""
    invoice = _get_invoice_for_update(invoice_id=invoice_id, organization=organization)

    if invoice.status == InvoiceStatus.VOID:
        return invoice
    if invoice.status != InvoiceStatus.SENT:
        raise ApplicationError(f"Cannot void an invoice in status '{invoice.status}'.", code="invoice_invalid_status")

    from sales.selectors import get_invoice_amount_paid

    if get_invoice_amount_paid(invoice=invoice) > 0:
        raise ApplicationError(
            "Cannot void an invoice that has payments allocated.", code="invoice_has_payments"
        )

    if invoice.accounting_journal_id:
        reverse_journal(
            journal_id=invoice.accounting_journal_id,
            organization=organization,
            actor=actor,
            posting_date=timezone.now().date(),
            memo=f"Void of invoice {invoice.invoice_number}",
        )

    direct_issue_lines = invoice.lines.select_related("item").filter(source_delivery_challan_line__isnull=True)
    for line in direct_issue_lines:
        item = line.item
        if item.item_type != ItemType.PRODUCT or not item.track_inventory:
            continue
        original_movement = (
            StockMovement.objects.filter(
                source_type="sales.Invoice", source_id=str(invoice.id), item=item, warehouse=invoice.warehouse
            )
            .order_by("-sequence")
            .first()
        )
        record_stock_movement(
            organization=organization,
            item=item,
            warehouse=invoice.warehouse,
            movement_type=MovementType.ADJUSTMENT_IN,
            quantity=line.quantity,
            unit_cost=original_movement.unit_cost if original_movement else None,
            movement_date=timezone.now().date(),
            source_type="sales.Invoice.void",
            source_id=str(invoice.id),
            notes=f"Void reversal: {line.description}",
            created_by=actor,
        )

    invoice.status = InvoiceStatus.VOID
    invoice.voided_by = actor
    invoice.voided_at = timezone.now()
    invoice.void_reason = reason
    invoice.save(update_fields=["status", "voided_by", "voided_at", "void_reason", "updated_at"])

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.REVERSE,
        object_type="sales.Invoice",
        object_id=invoice.id,
        changes={"reason": reason},
    )
    return invoice


def refresh_invoice_payment_status(*, invoice: Invoice, actor=None) -> Invoice:
    """Called only by services/payments.py (after a payment allocation) and
    services/credit_notes.py (after a credit note is issued against this
    invoice) — never by a user-facing endpoint. Derives SENT ->
    PARTIALLY_PAID -> PAID from the combined effect of payments AND credit
    notes (see selectors.py::get_invoice_amount_due) — either one alone can
    fully settle an invoice, so both must be considered together. A no-op
    for any other status."""
    if invoice.status not in (InvoiceStatus.SENT, InvoiceStatus.PARTIALLY_PAID):
        return invoice

    from sales.selectors import get_invoice_amount_due

    due = get_invoice_amount_due(invoice=invoice)
    if due <= 0:
        new_status = InvoiceStatus.PAID
    elif due < invoice.total:
        new_status = InvoiceStatus.PARTIALLY_PAID
    else:
        new_status = invoice.status

    if new_status == invoice.status:
        return invoice

    from_status = invoice.status
    invoice.status = new_status
    invoice.save(update_fields=["status", "updated_at"])
    record_audit(
        organization_id=invoice.organization_id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="sales.Invoice",
        object_id=invoice.id,
        changes={"status": {"from": from_status, "to": new_status}, "reason": "payment"},
    )
    return invoice
