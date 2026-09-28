from rest_framework import serializers

from automation.models.action import AutomationActionConfig
from automation.models.condition import AutomationCondition
from automation.models.execution import AutomationExecution
from automation.models.rule import AutomationRule
from automation.models.step_execution import AutomationStepExecution
from automation.services.rules import create_rule, update_rule


def _mask_webhook_secret(action_id, config):
    """The single masking rule for a webhook action's config, wherever it is
    serialized. A rule's live config and an execution's config SNAPSHOT carry
    the same secret; masking only the former leaked it through run history to
    every role that can view history, while configuring webhooks is limited to
    MANAGE_AUTOMATION_WEBHOOKS."""
    if action_id == "call_webhook" and isinstance(config, dict) and config.get("secret"):
        return {**config, "secret": "********"}  # nosec B105 -- masking placeholder, not a credential
    return config


class AutomationConditionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AutomationCondition
        fields = ["field", "operator", "value"]


class AutomationActionConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = AutomationActionConfig
        fields = ["action_id", "config"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # A webhook secret is write-only: never returned once set (phase
        # section 89) — no secret-storage architecture exists in this
        # codebase yet, so masking the API response is the control until
        # one does.
        data["config"] = _mask_webhook_secret(data.get("action_id"), data.get("config"))
        return data


class AutomationRuleSerializer(serializers.ModelSerializer):
    conditions = AutomationConditionSerializer(many=True, read_only=True)
    actions = AutomationActionConfigSerializer(many=True, read_only=True)

    class Meta:
        model = AutomationRule
        fields = [
            "id", "name", "description", "trigger_type", "status", "version", "priority",
            "stop_on_failure", "max_runs_per_period", "cooldown_days", "conditions", "actions",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "status", "version", "conditions", "actions", "created_at", "updated_at"]


class AutomationConditionInputSerializer(serializers.Serializer):
    field = serializers.CharField()
    operator = serializers.CharField()
    value = serializers.CharField(required=False, allow_blank=True, default="")


class AutomationActionConfigInputSerializer(serializers.Serializer):
    action_id = serializers.CharField()
    config = serializers.DictField(required=False, default=dict)


class AutomationRuleCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    trigger_type = serializers.CharField(max_length=64)
    priority = serializers.IntegerField(required=False, default=0, min_value=0)
    stop_on_failure = serializers.BooleanField(required=False, default=False)
    max_runs_per_period = serializers.IntegerField(required=False, allow_null=True, default=None, min_value=1)
    cooldown_days = serializers.IntegerField(required=False, allow_null=True, default=None, min_value=0)
    conditions = AutomationConditionInputSerializer(many=True, required=False, default=list)
    actions = AutomationActionConfigInputSerializer(many=True)

    def create(self, validated_data):
        request = self.context["request"]
        return create_rule(organization=request.organization, actor=request.user, **validated_data)


class AutomationRuleUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    priority = serializers.IntegerField(required=False, min_value=0)
    stop_on_failure = serializers.BooleanField(required=False)
    max_runs_per_period = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    cooldown_days = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    conditions = AutomationConditionInputSerializer(many=True, required=False)
    actions = AutomationActionConfigInputSerializer(many=True, required=False)

    def save(self, **kwargs):
        request = self.context["request"]
        data = dict(self.validated_data)
        conditions = data.pop("conditions", None)
        actions = data.pop("actions", None)
        return update_rule(
            rule=self.context["rule"], conditions=conditions, actions=actions, actor=request.user, **data
        )


class AutomationStepExecutionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AutomationStepExecution
        fields = [
            "id", "order", "action_id", "config_snapshot", "status", "attempt",
            "failure_category", "error_message", "result", "started_at", "finished_at",
        ]
        read_only_fields = fields

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["config_snapshot"] = _mask_webhook_secret(data.get("action_id"), data.get("config_snapshot"))
        return data


class AutomationExecutionSerializer(serializers.ModelSerializer):
    steps = AutomationStepExecutionSerializer(many=True, read_only=True)

    class Meta:
        model = AutomationExecution
        fields = [
            "id", "rule", "rule_version", "trigger_source", "entity_id", "status",
            "causation_id", "correlation_id", "depth", "initiated_by", "executed_as",
            "started_at", "finished_at", "attempt_count", "error_summary", "steps",
            "created_at",
        ]
        read_only_fields = fields
