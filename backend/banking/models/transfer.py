from django.conf import settings
from django.db import models

from core.models import TenantScopedModel


class BankTransferStatus(models.TextChoices):
    POSTED = "posted", "Posted"
    VOID = "void", "Void"


class BankTransfer(TenantScopedModel):
    """Money moved between two accounts the organization owns.

    There is deliberately NO draft phase, for the same reason
    `sales.CustomerPayment` has none: a transfer is a fact the moment it is
    recorded, so it is created and posted in one atomic act
    (services/transfers.py::record_transfer). Unlike a payment it can be
    voided, because a transfer is the one money movement a user commonly
    records against the wrong pair of accounts — and the correction is a
    reversing journal, never an edit (root CLAUDE.md: never silently edit
    posted data). Only `status` and the void audit fields are mutable; the
    financial fields are frozen at creation.

    A transfer between two of your own accounts is net-zero for the business:
    the journal debits the destination and credits the source, and nothing
    touches income or expense. Getting that wrong — booking a transfer as
    income — is the single most common way a self-serve ledger overstates
    revenue, which is why transfer DETECTION is a first-class part of the
    matcher (services/matching.py) rather than something left to rules.
    """

    _FROZEN_FIELDS = (
        "from_bank_account_id",
        "to_bank_account_id",
        "transfer_date",
        "amount",
        "currency_id",
        "transfer_number",
        "accounting_journal_id",
    )

    transfer_number = models.CharField(max_length=32)
    from_bank_account = models.ForeignKey(
        "banking.BankAccount", on_delete=models.PROTECT, related_name="transfers_out"
    )
    to_bank_account = models.ForeignKey(
        "banking.BankAccount", on_delete=models.PROTECT, related_name="transfers_in"
    )
    transfer_date = models.DateField()
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.ForeignKey("accounts.Currency", on_delete=models.PROTECT, related_name="+")
    reference = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)

    status = models.CharField(
        max_length=16, choices=BankTransferStatus.choices, default=BankTransferStatus.POSTED
    )
    accounting_journal = models.ForeignKey(
        "accounting.JournalEntry", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "transfer_number"], name="uniq_bank_transfer_number_per_org"
            ),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="bank_transfer_amount_positive"),
            models.CheckConstraint(
                condition=~models.Q(from_bank_account=models.F("to_bank_account")),
                name="bank_transfer_distinct_accounts",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "transfer_date"]),
            models.Index(fields=["organization", "from_bank_account"]),
            models.Index(fields=["organization", "to_bank_account"]),
        ]
        ordering = ["-transfer_date", "-created_at"]

    def __str__(self):
        return self.transfer_number

    def save(self, *args, **kwargs):
        if not self._state.adding:
            original = type(self).all_objects.filter(pk=self.pk).values(*self._FROZEN_FIELDS).first()
            if original is not None:
                changed = [f for f in self._FROZEN_FIELDS if original[f] != getattr(self, f)]
                if changed:
                    raise ValueError(
                        "A posted transfer's financial fields are immutable; void it instead. "
                        f"Attempted to change {sorted(changed)}."
                    )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Bank transfers cannot be deleted; void them instead.")
