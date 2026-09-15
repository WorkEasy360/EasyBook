import uuid
from collections import defaultdict
from decimal import Decimal

from django.db import transaction

from accounting.models.account import AccountType
from accounts.services import allocate_sequence_number
from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError
from sales.accounting_bridge import post_sales_journal
from sales.models.customer import Customer
from sales.models.invoice import Invoice, InvoiceStatus
from sales.models.payment import CustomerPayment, PaymentAllocation, PaymentMethod
from sales.selectors import get_invoice_amount_due
from sales.services.invoices import refresh_invoice_payment_status

PAYMENT_NUMBER_SEQUENCE_KEY = "payment"


def _validate_account(*, organization, account, expected_type, field_name):
    if account.organization_id != organization.id:
        raise ApplicationError(f"{field_name} must belong to the posting organization.", code="cross_org_reference")
    if account.account_type != expected_type:
        raise ApplicationError(
            f"{field_name} must reference an account of type '{expected_type}'.", code="invalid_account_type"
        )


@transaction.atomic
def record_payment(
    *,
    organization,
    customer: Customer,
    payment_date,
    amount: Decimal,
    destination_account,
    allocations: list[dict],
    currency=None,
    payment_method: str = PaymentMethod.OTHER,
    reference: str = "",
    notes: str = "",
    unapplied_credit_account=None,
    actor=None,
) -> CustomerPayment:
    """Records one customer payment and allocates it across one or more
    invoices in a single atomic operation — see sales/CLAUDE.md and root
    CLAUDE.md §9/§27. Any amount not explicitly allocated becomes unapplied
    customer credit (a PaymentAllocation with invoice=None), never a
    negative invoice balance and never silent over-allocation.

    Duplicate-request safety is the caller's responsibility via the
    Idempotency-Key header (core.idempotency) — this function has no
    DRAFT/POSTED status to exploit for idempotent-by-construction posting,
    unlike post_invoice.
    """
    if customer.organization_id != organization.id:
        raise ApplicationError("Customer must belong to the posting organization.", code="customer_cross_org")
    _validate_account(
        organization=organization, account=destination_account, expected_type=AccountType.ASSET,
        field_name="destination_account",
    )
    if amount <= 0:
        raise ApplicationError("Payment amount must be positive.", code="payment_amount_invalid")

    currency = currency or customer.currency

    locked_invoices = {}
    allocated_total = Decimal("0")
    validated_allocations = []
    for entry in allocations:
        invoice_id = entry["invoice"].id
        alloc_amount = entry["amount"]
        if alloc_amount <= 0:
            raise ApplicationError("Allocation amount must be positive.", code="allocation_amount_invalid")

        # Locked so two concurrent payments against the same invoice can
        # never both succeed in over-allocating it — same pattern as
        # inventory.services.movements.record_stock_movement's negative-stock guard.
        invoice = locked_invoices.get(invoice_id)
        if invoice is None:
            try:
                invoice = Invoice.objects.select_for_update().get(id=invoice_id, organization=organization)
            except Invoice.DoesNotExist:
                raise ApplicationError("Invoice not found.", code="invoice_not_found", status_code=404)
            locked_invoices[invoice_id] = invoice

        if invoice.customer_id != customer.id:
            raise ApplicationError("Invoice does not belong to this customer.", code="invoice_customer_mismatch")
        if invoice.status not in (InvoiceStatus.SENT, InvoiceStatus.PARTIALLY_PAID):
            raise ApplicationError(
                f"Cannot allocate a payment to an invoice in status '{invoice.status}'.",
                code="invoice_not_payable",
            )

        already_allocated_here = sum(
            (a["amount"] for a in validated_allocations if a["invoice"].id == invoice_id), Decimal("0")
        )
        due = get_invoice_amount_due(invoice=invoice) - already_allocated_here
        if alloc_amount > due:
            raise ApplicationError(
                f"Allocation ({alloc_amount}) exceeds the amount due on this invoice ({due}).",
                code="over_allocation",
            )

        allocated_total += alloc_amount
        validated_allocations.append({"invoice": invoice, "amount": alloc_amount})

    if allocated_total > amount:
        raise ApplicationError(
            "Sum of allocations cannot exceed the payment amount.", code="allocation_exceeds_payment"
        )
    remainder = amount - allocated_total
    if remainder > 0:
        if unapplied_credit_account is None:
            raise ApplicationError(
                "unapplied_credit_account is required when the payment exceeds its allocated invoices.",
                code="unapplied_credit_account_required",
            )
        _validate_account(
            organization=organization, account=unapplied_credit_account, expected_type=AccountType.LIABILITY,
            field_name="unapplied_credit_account",
        )

    payment_number = allocate_sequence_number(
        organization_id=organization.id, key=PAYMENT_NUMBER_SEQUENCE_KEY, prefix="PAY-"
    )

    # CustomerPayment.save() refuses any update after the initial INSERT
    # (append-only, like StockMovement/AuditLog — see models/payment.py), so
    # accounting_journal must be known BEFORE that one create() call, not
    # patched in with a second .save(). Generate the id up front so the
    # journal's source_id can reference it.
    payment_id = uuid.uuid4()

    credit_by_account = defaultdict(lambda: Decimal("0"))
    for entry in validated_allocations:
        credit_by_account[entry["invoice"].receivable_account_id] += entry["amount"]
    if remainder > 0:
        credit_by_account[unapplied_credit_account.id] += remainder

    journal_lines = [{"account_id": destination_account.id, "debit": amount}]
    for account_id, amt in credit_by_account.items():
        if amt != 0:
            journal_lines.append({"account_id": account_id, "credit": amt})

    journal = post_sales_journal(
        organization=organization,
        posting_date=payment_date,
        currency=currency,
        lines=journal_lines,
        memo=f"Payment {payment_number}",
        source_type="sales.CustomerPayment",
        source_id=str(payment_id),
        actor=actor,
    )

    payment = CustomerPayment.objects.create(
        id=payment_id,
        organization=organization,
        customer=customer,
        payment_number=payment_number,
        payment_date=payment_date,
        amount=amount,
        currency=currency,
        payment_method=payment_method,
        reference=reference,
        destination_account=destination_account,
        accounting_journal=journal,
        notes=notes,
        created_by=actor,
    )
    for entry in validated_allocations:
        PaymentAllocation.objects.create(
            organization=organization, payment=payment, invoice=entry["invoice"], amount=entry["amount"]
        )
    if remainder > 0:
        PaymentAllocation.objects.create(organization=organization, payment=payment, invoice=None, amount=remainder)

    for entry in validated_allocations:
        refresh_invoice_payment_status(invoice=entry["invoice"], actor=actor)

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="sales.CustomerPayment",
        object_id=payment.id,
        changes={
            "payment_number": payment_number, "amount": str(amount),
            "allocated": str(allocated_total), "unapplied": str(remainder),
        },
    )
    return payment
