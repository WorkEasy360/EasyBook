"""Enumerations shared across more than one domain module.

Lives in `core` for the same reason `core/money.py` does: these are values
with no single owning domain, and a peer-to-peer import (purchases -> sales)
to reach one would couple two modules at the same layer, while a second copy
would drift (root CLAUDE.md module dependency rule and "avoid duplicated
enumerations"). Only put something here once a SECOND module genuinely needs
it — an enum used by one app belongs in that app.
"""

from django.db import models


class PaymentMethod(models.TextChoices):
    """How money moved. Identical on both sides of the ledger — a cheque is a
    cheque whether we write it or receive it — so sales, purchases and
    (later) banking share this one catalog.
    """

    CASH = "cash", "Cash"
    BANK_TRANSFER = "bank_transfer", "Bank Transfer"
    CHEQUE = "cheque", "Cheque"
    CARD = "card", "Card"
    UPI = "upi", "UPI"
    OTHER = "other", "Other"


class RecurringFrequency(models.TextChoices):
    """How often a scheduled document regenerates. Shared by sales
    (RecurringInvoiceTemplate) and purchases (RecurringBillTemplate,
    RecurringExpenseTemplate) — "monthly" means the same thing on both
    sides, and the period-advance arithmetic that reads it is shared too.
    """

    WEEKLY = "weekly", "Weekly"
    MONTHLY = "monthly", "Monthly"
    QUARTERLY = "quarterly", "Quarterly"
    YEARLY = "yearly", "Yearly"
