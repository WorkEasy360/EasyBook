"""Condition evaluation against freshly reloaded, authoritative data (phase
section 16) — never against the event payload alone, and never eval()'d:
every operator here is explicit Python comparison code keyed off the fixed
`Operator` constants, on values cast per the field's declared type
(automation/conditions/schemas.py) — Decimal for money-shaped fields, never
float (root CLAUDE.md).
"""

import datetime
from decimal import Decimal

from automation.conditions.operators import Operator
from automation.conditions.schemas import (
    FIELD_TYPE_DATE,
    FIELD_TYPE_DECIMAL,
    FIELD_TYPE_INT,
    allowed_fields,
)


def _cast(value, field_type: str):
    if field_type == FIELD_TYPE_DECIMAL:
        return Decimal(str(value))
    if field_type == FIELD_TYPE_INT:
        return int(value)
    if field_type == FIELD_TYPE_DATE:
        if isinstance(value, datetime.datetime):
            return value.date()
        if isinstance(value, datetime.date):
            return value
        return datetime.date.fromisoformat(str(value))
    return str(value)


def evaluate_condition(*, trigger_type: str, condition, facts: dict) -> bool:
    """`condition` exposes `.field`/`.operator`/`.value` (an
    AutomationCondition row, or an equivalent frozen snapshot object used by
    the execution engine). `facts` is a plain {field: value} dict built by a
    trigger-specific fact builder (automation/triggers/facts.py)."""
    field_type = allowed_fields(trigger_type)[condition.field]
    raw = facts.get(condition.field)

    if condition.operator == Operator.IS_EMPTY:
        return raw is None or raw == ""
    if condition.operator == Operator.IS_NOT_EMPTY:
        return raw is not None and raw != ""
    if raw is None:
        return False

    if condition.operator == Operator.CONTAINS:
        return condition.value in str(raw)
    if condition.operator == Operator.IN:
        return str(raw) in [item.strip() for item in condition.value.split(",")]

    actual = _cast(raw, field_type)
    expected = _cast(condition.value, field_type)
    if condition.operator == Operator.EQUALS:
        return actual == expected
    if condition.operator == Operator.NOT_EQUALS:
        return actual != expected
    if condition.operator == Operator.GREATER_THAN:
        return actual > expected
    if condition.operator == Operator.GREATER_THAN_OR_EQUAL:
        return actual >= expected
    if condition.operator == Operator.LESS_THAN:
        return actual < expected
    if condition.operator == Operator.LESS_THAN_OR_EQUAL:
        return actual <= expected
    raise ValueError(f"Unhandled automation condition operator '{condition.operator}'.")


def evaluate_conditions(*, trigger_type: str, conditions, facts: dict) -> bool:
    """AND-only (phase sections 15-16) — no OR/grouping in Phase 11. An empty
    condition list means the rule always matches once triggered."""
    return all(evaluate_condition(trigger_type=trigger_type, condition=c, facts=facts) for c in conditions)
