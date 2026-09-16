"""Occurrence-date arithmetic for scheduled documents.

Shared by sales (recurring invoices) and purchases (recurring bills and
expenses). Lives in `core` for the same reason `core/money.py` does: it is
pure calendar arithmetic over `core.enums.RecurringFrequency` with no domain
concepts in it, and a second copy would be one more place for "what does
monthly mean on the 31st" to be answered differently.
"""

import datetime

from dateutil.relativedelta import relativedelta

from core.enums import RecurringFrequency
from core.exceptions import ApplicationError


def advance_occurrence(occurrence_date: datetime.date, frequency: str) -> datetime.date:
    """The next occurrence after `occurrence_date`.

    Monthly/quarterly/yearly use `relativedelta`, which clamps rather than
    overflows: 31 Jan + 1 month is 28/29 Feb, not 3 March. That is the
    behaviour a billing schedule wants, and it is why this is not plain
    `timedelta` arithmetic.
    """
    if frequency == RecurringFrequency.WEEKLY:
        return occurrence_date + datetime.timedelta(weeks=1)
    if frequency == RecurringFrequency.MONTHLY:
        return occurrence_date + relativedelta(months=1)
    if frequency == RecurringFrequency.QUARTERLY:
        return occurrence_date + relativedelta(months=3)
    if frequency == RecurringFrequency.YEARLY:
        return occurrence_date + relativedelta(years=1)
    raise ApplicationError(f"Unknown recurrence frequency '{frequency}'.", code="recurring_frequency_invalid")
