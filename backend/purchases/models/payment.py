from django.conf import settings
from django.db import models

from core.enums import PaymentMethod
from core.models import TenantScopedModel


class VendorPayment(TenantScopedModel):
    """Money paid out to a vendor — the mirror of `sales.CustomerPayment`.
    Like it, there is no DRAFT phase: a payment is a fact the moment it is
    recorded, so this model is append-only from creation (same pattern as
    inventory.StockMovement / audit.AuditLog), not merely immutable after
    leaving draft. Numbered and posted to accounting atomically at creation
    — see services/payments.py.

    Duplicate-request safety is handled by the Idempotency-Key header at the
    API layer (core.idempotency): there is no DRAFT/POSTED status to make
    posting idempotent-by-construction, unlike post_bill.
    """

    vendor = models.ForeignKey("purchases.Vendor", on_delete=models.PROTECT, related_name="payments")
    payment_number = models.CharField(max_length=32)
    payment_date = models.DateField()
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.ForeignKey("accounts.Currency", on_delete=models.PROTECT, related_name="+")
    payment_method = models.CharField(max_length=16, choices=PaymentMethod.choices, default=PaymentMethod.OTHER)
    reference = models.CharField(max_length=255, blank=True)
    # Where the money LEAVES from — the mirror of CustomerPayment's
    # destination_account. Explicit, never defaulted.
    source_account = models.ForeignKey("accounting.Account", on_delete=models.PROTECT, related_name="+")
    accounting_journal = models.ForeignKey(
        "accounting.JournalEntry", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "payment_number"], name="uniq_vendor_payment_number_per_org"
            ),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="vendor_payment_amount_positive"),
        ]
        indexes = [
            models.Index(fields=["organization", "vendor"]),
            models.Index(fields=["organization", "payment_date"]),
        ]
        ordering = ["-payment_date", "-created_at"]

    def __str__(self):
        return self.payment_number

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Vendor payments are append-only and cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Vendor payments are append-only and cannot be deleted.")


class VendorPaymentAllocation(TenantScopedModel):
    """One slice of a VendorPayment applied to a Bill — or, when `bill` is
    null, an advance paid to the vendor and not yet matched to any bill
    (the buy-side of sales' unapplied-credit rule: an overpayment becomes a
    vendor advance, never a negative bill balance and never a silent
    over-allocation). Append-only, like the payment it belongs to.
    """

    payment = models.ForeignKey(VendorPayment, on_delete=models.PROTECT, related_name="allocations")
    bill = models.ForeignKey(
        "purchases.Bill", null=True, blank=True, on_delete=models.PROTECT, related_name="payment_allocations"
    )
    amount = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="vendor_payment_allocation_amount_positive"),
        ]
        indexes = [
            models.Index(fields=["organization", "bill"]),
            models.Index(fields=["payment"]),
        ]

    def __str__(self):
        target = f"bill:{self.bill_id}" if self.bill_id else "vendor_advance"
        return f"{self.payment_id}->{target}:{self.amount}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Vendor payment allocations are append-only and cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Vendor payment allocations are append-only and cannot be deleted.")
