from automation.models.rule import AutomationRule, RuleStatus


def get_active_rules_for_trigger(*, trigger_type: str):
    """Current-tenant + trigger_type + ACTIVE only (phase section 79) — the
    query the evaluation engine (a later slice) will run per event/schedule
    tick. Never load every rule in the system."""
    return (
        AutomationRule.objects.filter(status=RuleStatus.ACTIVE, trigger_type=trigger_type)
        .prefetch_related("conditions", "actions")
        .order_by("-priority", "name")
    )
