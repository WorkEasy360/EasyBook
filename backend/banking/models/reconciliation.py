from django.conf import settings
from django.db import models

from core.models import TenantScopedModel


class ReconciliationStatus(models.TextChoices):
    IN_PROGRESS = "in_progress", "In progress"
    COMPLETED = "completed", "Completed"
    ABANDONED = "abandoned", "Abandoned"


class BankReconciliation(TenantScopedModel):
    """One period's formal agreement between a bank statement and the books.

    The closing balance is a number a PERSON reads off a paper or PDF
    statement and types in. It is deliberately not derived from the imported
    transactions: if it were, it would agree with them by construction and
    the reconciliation would prove nothing. Its whole value is that it is an
    independent figure from outside the system, so a line the bank charged
    and we never imported shows up as a difference instead of vanishing.

    COMPLETING IS THE CONTROL. `services/reconciliation.py::complete` refuses
    unless the cleared balance equals the typed closing balance to the cent
    AND no line in the period is still unmatched or merely suggested. A
    reconciliation that can be forced closed over a difference is a
    reconciliation that certifies nothing, so there is no override flag here
    — the escape hatch is to categorize or exclude the offending lines, both
    of which leave a record.
    """

    bank_account = models.ForeignKey(
        "banking.BankAccount", on_delete=models.PROTECT, related_name="reconciliations"
    )
    statement_start_date = models.DateField()
    statement_end_date = models.DateField()
    # Signed on the same convention as BankTransaction.amount: positive is
    # money held, negative is money owed. A credit card statement's closing
    # balance is therefore entered as a negative number.
    statement_closing_balance = models.DecimalField(max_digits=18, decimal_places=2)
    status = models.CharField(
        max_length=16, choices=ReconciliationStatus.choices, default=ReconciliationStatus.IN_PROGRESS
    )
    # Frozen at completion. The derived figure would drift the moment a later
    # backdated transaction was imported, and a signed-off reconciliation
    # that silently changes afterwards is worse than none.
    cleared_balance = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(statement_end_date__gte=models.F("statement_start_date")),
                name="bank_reconciliation_dates_ordered",
            ),
            # One open reconciliation per account at a time. Two overlapping
            # drafts would both claim the same statement lines and whichever
            # completed second would find them already locked.
            models.UniqueConstraint(
                fields=["bank_account"],
                condition=models.Q(status=ReconciliationStatus.IN_PROGRESS),
                name="uniq_open_reconciliation_per_bank_account",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "bank_account", "statement_end_date"]),
        ]
        ordering = ["-statement_end_date"]

    def __str__(self):
        return f"{self.bank_account_id} to {self.statement_end_date}"
