"""Trigger catalog (phase section 8, 20, 64).

Slice 1 scope: this only proves a `trigger_type` is a recognized identifier,
so a rule can't be created or activated against a nonexistent trigger.
Actually dispatching domain events into this catalog (event emission,
outbox, schedule evaluation) is Slice 2+ (automation/engine/, tasks) and
does not change these identifiers.
"""

from dataclasses import dataclass

CATEGORY_EVENT = "event"
CATEGORY_SCHEDULE = "schedule"
CATEGORY_MANUAL = "manual"


@dataclass(frozen=True)
class TriggerDefinition:
    id: str
    label: str
    category: str


_TRIGGERS: dict[str, TriggerDefinition] = {}


def register(trigger: TriggerDefinition) -> TriggerDefinition:
    if trigger.id in _TRIGGERS:
        raise ValueError(f"Duplicate automation trigger: {trigger.id}")
    _TRIGGERS[trigger.id] = trigger
    return trigger


def get_trigger(trigger_id: str) -> TriggerDefinition | None:
    return _TRIGGERS.get(trigger_id)


def all_triggers() -> list[TriggerDefinition]:
    return sorted(_TRIGGERS.values(), key=lambda trigger: trigger.id)


_DEFAULT_TRIGGERS = (
    TriggerDefinition("invoice.posted", "Invoice posted", CATEGORY_EVENT),
    TriggerDefinition("invoice.overdue", "Invoice overdue", CATEGORY_SCHEDULE),
    TriggerDefinition("invoice.paid", "Invoice paid", CATEGORY_EVENT),
    TriggerDefinition("bill.posted", "Bill posted", CATEGORY_EVENT),
    TriggerDefinition("bill.overdue", "Bill overdue", CATEGORY_SCHEDULE),
    TriggerDefinition("payment.received", "Payment received", CATEGORY_EVENT),
    TriggerDefinition("stock.low", "Stock below reorder level", CATEGORY_SCHEDULE),
    TriggerDefinition("document.ocr_completed", "Document OCR completed", CATEGORY_EVENT),
    TriggerDefinition("schedule.daily", "Daily schedule", CATEGORY_SCHEDULE),
    TriggerDefinition("schedule.weekly", "Weekly schedule", CATEGORY_SCHEDULE),
    TriggerDefinition("schedule.monthly", "Monthly schedule", CATEGORY_SCHEDULE),
    TriggerDefinition("manual", "Manual run", CATEGORY_MANUAL),
)
for _trigger in _DEFAULT_TRIGGERS:
    register(_trigger)
