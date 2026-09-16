from django.db import models

from core.models import TenantScopedModel


class BankAccountKind(models.TextChoices):
    BANK = "bank", "Bank account"
    CREDIT_CARD = "credit_card", "Credit card"


class BankAccount(TenantScopedModel):
    """A real-world account at a financial institution, paired 1:1 with the
    `accounting.Account` that carries its ledger balance.

    This model is NOT the balance. It holds what the BANK knows (institution,
    masked number, statement feed configuration) while the GL account holds
    what the BOOKS know. Reconciliation is precisely the exercise of comparing
    the two, so collapsing them into one model would leave nothing to compare.

    SIGN CONVENTION — the single rule the rest of this app depends on.
    `BankTransaction.amount` is signed from the account holder's point of
    view: positive means money arrived, negative means money left. That maps
    onto the ledger as *positive debits the GL account, negative credits it*,
    and — this is why the convention was chosen — that one rule is correct for
    both kinds with no special case. A bank account is an ASSET, so a deposit
    debits it upward. A credit card is a LIABILITY, so a repayment (positive:
    money arrived in the card account) debits it downward, which is exactly
    right. A card purchase is negative and credits the liability upward.

    NUMBER STORAGE. Only the last four digits are kept, never the full
    account or card number (root CLAUDE.md: never store raw card
    credentials). Four digits is what a human needs to tell two accounts
    apart on screen, and it is the whole of what this product ever needs:
    nothing here initiates a payment, so there is no workflow that would
    require the full number. `branch_identifier` (IFSC/sort code/routing
    number) is public reference data about the BRANCH, not about the account,
    and is safe to hold.
    """

    kind = models.CharField(max_length=16, choices=BankAccountKind.choices, default=BankAccountKind.BANK)
    name = models.CharField(max_length=255)
    # OneToOne, not FK: two bank accounts sharing one GL account would make
    # every reconciliation permanently unexplainable, because the book side
    # would carry the other account's movements as well.
    account = models.OneToOneField(
        "accounting.Account", on_delete=models.PROTECT, related_name="bank_account"
    )
    currency = models.ForeignKey("accounts.Currency", on_delete=models.PROTECT, related_name="+")
    bank_name = models.CharField(max_length=255, blank=True)
    account_number_last4 = models.CharField(max_length=4, blank=True)
    branch_identifier = models.CharField(max_length=32, blank=True)

    # The STATEMENT balance on the day before we began importing — the anchor
    # every derived statement figure is measured from. Deliberately distinct
    # from the GL opening balance, which is
    # `accounting.services.opening_balances`' job: the two are different
    # numbers whenever the account was mid-reconciliation at go-live, and
    # conflating them is what makes a first reconciliation impossible to
    # close.
    opening_balance = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    opening_balance_date = models.DateField(null=True, blank=True)

    # Which feed supplies statements. `manual` is the only provider that
    # exists — see banking/providers/base.py for why no live-feed vendor is
    # implemented here.
    provider_key = models.CharField(max_length=32, default="manual")
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "name"], name="uniq_bank_account_name_per_org"),
        ]
        indexes = [
            models.Index(fields=["organization", "is_active"]),
        ]
        ordering = ["name"]

    def __str__(self):
        # The masked digits are deliberately NOT included: __str__ lands in
        # admin listings, error pages and log lines (root CLAUDE.md: do not
        # leak data into logs), and the name alone identifies the account.
        return self.name

    @property
    def is_credit_card(self) -> bool:
        return self.kind == BankAccountKind.CREDIT_CARD

    @property
    def masked_number(self) -> str:
        return f"••••{self.account_number_last4}" if self.account_number_last4 else ""
