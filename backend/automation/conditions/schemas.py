"""Per-trigger field allowlist (phase section 17): a condition may only
reference a field this module explicitly declares for that trigger type —
never an arbitrary model attribute. Each field also declares its comparison
type so values are validated (and, in the later-slice evaluator, compared)
as Decimal/date/str rather than guessed at read time.
"""

import datetime
from decimal import Decimal, InvalidOperation

from automation.conditions.operators import (
    ALL_OPERATORS,
    NO_VALUE_OPERATORS,
    ORDERED_OPERATORS,
    TEXT_ONLY_OPERATORS,
)
from core.exceptions import ApplicationError

FIELD_TYPE_DECIMAL = "decimal"
FIELD_TYPE_STRING = "string"
FIELD_TYPE_DATE = "date"
FIELD_TYPE_INT = "int"

# trigger_type -> {field_name: field_type}. Empty dict means the trigger
# (schedule/manual) carries no entity fields to condition on.
TRIGGER_FIELDS: dict[str, dict[str, str]] = {
    "invoice.posted": {
        "amount_due": FIELD_TYPE_DECIMAL,
        "status": FIELD_TYPE_STRING,
        "customer_id": FIELD_TYPE_STRING,
    },
    "invoice.overdue": {
        "amount_due": FIELD_TYPE_DECIMAL,
        "days_overdue": FIELD_TYPE_INT,
        "customer_id": FIELD_TYPE_STRING,
    },
    "invoice.paid": {
        "amount_due": FIELD_TYPE_DECIMAL,
        "customer_id": FIELD_TYPE_STRING,
    },
    "bill.posted": {
        "amount_due": FIELD_TYPE_DECIMAL,
        "vendor_id": FIELD_TYPE_STRING,
    },
    "bill.overdue": {
        "amount_due": FIELD_TYPE_DECIMAL,
        "days_until_due": FIELD_TYPE_INT,
        "vendor_id": FIELD_TYPE_STRING,
    },
    "payment.received": {
        "amount": FIELD_TYPE_DECIMAL,
        "customer_id": FIELD_TYPE_STRING,
    },
    "stock.low": {
        "quantity_on_hand": FIELD_TYPE_DECIMAL,
        "reorder_level": FIELD_TYPE_DECIMAL,
        "item_id": FIELD_TYPE_STRING,
    },
    "document.ocr_completed": {
        "ocr_status": FIELD_TYPE_STRING,
        "document_id": FIELD_TYPE_STRING,
    },
    "schedule.daily": {},
    "schedule.weekly": {},
    "schedule.monthly": {},
    "manual": {},
}


def allowed_fields(trigger_type: str) -> dict[str, str]:
    if trigger_type not in TRIGGER_FIELDS:
        raise ApplicationError(f"Unknown trigger type '{trigger_type}'.", code="automation_trigger_unknown")
    return TRIGGER_FIELDS[trigger_type]


def _check_value_type(*, field_type: str, value: str) -> None:
    if field_type == FIELD_TYPE_DECIMAL:
        try:
            Decimal(value)
        except (InvalidOperation, ValueError):
            raise ApplicationError(f"Value '{value}' is not a valid decimal.", code="automation_condition_value_invalid")
    elif field_type == FIELD_TYPE_INT:
        try:
            int(value)
        except ValueError:
            raise ApplicationError(f"Value '{value}' is not a valid integer.", code="automation_condition_value_invalid")
    elif field_type == FIELD_TYPE_DATE:
        try:
            datetime.date.fromisoformat(value)
        except ValueError:
            raise ApplicationError(f"Value '{value}' is not a valid ISO date.", code="automation_condition_value_invalid")


def validate_condition(*, trigger_type: str, field: str, operator: str, value: str) -> None:
    fields = allowed_fields(trigger_type)
    if field not in fields:
        raise ApplicationError(
            f"Field '{field}' is not allowed for trigger '{trigger_type}'.", code="automation_condition_field_invalid"
        )
    if operator not in ALL_OPERATORS:
        raise ApplicationError(
            f"Unknown condition operator '{operator}'.", code="automation_condition_operator_invalid"
        )

    field_type = fields[field]
    if operator in NO_VALUE_OPERATORS:
        return
    if operator in ORDERED_OPERATORS and field_type not in (FIELD_TYPE_DECIMAL, FIELD_TYPE_INT, FIELD_TYPE_DATE):
        raise ApplicationError(
            f"Operator '{operator}' cannot be used on field '{field}'.", code="automation_condition_operator_invalid"
        )
    if operator in TEXT_ONLY_OPERATORS and field_type != FIELD_TYPE_STRING:
        raise ApplicationError(
            f"Operator '{operator}' cannot be used on field '{field}'.", code="automation_condition_operator_invalid"
        )
    if not value:
        raise ApplicationError(f"Operator '{operator}' requires a value.", code="automation_condition_value_invalid")
    if operator not in TEXT_ONLY_OPERATORS:
        _check_value_type(field_type=field_type, value=value)
