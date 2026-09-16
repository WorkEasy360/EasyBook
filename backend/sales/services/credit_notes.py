from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from accounting.models.account import AccountType
from accounting.services.posting import reverse_journal
from accounts.services import allocate_sequence_number
from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError
from core.money import calculate_document_totals
from inventory.models.stock_movement import MovementType
from inventory.services.movements import record_stock_movement
from items.models.item import ItemType
from sales.accounting_bridge import post_sales_journal
from sales.models.credit_note import CreditNote, CreditNoteLine, CreditNoteStatus
from sales.models.customer import Customer
from sales.models.invoice import Invoice
from sales.selectors import get_invoice_amount_due
from sales.services.customers import assert_customer_usable_for_new_transaction
from sales.services.line_items import build_line_snapshot
from tax.enums import SupplyNature, TaxDirection
from tax.services.computation import apply_tax_components, component_totals
from tax.services.documents import carry_forward_tax, resolve_document_tax
from tax.services.posting import build_tax_journal_lines

CREDIT_NOTE_NUMBER_SEQUENCE_KEY = "credit_note"


def _validate_account(*, organization, account, expected_type, field_name):
    if account.organization_id != organization.id:
        raise ApplicationError(f"{field_name} must belong to the posting organization.", code="cross_org_reference")
    if account.account_type != expected_type:
        raise ApplicationError(
            f"{field_name} must reference an account of type '{expected_type}'.", code="invalid_account_type"
        )


def _build_credit_note_line_row(
    *, organization, credit_note_warehouse, line: dict, line_number: int,
    supply_nature: str = SupplyNature.UNSPECIFIED,
) -> dict:
    row = build_line_snapshot(organization=organization, line=line, line_number=line_number)
    row = apply_tax_components(
        row, supply_nature=supply_nature, cess_rate=line.get("cess_rate", Decimal("0"))
    )
    item = row["item"]
    source_invoice_line = line.get("source_invoice_line")
    restock = line.get("restock", False)
    unit_cost = line.get("unit_cost")

    if source_invoice_line is not None:
        if source_invoice_line.organization_id != organization.id:
            raise ApplicationError(
                "Invoice line must belong to the posting organization.", code="invoice_line_cross_org"
            )
        if source_invoice_line.item_id != item.id:
            raise ApplicationError(
                "Credit note line item must match the linked invoice line's item.", code="invoice_line_item_mismatch"
            )
        if row["quantity"] > source_invoice_line.quantity:
            raise ApplicationError(
                f"Credited quantity ({row['quantity']}) cannot exceed the invoiced quantity "
                f"({source_invoice_line.quantity}).",
                code="credit_quantity_exceeds_invoice",
            )

    if restock:
        if item.item_type == ItemType.SERVICE:
            # Root CLAUDE.md: never automatically restock service items —
            # this is an explicit per-line flag, so reject it outright
            # rather than silently ignoring it.
            raise ApplicationError("Service items cannot be restocked.", code="service_cannot_restock")
        if not item.track_inventory:
            raise ApplicationError("Item does not track inventory and cannot be restocked.", code="item_not_tracked")
        if unit_cost is None:
            raise ApplicationError("unit_cost is required for a restocked line.", code="restock_cost_required")
        if credit_note_warehouse is None:
            raise ApplicationError(
                "A warehouse is required to restock inventory on a credit note.", code="warehouse_required"
            )

    row["source_invoice_line"] = source_invoice_line
    row["restock"] = restock
    row["unit_cost"] = unit_cost
    return row


def _get_credit_note_for_update(*, credit_note_id, organization) -> CreditNote:
    try:
        return CreditNote.objects.select_for_update().get(id=credit_note_id, organization=organization)
    except CreditNote.DoesNotExist:
        raise ApplicationError("Credit note not found.", code="credit_note_not_found", status_code=404)


@transaction.atomic
def create_credit_note(
    *,
    organization,
    customer: Customer,
    credit_note_date,
    lines: list[dict],
    source_invoice: Invoice | None = None,
    reason: str = "other",
    currency=None,
    exchange_rate: Decimal = Decimal("1"),
    reference: str = "",
    receivable_account=None,
    tax_payable_account=None,
    unapplied_credit_account=None,
    warehouse=None,
    notes: str = "",
    place_of_supply=None,
    tax_treatment: dict | None = None,
    actor=None,
) -> CreditNote:
    if customer.organization_id != organization.id:
        raise ApplicationError("Customer must belong to the posting organization.", code="customer_cross_org")
    assert_customer_usable_for_new_transaction(customer=customer)

    if source_invoice is not None:
        if source_invoice.organization_id != organization.id:
            raise ApplicationError("Invoice must belong to the posting organization.", code="invoice_cross_org")
        if source_invoice.customer_id != customer.id:
            raise ApplicationError("Invoice does not belong to this customer.", code="invoice_customer_mismatch")

    for account, expected_type, field_name in (
        (receivable_account, AccountType.ASSET, "receivable_account"),
        (tax_payable_account, AccountType.LIABILITY, "tax_payable_account"),
        (unapplied_credit_account, AccountType.LIABILITY, "unapplied_credit_account"),
    ):
        if account is not None:
            _validate_account(organization=organization, account=account, expected_type=expected_type, field_name=field_name)
    if warehouse is not None and warehouse.organization_id != organization.id:
        raise ApplicationError("warehouse must belong to the posting organization.", code="warehouse_cross_org")
    if not lines:
        raise ApplicationError("A credit note needs at least one line.", code="credit_note_no_lines")

    currency = currency or (source_invoice.currency if source_invoice else customer.currency)

    # A credit note against an invoice inherits that invoice's GST treatment
    # rather than re-determining it. Crediting a supply under a different
    # treatment than it was billed under would leave GSTR-1 unable to net the
    # two against each other.
    if tax_treatment is None:
        tax_treatment = (
            {k: v for k, v in carry_forward_tax(source_invoice).items() if k != "is_reverse_charge"}
            if source_invoice is not None
            else resolve_document_tax(
                organization=organization, party=customer, place_of_supply=place_of_supply
            )
        )

    line_rows = [
        _build_credit_note_line_row(
            organization=organization, credit_note_warehouse=warehouse, line=line,
            line_number=index, supply_nature=tax_treatment["supply_nature"],
        )
        for index, line in enumerate(lines, start=1)
    ]
    totals = calculate_document_totals(line_rows)
    components = component_totals(line_rows)

    credit_note = CreditNote.objects.create(
        organization=organization,
        customer=customer,
        reason=reason,
        source_invoice=source_invoice,
        credit_note_date=credit_note_date,
        reference=reference,
        currency=currency,
        exchange_rate=exchange_rate,
        receivable_account=receivable_account,
        tax_payable_account=tax_payable_account,
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
        CreditNoteLine.objects.create(organization=organization, credit_note=credit_note, **row)

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="sales.CreditNote",
        object_id=credit_note.id,
        changes={"total": str(totals["total"])},
    )
    return credit_note


@transaction.atomic
def replace_credit_note_lines(*, credit_note: CreditNote, lines: list[dict], actor=None) -> CreditNote:
    if credit_note.status != CreditNoteStatus.DRAFT:
        raise ApplicationError("Only draft credit notes can be modified.", code="credit_note_not_draft")
    if not lines:
        raise ApplicationError("A credit note needs at least one line.", code="credit_note_no_lines")

    line_rows = [
        _build_credit_note_line_row(
            organization=credit_note.organization, credit_note_warehouse=credit_note.warehouse, line=line,
            line_number=index, supply_nature=credit_note.supply_nature,
        )
        for index, line in enumerate(lines, start=1)
    ]
    totals = calculate_document_totals(line_rows)
    components = component_totals(line_rows)

    credit_note.lines.all().delete()
    for row in line_rows:
        CreditNoteLine.objects.create(organization=credit_note.organization, credit_note=credit_note, **row)

    credit_note.subtotal = totals["subtotal"]
    credit_note.discount_total = totals["discount"]
    credit_note.tax_total = totals["tax"]
    credit_note.total = totals["total"]
    for field, value in components.items():
        setattr(credit_note, field, value)
    credit_note.save(
        update_fields=[
            "subtotal", "discount_total", "tax_total", "total", *components.keys(), "updated_at",
        ]
    )

    record_audit(
        organization_id=credit_note.organization_id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="sales.CreditNote",
        object_id=credit_note.id,
        changes={"total": str(totals["total"])},
    )
    return credit_note


@transaction.atomic
def issue_credit_note(*, credit_note_id, organization, actor=None) -> CreditNote:
    """The single authoritative path from DRAFT to an issued financial
    correction. Idempotent like post_invoice. Posts ONE journal:
    Dr Sales Revenue (reversal) + Dr Tax Payable (reversal) against
    Cr AR (netted against the source invoice's remaining balance, if any)
    and/or Cr unapplied customer credit for any excess — never a negative
    invoice balance (root CLAUDE.md §27, reused here). Lines with
    restock=True additionally post Dr Inventory Asset / Cr COGS and issue
    an inbound stock movement. The original invoice is never mutated.
    """
    credit_note = _get_credit_note_for_update(credit_note_id=credit_note_id, organization=organization)

    if credit_note.status == CreditNoteStatus.ISSUED:
        return credit_note
    if credit_note.status != CreditNoteStatus.DRAFT:
        raise ApplicationError(
            f"Cannot issue a credit note in status '{credit_note.status}'.", code="credit_note_invalid_status"
        )

    lines = list(
        CreditNoteLine.objects.select_for_update().select_related("item").filter(credit_note=credit_note)
        .order_by("line_number")
    )
    if not lines:
        raise ApplicationError("Cannot issue a credit note with no lines.", code="credit_note_no_lines")

    receivable_account = credit_note.receivable_account
    if credit_note.source_invoice_id:
        source_invoice = Invoice.objects.select_for_update().get(pk=credit_note.source_invoice_id)
        receivable_account = receivable_account or source_invoice.receivable_account
    else:
        source_invoice = None

    # The account requirement is enforced by build_output_tax_lines below,
    # which only demands one when the components cannot carry the tax
    # themselves. The inheritance from the source invoice stays: a credit note
    # that omits the account still reverses into the account the original
    # supply was posted to.
    tax_payable_account_id = credit_note.tax_payable_account_id or (
        source_invoice.tax_payable_account_id if source_invoice else None
    )

    revenue_by_account = defaultdict(lambda: Decimal("0"))
    restock_by_pair = defaultdict(lambda: Decimal("0"))

    for line in lines:
        item = line.item
        revenue_by_account[item.sales_account_id] += line.line_base - line.discount_amount

        if line.restock and item.inventory_account_id and item.cogs_account_id:
            restock_amount = (line.quantity * line.unit_cost).quantize(Decimal("0.01"))
            restock_by_pair[(item.inventory_account_id, item.cogs_account_id)] += restock_amount

    # Net against the source invoice's remaining balance first — any excess
    # becomes unapplied customer credit, never a negative AR balance.
    applied_to_ar = Decimal("0")
    unapplied = credit_note.total
    if source_invoice is not None:
        due = get_invoice_amount_due(invoice=source_invoice)
        applied_to_ar = min(credit_note.total, due) if due > 0 else Decimal("0")
        unapplied = credit_note.total - applied_to_ar

    if unapplied > 0 and credit_note.unapplied_credit_account_id is None:
        raise ApplicationError(
            "unapplied_credit_account is required when the credit note exceeds the invoice's remaining balance.",
            code="unapplied_credit_account_required",
        )
    if applied_to_ar > 0 and receivable_account is None:
        raise ApplicationError("receivable_account is required to issue this credit note.", code="receivable_account_required")

    journal_lines = []
    for account_id, amount in revenue_by_account.items():
        if amount != 0:
            journal_lines.append({"account_id": account_id, "debit": amount})
    # A credit note reverses output tax, so each component is DEBITED here -
    # the mirror of post_invoice's credits, through the same builder so the
    # two can never disagree about which account a component belongs to.
    journal_lines.extend(
        build_tax_journal_lines(
            organization=organization,
            document=credit_note,
            direction=TaxDirection.OUTPUT,
            fallback_account_id=tax_payable_account_id,
            side="debit",
            account_required_message=(
                "tax_payable_account is required to issue a credit note with tax, unless "
                "every tax component is mapped to an account."
            ),
        )
    )
    if applied_to_ar > 0:
        journal_lines.append({"account_id": receivable_account.id, "credit": applied_to_ar})
    if unapplied > 0:
        journal_lines.append({"account_id": credit_note.unapplied_credit_account_id, "credit": unapplied})
    for (inventory_account_id, cogs_account_id), amount in restock_by_pair.items():
        if amount == 0:
            continue
        journal_lines.append({"account_id": inventory_account_id, "debit": amount})
        journal_lines.append({"account_id": cogs_account_id, "credit": amount})

    for line in lines:
        if line.restock:
            record_stock_movement(
                organization=organization,
                item=line.item,
                warehouse=credit_note.warehouse,
                movement_type=MovementType.ADJUSTMENT_IN,
                quantity=line.quantity,
                unit_cost=line.unit_cost,
                movement_date=credit_note.credit_note_date,
                source_type="sales.CreditNote",
                source_id=str(credit_note.id),
                notes=line.description,
                created_by=actor,
            )

    credit_note_number = allocate_sequence_number(
        organization_id=organization.id, key=CREDIT_NOTE_NUMBER_SEQUENCE_KEY, prefix="CN-"
    )

    journal = post_sales_journal(
        organization=organization,
        posting_date=credit_note.credit_note_date,
        currency=credit_note.currency,
        lines=journal_lines,
        memo=f"Credit Note {credit_note_number}",
        source_type="sales.CreditNote",
        source_id=str(credit_note.id),
        actor=actor,
    )

    credit_note.credit_note_number = credit_note_number
    credit_note.status = CreditNoteStatus.ISSUED
    credit_note.issued_by = actor
    credit_note.issued_at = timezone.now()
    credit_note.accounting_journal = journal
    credit_note.amount_applied_to_invoice = applied_to_ar
    credit_note.save(
        update_fields=[
            "credit_note_number", "status", "issued_by", "issued_at", "accounting_journal",
            "amount_applied_to_invoice", "updated_at",
        ]
    )

    if source_invoice is not None:
        from sales.services.invoices import refresh_invoice_payment_status

        refresh_invoice_payment_status(invoice=source_invoice, actor=actor)

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.POST,
        object_type="sales.CreditNote",
        object_id=credit_note.id,
        changes={
            "credit_note_number": credit_note_number, "total": str(credit_note.total),
            "applied_to_ar": str(applied_to_ar), "unapplied": str(unapplied),
        },
    )
    return credit_note


@transaction.atomic
def void_credit_note(*, credit_note_id, organization, actor=None) -> CreditNote:
    """Reverses an ISSUED credit note's accounting AND any restock it
    performed — never touching the original invoice."""
    credit_note = _get_credit_note_for_update(credit_note_id=credit_note_id, organization=organization)

    if credit_note.status == CreditNoteStatus.VOID:
        return credit_note
    if credit_note.status != CreditNoteStatus.ISSUED:
        raise ApplicationError(
            f"Cannot void a credit note in status '{credit_note.status}'.", code="credit_note_invalid_status"
        )

    if credit_note.accounting_journal_id:
        reverse_journal(
            journal_id=credit_note.accounting_journal_id,
            organization=organization,
            actor=actor,
            posting_date=timezone.now().date(),
            memo=f"Void of credit note {credit_note.credit_note_number}",
        )

    for line in credit_note.lines.select_related("item").filter(restock=True):
        record_stock_movement(
            organization=organization,
            item=line.item,
            warehouse=credit_note.warehouse,
            # ADJUSTMENT_OUT, not ISSUE — this reverses the ADJUSTMENT_IN the
            # restock itself created, same type-pairing convention as
            # inventory.services.adjustments.reverse_stock_adjustment.
            movement_type=MovementType.ADJUSTMENT_OUT,
            quantity=line.quantity,
            unit_cost=line.unit_cost,
            movement_date=timezone.now().date(),
            source_type="sales.CreditNote.void",
            source_id=str(credit_note.id),
            notes=f"Void reversal: {line.description}",
            created_by=actor,
        )

    credit_note.status = CreditNoteStatus.VOID
    credit_note.voided_by = actor
    credit_note.voided_at = timezone.now()
    credit_note.save(update_fields=["status", "voided_by", "voided_at", "updated_at"])

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.REVERSE,
        object_type="sales.CreditNote",
        object_id=credit_note.id,
        changes={},
    )
    return credit_note
