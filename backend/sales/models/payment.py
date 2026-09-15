from django.conf import settings
from django.db import models

from core.models import TenantScopedModel


class PaymentMethod(models.TextChoices):
    CASH = "cash", "Cash"
    BANK_TRANSFER = "bank_transfer", "Bank Transfer"
    CHEQUE = "cheque", "Cheque"
    CARD = "card", "Card"
    UPI = "upi", "UPI"
    OTHER = "other", "Other"


class CustomerPayment(TenantScopedModel):
    """A completed receipt of money from a customer. Unlike Quote/Order/
    Invoice there is no DRAFT phase — a payment is a fact the moment it's
    recorded (like StockMovement/AuditLog), so it is append-only from
    creation, not merely "immutable after leaving draft". Numbered and
    posted to accounting atomically at creation — see services/payments.py.
    Duplicate-request safety is handled by core.idempotency's
    Idempotency-Key header at the API layer (there is no DRAFT/POSTED
    status to make posting idempotent-by-construction, unlike Invoice).
    """

    customer = models.ForeignKey("sales.Customer", on_delete=models.PROTECT, related_name="payments")
    payment_number = models.CharField(max_length=32)
    payment_date = models.DateField()
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.ForeignKey("accounts.Currency", on_delete=models.PROTECT, related_name="+")
    payment_method = models.CharField(max_length=16, choices=PaymentMethod.choices, default=PaymentMethod.OTHER)
    reference = models.CharField(max_length=255, blank=True)
    # Explicit, not defaulted — same "no implicit account invented" principle
    # as Invoice.receivable_account / inventory.StockAdjustment.contra_account.
    destination_account = models.ForeignKey("accounting.Account", on_delete=models.PROTECT, related_name="+")
    accounting_journal = models.ForeignKey(
        "accounting.JournalEntry", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "payment_number"], name="uniq_payment_number_per_org"),
            models.CheckConstraint(check=models.Q(amount__gt=0), name="payment_amount_positive"),
        ]
        indexes = [
            models.Index(fields=["organization", "customer"]),
            models.Index(fields=["organization", "payment_date"]),
        ]
        ordering = ["-payment_date", "-created_at"]

    def __str__(self):
        return self.payment_number

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Customer payments are append-only and cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Customer payments are append-only and cannot be deleted.")


class PaymentAllocation(TenantScopedModel):
    """One slice of a CustomerPayment applied to an Invoice — or, when
    `invoice` is null, held as unapplied customer credit (root CLAUDE.md
    §27 overpayment policy: excess becomes credit, never a negative invoice
    balance). Append-only, like the payment it belongs to; correcting a
    misallocation is out of scope for this phase (no reverse_payment
    service exists — see sales/CLAUDE.md).
    """

    payment = models.ForeignKey(CustomerPayment, on_delete=models.PROTECT, related_name="allocations")
    invoice = models.ForeignKey(
        "sales.Invoice", null=True, blank=True, on_delete=models.PROTECT, related_name="payment_allocations"
    )
    amount = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta:
        constraints = [
            models.CheckConstraint(check=models.Q(amount__gt=0), name="payment_allocation_amount_positive"),
        ]
        indexes = [
            models.Index(fields=["organization", "invoice"]),
            models.Index(fields=["payment"]),
        ]

    def __str__(self):
        target = f"invoice:{self.invoice_id}" if self.invoice_id else "unapplied_credit"
        return f"{self.payment_id}->{target}:{self.amount}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Payment allocations are append-only and cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Payment allocations are append-only and cannot be deleted.")
