"""Bank feed abstraction.

WHAT IS HERE AND WHAT IS DELIBERATELY NOT.

The interface below is the seam a live bank feed would plug into. There is
exactly one implementation — `ManualImportProvider`, which is the honest
description of "a human downloads a file and uploads it".

No Plaid / Salt Edge / Yodlee / account-aggregator provider is implemented,
and that is a decision rather than a gap. Root CLAUDE.md is explicit: do not
invent APIs, and verify version-sensitive third-party behaviour against
official documentation before implementing. Writing a provider against an
aggregator API from memory would produce code that looks finished, passes
tests written from the same memory, and fails on first contact with the real
service — the exact "fabricated third-party integration" the build rules
forbid. Every one of those services also requires credentials and a
commercial agreement that do not exist here.

So the seam is real and the contract is small, and adding a provider later is
a new class plus a settings entry, not a refactor of the import pipeline.
Anything that DOES get written against a live API must be built from that
provider's current official documentation, with its credentials in the secret
store (never the repository), and with `fetch_transactions` returning the same
`ParsedTransaction` records the CSV parser already produces — so the dedupe,
rule and matching layers stay unchanged and untrusted-input-safe.
"""

import abc
import datetime
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class ParsedTransaction:
    """One statement line, normalized, before it becomes a BankTransaction.

    The single currency in which every parser and every provider speaks, so
    that duplicate detection, rules and matching have exactly one input shape
    to reason about. `amount` follows the app-wide sign convention: positive
    money in, negative money out (see banking/models/bank_account.py).
    """

    transaction_date: datetime.date
    amount: Decimal
    description: str = ""
    counterparty_name: str = ""
    bank_reference: str = ""
    external_id: str = ""
    raw: dict = field(default_factory=dict)


class BankFeedProvider(abc.ABC):
    """A source of statement lines for one bank account."""

    key: str = ""

    @abc.abstractmethod
    def fetch_transactions(
        self, *, bank_account, since: datetime.date | None = None, until: datetime.date | None = None
    ) -> list[ParsedTransaction]:
        """Return statement lines for the period, normalized.

        Implementations must NOT write to the database. Persistence, duplicate
        detection and rule application belong to services/imports.py, so that
        every provider inherits the same guarantees rather than each one
        reimplementing (and each one getting duplicate handling subtly wrong).
        """
        raise NotImplementedError

    def supports_pull(self) -> bool:
        """False for providers that can only receive pushed/uploaded data."""
        return True


_REGISTRY: dict[str, BankFeedProvider] = {}


def register_provider(provider: BankFeedProvider) -> None:
    _REGISTRY[provider.key] = provider


def get_provider(key: str) -> BankFeedProvider | None:
    return _REGISTRY.get(key)


def available_providers() -> list[str]:
    return sorted(_REGISTRY)
