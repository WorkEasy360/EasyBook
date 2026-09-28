"""Single-currency guard.

EasyBook has no multi-currency accounting: no exchange-rate source, no
revaluation, no realised or unrealised FX gain/loss, and document posting never
carried a document's exchange rate into its journal — a USD 1,000 invoice
posted 1,000 in the organization's base currency. Until that exists, every
transaction must be in the organization's base currency at rate 1. Enforced at
the ledger boundary (services/journals.py, services/posting.py — the one path
every posting takes) and early in each document and party service, so the user
is refused while entering the record rather than when posting it.
"""

from decimal import Decimal, InvalidOperation

from core.exceptions import ApplicationError

UNIT_RATE = Decimal("1")


def assert_base_currency(*, organization, currency=None, exchange_rate=None) -> None:
    """`currency` is a Currency, a currency id, or None when not being set;
    `exchange_rate` is None when not being set."""
    if currency is not None and getattr(currency, "pk", currency) != organization.default_currency_id:
        raise ApplicationError(
            "Only the organization's base currency is supported; multi-currency transactions are not available yet.",
            code="foreign_currency_not_supported",
        )
    if exchange_rate is not None:
        try:
            rate = Decimal(str(exchange_rate))
        except (InvalidOperation, ValueError):
            rate = None
        if rate != UNIT_RATE:
            raise ApplicationError(
                "Exchange rates other than 1 are not supported; multi-currency transactions are not available yet.",
                code="exchange_rate_not_supported",
            )
