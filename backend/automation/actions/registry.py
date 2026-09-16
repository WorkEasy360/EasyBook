"""Action catalog (phase sections 20-22).

Slice 1 scope: identifier, safety level, allowed triggers and a minimal
config schema, enough to validate an `AutomationActionConfig` at rule
save/activate time. Execution handlers (the code that actually calls a
domain service to perform the action) are wired in a later slice
(automation/engine/execution.py) — nothing here executes anything.

`register()` refuses safety_level >= 3: Level 3 (post invoice, record
payment, reconcile, submit GST, transfer stock, send payment — phase
section 4) must never be reachable from this registry in Phase 11, so the
refusal is structural rather than a runtime flag that could be flipped.
"""

from dataclasses import dataclass, field

from core.exceptions import ApplicationError

SAFETY_LEVEL_LOW_RISK = 1
SAFETY_LEVEL_CONTROLLED_MUTATION = 2


@dataclass(frozen=True)
class ActionDefinition:
    id: str
    label: str
    safety_level: int
    # {config_key: "string"} — minimal type declarations, checked below.
    config_schema: dict[str, str] = field(default_factory=dict)
    # Subset of config_schema that must be present. A schema key not listed
    # here is optional — allowed if provided (and type-checked), omittable
    # otherwise (e.g. send_notification's recipient_id).
    required_config_keys: frozenset[str] = field(default_factory=frozenset)
    # Empty means "any trigger type may use this action".
    allowed_trigger_types: frozenset[str] = field(default_factory=frozenset)


_ACTIONS: dict[str, ActionDefinition] = {}


def register(action: ActionDefinition) -> ActionDefinition:
    if action.safety_level >= 3:
        raise ValueError(
            f"Automation action '{action.id}' declares safety_level {action.safety_level}; "
            "Level 3 (high-risk financial) actions may not be registered in Phase 11."
        )
    if action.id in _ACTIONS:
        raise ValueError(f"Duplicate automation action: {action.id}")
    _ACTIONS[action.id] = action
    return action


def get_action(action_id: str) -> ActionDefinition | None:
    return _ACTIONS.get(action_id)


def all_actions() -> list[ActionDefinition]:
    return sorted(_ACTIONS.values(), key=lambda action: action.id)


def validate_action_config(*, action_id: str, trigger_type: str, config: dict) -> None:
    action = get_action(action_id)
    if action is None:
        raise ApplicationError(f"Unknown automation action '{action_id}'.", code="automation_action_unknown")
    if action.allowed_trigger_types and trigger_type not in action.allowed_trigger_types:
        raise ApplicationError(
            f"Action '{action_id}' is not compatible with trigger '{trigger_type}'.",
            code="automation_action_trigger_incompatible",
        )
    if not isinstance(config, dict):
        raise ApplicationError("Action configuration must be an object.", code="automation_action_config_invalid")

    unknown = set(config) - set(action.config_schema)
    if unknown:
        raise ApplicationError(
            f"Unknown configuration keys for action '{action_id}': {', '.join(sorted(unknown))}.",
            code="automation_action_config_invalid",
        )
    for key in action.required_config_keys:
        if key not in config:
            raise ApplicationError(
                f"Action '{action_id}' requires '{key}' in its configuration.",
                code="automation_action_config_invalid",
            )
    for key, value in config.items():
        expected_type = action.config_schema[key]
        if expected_type == "string" and not isinstance(value, str):
            raise ApplicationError(f"'{key}' must be a string.", code="automation_action_config_invalid")

    if action_id == "call_webhook" and "url" in config:
        from automation.actions.webhook_security import validate_webhook_url

        validate_webhook_url(config["url"])


register(
    ActionDefinition(
        id="send_notification",
        label="Send in-app notification",
        safety_level=SAFETY_LEVEL_LOW_RISK,
        config_schema={"message": "string", "recipient_id": "string"},
        required_config_keys=frozenset({"message"}),
    )
)
register(
    ActionDefinition(
        id="generate_report",
        label="Generate report",
        safety_level=SAFETY_LEVEL_LOW_RISK,
        config_schema={"report_type": "string"},
        required_config_keys=frozenset({"report_type"}),
    )
)
register(
    ActionDefinition(
        id="draft_payment_reminder",
        label="Draft AI payment reminder (never sent automatically)",
        safety_level=SAFETY_LEVEL_LOW_RISK,
        config_schema={},
        allowed_trigger_types=frozenset({"invoice.overdue"}),
    )
)
register(
    ActionDefinition(
        id="call_webhook",
        label="Call outbound webhook",
        safety_level=SAFETY_LEVEL_LOW_RISK,
        # `secret` (optional) signs the payload (HMAC-SHA256) but is never
        # returned by the API once set — see api/serializers.py masking.
        config_schema={"url": "string", "secret": "string"},  # nosec B105 -- type-name literal, not a credential
        required_config_keys=frozenset({"url"}),
    )
)
