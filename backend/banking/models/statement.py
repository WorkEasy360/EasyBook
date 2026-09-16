from django.conf import settings
from django.db import models

from core.models import TenantScopedModel


class StatementFormat(models.TextChoices):
    CSV = "csv", "CSV"
    OFX = "ofx", "OFX"
    QIF = "qif", "QIF"
    FEED = "feed", "Bank feed"
    MANUAL = "manual", "Manual entry"


class StatementImportStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class BankTransactionStatus(models.TextChoices):
    UNMATCHED = "unmatched", "Unmatched"
    SUGGESTED = "suggested", "Suggested"
    MATCHED = "matched", "Matched"
    EXCLUDED = "excluded", "Excluded"


class StatementImport(TenantScopedModel):
    """One ingestion of statement data into one bank account.

    Kept as a first-class record rather than a transient parse step because
    it is the only thing that can answer "where did this transaction come
    from" — and because `file_hash` is the cheapest possible guard against
    the most common user error in banking software: uploading the same
    download twice.

    The whole-file hash is only the FIRST guard, and the weaker one; it
    catches a byte-identical re-upload and nothing else. The guard that
    actually matters is per-transaction (see `BankTransaction.fingerprint`),
    because the realistic duplicate is an overlapping date range exported on
    two different days, whose bytes differ.
    """

    bank_account = models.ForeignKey(
        "banking.BankAccount", on_delete=models.PROTECT, related_name="statement_imports"
    )
    source_format = models.CharField(max_length=16, choices=StatementFormat.choices)
    file_name = models.CharField(max_length=255, blank=True)
    file_hash = models.CharField(max_length=64, blank=True)
    status = models.CharField(
        max_length=16, choices=StatementImportStatus.choices, default=StatementImportStatus.PENDING
    )
    statement_start_date = models.DateField(null=True, blank=True)
    statement_end_date = models.DateField(null=True, blank=True)
    rows_read = models.PositiveIntegerField(default=0)
    rows_imported = models.PositiveIntegerField(default=0)
    rows_skipped_duplicate = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        constraints = [
            # Scoped to the bank account, not the organization: the same file
            # cannot be the statement of two different accounts, and a
            # per-org unique would block an organization banking with one
            # institution under two accounts whose empty exports collide.
            models.UniqueConstraint(
                fields=["bank_account", "file_hash"],
                condition=~models.Q(file_hash=""),
                name="uniq_statement_file_hash_per_bank_account",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "bank_account"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.source_format} import for {self.bank_account_id}"


class BankTransaction(TenantScopedModel):
    """One line of a bank statement.

    A statement line is EVIDENCE, not an accounting event. Importing it posts
    nothing — exactly as `purchases.GoodsReceipt` moves stock without posting
    a journal. The bank telling us money moved does not by itself say which
    account it belongs to, and guessing is what reconciliation exists to
    avoid. A journal appears only when the line is categorized
    (services/matching.py::categorize_transaction) or when it is matched to a
    document that already posted its own.

    The bank-reported fields are frozen at insert (`_BANK_REPORTED_FIELDS`).
    They are the bank's assertion about what happened, and editing them would
    quietly destroy the only independent record we hold against our own
    books. What IS mutable is our interpretation: status, exclusion, and
    which reconciliation the line was cleared in.
    """

    # Fields the bank told us. Immutable once written — see save().
    _BANK_REPORTED_FIELDS = (
        "bank_account_id",
        "transaction_date",
        "amount",
        "description",
        "counterparty_name",
        "bank_reference",
        "external_id",
        "fingerprint",
        "duplicate_ordinal",
    )

    bank_account = models.ForeignKey(
        "banking.BankAccount", on_delete=models.PROTECT, related_name="transactions"
    )
    statement_import = models.ForeignKey(
        StatementImport, null=True, blank=True, on_delete=models.PROTECT, related_name="transactions"
    )
    transaction_date = models.DateField()
    # Signed, account-holder's perspective: positive = money in, negative =
    # money out. See BankAccount's docstring for why one signed column beats
    # separate deposit/withdrawal columns (which can both be populated, and
    # then no reader agrees on what the row means).
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    description = models.TextField(blank=True)
    counterparty_name = models.CharField(max_length=255, blank=True)
    bank_reference = models.CharField(max_length=255, blank=True)
    # The bank's own unique id for the line (OFX FITID, or a CSV column that
    # carries one). When present it IS the fingerprint input, because an
    # institution-issued id is a stronger duplicate guard than anything we
    # can derive from the visible columns.
    external_id = models.CharField(max_length=255, blank=True)

    fingerprint = models.CharField(max_length=64)
    # How many rows with this exact fingerprint already existed when this one
    # was imported. Two identical 500.00 ATM withdrawals on one day are a
    # REAL pair, not a duplicate, so the uniqueness guard cannot be on the
    # fingerprint alone — it has to count occurrences. See
    # services/imports.py for the multiset reasoning.
    duplicate_ordinal = models.PositiveIntegerField(default=0)

    status = models.CharField(
        max_length=16, choices=BankTransactionStatus.choices, default=BankTransactionStatus.UNMATCHED
    )
    excluded_reason = models.CharField(max_length=255, blank=True)
    reconciliation = models.ForeignKey(
        "banking.BankReconciliation", null=True, blank=True, on_delete=models.PROTECT, related_name="transactions"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["bank_account", "fingerprint", "duplicate_ordinal"],
                name="uniq_bank_transaction_occurrence",
            ),
            # A zero-amount statement line carries no financial information
            # and would match anything with a zero-amount counterpart.
            models.CheckConstraint(condition=~models.Q(amount=0), name="bank_transaction_amount_nonzero"),
            models.CheckConstraint(
                condition=(models.Q(status=BankTransactionStatus.EXCLUDED) | models.Q(excluded_reason="")),
                name="bank_transaction_reason_only_when_excluded",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "bank_account", "transaction_date"]),
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["bank_account", "amount"]),
        ]
        ordering = ["-transaction_date", "-created_at"]

    def __str__(self):
        return f"{self.transaction_date} {self.amount}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            original = type(self).all_objects.filter(pk=self.pk).values(*self._BANK_REPORTED_FIELDS).first()
            if original is not None:
                changed = [f for f in self._BANK_REPORTED_FIELDS if original[f] != getattr(self, f)]
                if changed:
                    raise ValueError(
                        "Bank-reported fields are immutable once imported; "
                        f"attempted to change {sorted(changed)}."
                    )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        # Deleting evidence is never the right correction. A line that should
        # not have been imported is EXCLUDED, which keeps it visible and
        # auditable — see services/transactions.py::exclude_transaction.
        raise ValueError("Bank transactions cannot be deleted; exclude them instead.")

    @property
    def is_inflow(self) -> bool:
        return self.amount > 0
