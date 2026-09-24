from decimal import Decimal

from django.conf import settings
from django.db import models

from core.models import TenantScopedModel


class JournalStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    POSTED = "posted", "Posted"
    REVERSED = "reversed", "Reversed"


# Statuses whose lines are part of the ledger. A REVERSED journal stays a
# posted fact of its own period; its effect is cancelled by the separate POSTED
# reversal journal. Counting POSTED alone drops the original but keeps its
# mirror, so every balance/report derived from JournalLine must use this.
LEDGER_STATUSES = (JournalStatus.POSTED, JournalStatus.REVERSED)


class JournalEntry(TenantScopedModel):
    """A single accounting transaction: header for a balanced set of JournalLines.

    Numbering is allocated at POST time (accounting.services.posting), not on
    draft creation, so abandoned drafts never burn a sequence number.
    """

    journal_number = models.CharField(max_length=32, blank=True)
    reference = models.CharField(max_length=255, blank=True)
    posting_date = models.DateField()
    memo = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=JournalStatus.choices, default=JournalStatus.DRAFT)
    source_type = models.CharField(max_length=64, blank=True)
    source_id = models.CharField(max_length=64, blank=True)

    currency = models.ForeignKey("accounts.Currency", on_delete=models.PROTECT, related_name="+")
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=8, default=Decimal("1"))

    fiscal_year = models.ForeignKey(
        "accounts.FiscalYear", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    posted_at = models.DateTimeField(null=True, blank=True)

    # A journal may be reversed at most once — enforced by this being a
    # OneToOneField (the reversal journal points back at the original it reverses).
    reverses = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversal"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "journal_number"],
                name="uniq_journal_number_per_org",
                condition=~models.Q(journal_number=""),
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "status", "posting_date"]),
            models.Index(fields=["organization", "source_type", "source_id"]),
        ]
        verbose_name_plural = "journal entries"
        ordering = ["-posting_date", "-created_at"]

    def __str__(self):
        return self.journal_number or f"draft:{self.id}"

    # Fields the posting/reversal services are allowed to touch on a journal
    # that has left DRAFT. Everything else (lines, posting_date, accounts,
    # amounts, ...) is frozen once posted — see accounting/CLAUDE.md.
    _MUTABLE_AFTER_DRAFT_FIELDS = {"status", "posted_by", "posted_by_id", "posted_at", "journal_number", "updated_at"}

    def save(self, *args, **kwargs):
        if not self._state.adding:
            previous_status = JournalEntry.all_objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if previous_status and previous_status != JournalStatus.DRAFT:
                update_fields = kwargs.get("update_fields")
                if not update_fields or not set(update_fields) <= self._MUTABLE_AFTER_DRAFT_FIELDS:
                    raise ValueError(
                        "Posted journal entries are immutable except through the posting/reversal services."
                    )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != JournalStatus.DRAFT:
            raise ValueError("Only draft journal entries can be deleted.")
        super().delete(*args, **kwargs)


class JournalLine(TenantScopedModel):
    """One debit or credit leg of a JournalEntry.

    `base_debit`/`base_credit` hold the amount converted to the organization's
    base currency (debit/credit * JournalEntry.exchange_rate) — the
    multi-currency foundation described in accounting/CLAUDE.md. Ledger/Trial
    Balance queries read the base_* columns.
    """

    journal_entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey("accounting.Account", on_delete=models.PROTECT, related_name="+")
    line_number = models.PositiveIntegerField()
    description = models.CharField(max_length=255, blank=True)

    debit = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    credit = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    base_debit = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    base_credit = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(debit__gte=0) & models.Q(credit__gte=0),
                name="journal_line_amounts_nonnegative",
            ),
            models.CheckConstraint(
                condition=~(models.Q(debit__gt=0) & models.Q(credit__gt=0)),
                name="journal_line_not_both_debit_and_credit",
            ),
            models.CheckConstraint(
                condition=~(models.Q(debit=0) & models.Q(credit=0)),
                name="journal_line_not_both_zero",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "account"]),
            models.Index(fields=["journal_entry", "line_number"]),
        ]
        ordering = ["journal_entry", "line_number"]

    def __str__(self):
        return f"{self.journal_entry_id}#{self.line_number}"

    def _parent_status(self):
        return JournalEntry.all_objects.filter(pk=self.journal_entry_id).values_list("status", flat=True).first()

    def save(self, *args, **kwargs):
        # Adding counts too: a new line on a posted journal changes its
        # totals exactly as much as editing one. The database trigger
        # (migration 0005) enforces the same for every path, including
        # queryset update/delete and bulk_create, which never call save().
        parent_status = self._parent_status()
        if parent_status and parent_status != JournalStatus.DRAFT:
            raise ValueError("Cannot modify a line belonging to a posted journal entry.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        parent_status = self._parent_status()
        if parent_status and parent_status != JournalStatus.DRAFT:
            raise ValueError("Cannot delete a line belonging to a posted journal entry.")
        super().delete(*args, **kwargs)
