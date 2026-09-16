from django.db import models

from core.models import TenantScopedModel


class RuleDirection(models.TextChoices):
    ANY = "any", "Any"
    INFLOW = "inflow", "Money in"
    OUTFLOW = "outflow", "Money out"


class RuleAction(models.TextChoices):
    CATEGORIZE = "categorize", "Categorize to an account"
    EXCLUDE = "exclude", "Exclude from reconciliation"


class BankRule(TenantScopedModel):
    """A deterministic "when a statement line looks like this, do that" rule.

    WHY THE CONDITIONS ARE COLUMNS AND NOT A REGEX OR AN EXPRESSION BLOB.
    A rule engine that evaluates user-supplied regular expressions against
    every imported line is a denial-of-service waiting to happen (a
    catastrophically backtracking pattern hangs the import worker), and one
    that evaluates a stored expression language is a sandbox problem. Fixed
    columns with case-insensitive substring matching cover the cases that
    actually occur on bank narrations — "UBER", "AWS", a payroll reference —
    and they are trivially explainable to the user, which a regex is not.
    Widen this only with a concrete case a substring cannot express.

    `auto_confirm` defaults to FALSE, and that default is the point. A rule
    that fires with auto_confirm posts a journal without a human in the loop;
    that is legitimate and useful for the tenth identical cloud-hosting
    charge, but it must be something the user deliberately switches on for a
    rule they trust, not something they discover after the fact in their P&L.
    """

    name = models.CharField(max_length=255)
    # Lower runs first. Only the first matching rule applies — see
    # services/rules.py for why rules do not compose.
    priority = models.PositiveIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    # Null means "every account in this organization".
    bank_account = models.ForeignKey(
        "banking.BankAccount", null=True, blank=True, on_delete=models.CASCADE, related_name="rules"
    )

    # ---- conditions (all populated ones must hold; empty ones are ignored)
    description_contains = models.CharField(max_length=255, blank=True)
    counterparty_contains = models.CharField(max_length=255, blank=True)
    direction = models.CharField(max_length=8, choices=RuleDirection.choices, default=RuleDirection.ANY)
    # Compared against the ABSOLUTE amount, so a user writing a rule for
    # "card charges over 5000" does not have to reason about the sign
    # convention to get it right.
    amount_min = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    amount_max = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)

    # ---- action
    action = models.CharField(max_length=16, choices=RuleAction.choices, default=RuleAction.CATEGORIZE)
    # The account the other side of the journal goes to. Required for
    # CATEGORIZE, meaningless for EXCLUDE — validated in services/rules.py
    # rather than by constraint, so the error carries a usable message.
    target_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    vendor = models.ForeignKey(
        "purchases.Vendor", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    customer = models.ForeignKey(
        "sales.Customer", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    auto_confirm = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "name"], name="uniq_bank_rule_name_per_org"),
            models.CheckConstraint(
                condition=(
                    models.Q(amount_min__isnull=True)
                    | models.Q(amount_max__isnull=True)
                    | models.Q(amount_max__gte=models.F("amount_min"))
                ),
                name="bank_rule_amount_range_ordered",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(amount_min__isnull=True) | models.Q(amount_min__gte=0)
                ),
                name="bank_rule_amount_min_non_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "is_active", "priority"]),
        ]
        ordering = ["priority", "name"]

    def __str__(self):
        return self.name
