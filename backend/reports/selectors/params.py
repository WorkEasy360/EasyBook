"""Shared parsing/formatting helpers for report selectors and views.

Report endpoints share a common parameter vocabulary (root CLAUDE.md /
PHASE 8 spec §16): from_date/to_date/as_of_date/comparison_from/
comparison_to. This module is the one place that parses and formats them so
every report behaves identically for invalid input and variance edge cases.
"""

import datetime
from decimal import Decimal

from core.exceptions import ApplicationError

ZERO = Decimal("0")


def parse_date(value, *, param_name="date"):
    """Parses a YYYY-MM-DD string. Returns None for an absent value; raises
    ApplicationError (400) for a present-but-invalid one — never silently
    falls back to "today" for a malformed value, which would misreport a
    typo'd filter as an unfiltered report."""
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise ApplicationError(
            f"Invalid {param_name} '{value}', expected YYYY-MM-DD.", code="invalid_date"
        )


def money(value: Decimal) -> str:
    """Decimal -> string for JSON responses. Never float (root CLAUDE.md)."""
    return str(value if value is not None else ZERO)


def to_json_safe(value):
    """Recursively converts Decimal -> str (never float — root CLAUDE.md)
    through a nested dict/list report result, and stringifies dict keys (a
    GST register groups rows by GSTIN/state/UUID keys — JSON object keys
    must be strings, unlike Python dict keys). `datetime.date` values pass
    through unchanged; DRF's renderer already serializes those."""
    if isinstance(value, Decimal):
        return money(value)
    if isinstance(value, dict):
        return {str(key): to_json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_json_safe(item) for item in value]
    return value


def compute_variance(*, current: Decimal, previous: Decimal) -> dict:
    """current vs previous, plus percentage variance with an explicit
    divide-by-zero policy (PHASE 8 spec §17): never Infinity/NaN.

    previous == 0, current == 0 -> 0% (no change).
    previous == 0, current != 0 -> None (undefined: cannot express "from
    zero" as a percentage; the frontend renders this as "n/a", never 0% or
    an error).
    """
    current = current if current is not None else ZERO
    previous = previous if previous is not None else ZERO
    variance = current - previous
    if previous == ZERO:
        percent = Decimal("0") if current == ZERO else None
    else:
        percent = (variance / previous) * Decimal("100")
    return {
        "current": money(current),
        "previous": money(previous),
        "variance": money(variance),
        "variance_percent": money(percent) if percent is not None else None,
    }
