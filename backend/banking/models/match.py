from django.conf import settings
from django.db import models

from core.models import TenantScopedModel

# The counterparts a statement line can be explained by. Each is a real FK
# rather than the (source_type, source_id) string pair `accounting.JournalEntry`
# uses, because here the database must actually protect the link: PROTECT stops
# a matched payment being deleted out from under a closed reconciliation, and a
# string pair cannot express that.
COUNTERPART_FIELDS = (
    "customer_payment",
    "vendor_payment",
    "expense",
    "bank_transfer",
    "journal_entry",
)


def _exactly_one_counterpart() -> models.Q:
    """Exactly one of COUNTERPART_FIELDS is set.

    Written out as an explicit OR of mutually exclusive terms rather than a
    raw `num_nonnulls(...) = 1`: it deconstructs into a migration cleanly and
    it stays readable, which matters because this is the constraint that stops
    a single statement line claiming to be both a customer receipt and a
    vendor payment.
    """
    clauses = models.Q()
    for field in COUNTERPART_FIELDS:
        term = models.Q(**{f"{field}__isnull": False})
        for other in COUNTERPART_FIELDS:
            if other != field:
                term &= models.Q(**{f"{other}__isnull": True})
        clauses |= term
    return clauses


class MatchType(models.TextChoices):
    EXACT = "exact", "Exact (amount, date and reference)"
    AMOUNT_DATE = "amount_date", "Amount and date"
    TRANSFER = "transfer", "Transfer between own accounts"
    RULE = "rule", "Reconciliation rule"
    CATEGORIZATION = "categorization", "Categorized to an account"
    MANUAL = "manual", "Chosen by a person"


class SuggestionSource(models.TextChoices):
    """Who proposed this match. Load-bearing, not descriptive metadata.

    Root CLAUDE.md draws a hard line: AI may suggest, but deterministic logic
    performs the authoritative act. `AI` exists here so that line is
    enforceable in code rather than by convention — `services/matching.py`
    refuses to confirm an AI-sourced match without a human actor, and refuses
    to auto-confirm one under any circumstances. No AI produces suggestions
    yet (that is Phase 10); the guard is in place first, deliberately, because
    a control added after the feature it controls is a control that shipped
    late.
    """

    SYSTEM = "system", "Deterministic matcher"
    RULE = "rule", "Reconciliation rule"
    USER = "user", "Person"
    AI = "ai", "AI assistant"


class BankTransactionMatch(TenantScopedModel):
    """A proposed or confirmed explanation of one statement line.

    MATCHING POSTS NO ACCOUNTING. Linking a statement line to a
    `CustomerPayment` records that the money we already booked is the money
    the bank saw; the payment posted its own journal when it was recorded, and
    posting a second one here would double the revenue. The single exception
    is CATEGORIZATION, where there is no existing document and
    `services/matching.py::categorize_transaction` creates the journal — and
    then this row points at that journal.

    `amount` is always POSITIVE and is the slice of the line this match
    explains. One statement line may need several matches (a single deposit
    covering three customer payments), and the slices may never sum to more
    than the line itself — enforced under a lock in services/matching.py,
    because an unlocked derived-total check is a read-then-write race (the
    lesson from purchases/services/bills.py).
    """

    transaction = models.ForeignKey(
        "banking.BankTransaction", on_delete=models.PROTECT, related_name="matches"
    )

    customer_payment = models.ForeignKey(
        "sales.CustomerPayment", null=True, blank=True, on_delete=models.PROTECT, related_name="bank_matches"
    )
    vendor_payment = models.ForeignKey(
        "purchases.VendorPayment", null=True, blank=True, on_delete=models.PROTECT, related_name="bank_matches"
    )
    expense = models.ForeignKey(
        "purchases.Expense", null=True, blank=True, on_delete=models.PROTECT, related_name="bank_matches"
    )
    bank_transfer = models.ForeignKey(
        "banking.BankTransfer", null=True, blank=True, on_delete=models.PROTECT, related_name="bank_matches"
    )
    journal_entry = models.ForeignKey(
        "accounting.JournalEntry", null=True, blank=True, on_delete=models.PROTECT, related_name="bank_matches"
    )

    amount = models.DecimalField(max_digits=18, decimal_places=2)
    match_type = models.CharField(max_length=16, choices=MatchType.choices)
    suggestion_source = models.CharField(
        max_length=16, choices=SuggestionSource.choices, default=SuggestionSource.SYSTEM
    )
    # 0..1. Deterministic and reproducible — see services/matching.py::score.
    # It is an ordering aid for a human, never a licence to post: nothing in
    # this app confirms a match because its confidence was high enough on its
    # own, only because it was high enough AND unambiguous.
    confidence = models.DecimalField(max_digits=4, decimal_places=3, default=0)
    reason = models.CharField(max_length=255, blank=True)

    is_confirmed = models.BooleanField(default=False)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="bank_match_amount_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(confidence__gte=0) & models.Q(confidence__lte=1),
                name="bank_match_confidence_in_range",
            ),
            models.CheckConstraint(
                condition=_exactly_one_counterpart(), name="bank_match_exactly_one_counterpart"
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "transaction"]),
            models.Index(fields=["organization", "is_confirmed"]),
            models.Index(fields=["customer_payment"]),
            models.Index(fields=["vendor_payment"]),
            models.Index(fields=["expense"]),
        ]
        ordering = ["-confidence", "-created_at"]

    def __str__(self):
        state = "confirmed" if self.is_confirmed else "suggested"
        return f"{self.transaction_id} {state} ({self.match_type})"

    @property
    def counterpart(self):
        for field in COUNTERPART_FIELDS:
            value = getattr(self, field, None)
            if value is not None:
                return value
        return None

    @property
    def counterpart_type(self) -> str:
        for field in COUNTERPART_FIELDS:
            if getattr(self, f"{field}_id", None) is not None:
                return field
        return ""
