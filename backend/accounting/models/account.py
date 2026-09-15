from django.db import models

from core.models import TenantScopedModel


class AccountType(models.TextChoices):
    ASSET = "asset", "Asset"
    LIABILITY = "liability", "Liability"
    EQUITY = "equity", "Equity"
    INCOME = "income", "Income"
    EXPENSE = "expense", "Expense"


DEBIT_NORMAL_TYPES = {AccountType.ASSET, AccountType.EXPENSE}
CREDIT_NORMAL_TYPES = {AccountType.LIABILITY, AccountType.EQUITY, AccountType.INCOME}


class Account(TenantScopedModel):
    """A node in the organization's Chart of Accounts.

    Postings never reference a balance stored here — see accounting/CLAUDE.md.
    `is_system` marks accounts the platform itself depends on (e.g. an opening
    balance equity account); services.accounts enforces stronger mutation
    restrictions on those, see accounting/services/accounts.py.
    """

    code = models.CharField(max_length=32)
    name = models.CharField(max_length=255)
    account_type = models.CharField(max_length=16, choices=AccountType.choices)
    account_subtype = models.CharField(max_length=64, blank=True)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )
    is_active = models.BooleanField(default=True)
    is_system = models.BooleanField(default=False)
    description = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], name="uniq_account_code_per_org"),
        ]
        indexes = [
            models.Index(fields=["organization", "account_type"]),
            models.Index(fields=["organization", "parent"]),
        ]
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} {self.name}"

    @property
    def is_debit_normal(self) -> bool:
        return self.account_type in DEBIT_NORMAL_TYPES
