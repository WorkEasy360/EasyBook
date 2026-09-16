"""Rule lifecycle: create/update a rule's definition, and the explicit
DRAFT -> ACTIVE -> PAUSED -> ARCHIVED state transitions (phase sections
57-61). `_ALLOWED_TRANSITIONS` is the single source of truth for legal
status edges, same pattern as sales.services.quotes._ALLOWED_TRANSITIONS.

Every mutation is audited (audit.services.record) — activation especially,
since it is the point a rule starts actually running (phase section 59).
"""

from django.db import transaction

from audit.models import AuditLog
from audit.services import record as record_audit
from automation.actions.registry import validate_action_config
from automation.conditions.schemas import validate_condition
from automation.models.action import AutomationActionConfig
from automation.models.condition import AutomationCondition
from automation.models.rule import AutomationRule, RuleStatus
from automation.triggers.registry import get_trigger
from core.exceptions import ApplicationError

_ALLOWED_TRANSITIONS = {
    RuleStatus.DRAFT: {RuleStatus.ACTIVE, RuleStatus.ARCHIVED},
    RuleStatus.ACTIVE: {RuleStatus.PAUSED, RuleStatus.ARCHIVED},
    RuleStatus.PAUSED: {RuleStatus.ACTIVE, RuleStatus.ARCHIVED},
    RuleStatus.ARCHIVED: set(),
}


def validate_rule(*, trigger_type: str, conditions: list[dict], actions: list[dict]) -> None:
    if get_trigger(trigger_type) is None:
        raise ApplicationError(f"Unknown trigger type '{trigger_type}'.", code="automation_trigger_unknown")
    for condition in conditions:
        validate_condition(
            trigger_type=trigger_type,
            field=condition["field"],
            operator=condition["operator"],
            value=str(condition.get("value", "")),
        )
    if not actions:
        raise ApplicationError("A rule needs at least one action.", code="automation_rule_no_actions")
    for action in actions:
        validate_action_config(
            action_id=action["action_id"], trigger_type=trigger_type, config=action.get("config") or {}
        )


def _replace_conditions(*, rule: AutomationRule, conditions: list[dict]) -> None:
    rule.conditions.all().delete()
    for index, condition in enumerate(conditions):
        AutomationCondition.objects.create(
            organization=rule.organization,
            rule=rule,
            order=index,
            field=condition["field"],
            operator=condition["operator"],
            value=str(condition.get("value", "")),
        )


def _replace_actions(*, rule: AutomationRule, actions: list[dict]) -> None:
    rule.actions.all().delete()
    for index, action in enumerate(actions):
        AutomationActionConfig.objects.create(
            organization=rule.organization,
            rule=rule,
            order=index,
            action_id=action["action_id"],
            config=action.get("config") or {},
        )


@transaction.atomic
def create_rule(
    *,
    organization,
    name: str,
    trigger_type: str,
    conditions: list[dict],
    actions: list[dict],
    description: str = "",
    priority: int = 0,
    stop_on_failure: bool = False,
    max_runs_per_period: int | None = None,
    actor=None,
) -> AutomationRule:
    validate_rule(trigger_type=trigger_type, conditions=conditions, actions=actions)

    rule = AutomationRule.objects.create(
        organization=organization,
        name=name,
        description=description,
        trigger_type=trigger_type,
        status=RuleStatus.DRAFT,
        priority=priority,
        stop_on_failure=stop_on_failure,
        max_runs_per_period=max_runs_per_period,
        created_by=actor,
        updated_by=actor,
    )
    _replace_conditions(rule=rule, conditions=conditions)
    _replace_actions(rule=rule, actions=actions)

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="automation.AutomationRule",
        object_id=rule.id,
        changes={"name": name, "trigger_type": trigger_type},
    )
    return rule


@transaction.atomic
def update_rule(
    *,
    rule: AutomationRule,
    conditions: list[dict] | None = None,
    actions: list[dict] | None = None,
    actor=None,
    **fields,
) -> AutomationRule:
    if rule.status == RuleStatus.ARCHIVED:
        raise ApplicationError("Archived rules cannot be edited.", code="automation_rule_archived")

    changes = {}
    for key, value in fields.items():
        if getattr(rule, key) == value:
            continue
        changes[key] = value
        setattr(rule, key, value)

    trigger_type = fields.get("trigger_type", rule.trigger_type)
    structural_change = trigger_type != rule.trigger_type or conditions is not None or actions is not None

    if structural_change:
        effective_conditions = (
            conditions
            if conditions is not None
            else [{"field": c.field, "operator": c.operator, "value": c.value} for c in rule.conditions.all()]
        )
        effective_actions = (
            actions
            if actions is not None
            else [{"action_id": a.action_id, "config": a.config} for a in rule.actions.all()]
        )
        validate_rule(trigger_type=trigger_type, conditions=effective_conditions, actions=effective_actions)

        if conditions is not None:
            _replace_conditions(rule=rule, conditions=conditions)
            changes["condition_count"] = len(conditions)
        if actions is not None:
            _replace_actions(rule=rule, actions=actions)
            changes["action_count"] = len(actions)
        rule.version += 1
        changes["version"] = rule.version

    if changes:
        rule.updated_by = actor
        rule.save()
        record_audit(
            organization_id=rule.organization_id,
            actor=actor,
            action=AuditLog.Action.UPDATE,
            object_type="automation.AutomationRule",
            object_id=rule.id,
            changes=changes,
        )
    return rule


def _current_conditions(rule: AutomationRule) -> list[dict]:
    return [{"field": c.field, "operator": c.operator, "value": c.value} for c in rule.conditions.all()]


def _current_actions(rule: AutomationRule) -> list[dict]:
    return [{"action_id": a.action_id, "config": a.config} for a in rule.actions.all()]


def _transition(*, rule: AutomationRule, target: str, actor=None) -> AutomationRule:
    allowed = _ALLOWED_TRANSITIONS.get(RuleStatus(rule.status), set())
    if target not in allowed:
        raise ApplicationError(
            f"Cannot move rule from '{rule.status}' to '{target}'.", code="automation_rule_invalid_transition"
        )
    if target == RuleStatus.ACTIVE:
        # Re-validate on every activation, not just at create/update time —
        # a rule that was valid when saved must still be valid against the
        # current trigger/action catalogs before it is allowed to run
        # (phase section 59: "Activation should run full validation").
        validate_rule(
            trigger_type=rule.trigger_type,
            conditions=_current_conditions(rule),
            actions=_current_actions(rule),
        )

    previous = rule.status
    rule.status = target
    rule.updated_by = actor
    rule.save(update_fields=["status", "updated_by", "updated_at"])
    record_audit(
        organization_id=rule.organization_id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="automation.AutomationRule",
        object_id=rule.id,
        changes={"status": {"from": previous, "to": target}},
    )
    return rule


def activate_rule(*, rule: AutomationRule, actor=None) -> AutomationRule:
    return _transition(rule=rule, target=RuleStatus.ACTIVE, actor=actor)


def pause_rule(*, rule: AutomationRule, actor=None) -> AutomationRule:
    return _transition(rule=rule, target=RuleStatus.PAUSED, actor=actor)


def archive_rule(*, rule: AutomationRule, actor=None) -> AutomationRule:
    return _transition(rule=rule, target=RuleStatus.ARCHIVED, actor=actor)
