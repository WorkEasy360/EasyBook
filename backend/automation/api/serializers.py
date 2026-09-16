from rest_framework import serializers

from automation.models.action import AutomationActionConfig
from automation.models.condition import AutomationCondition
from automation.models.rule import AutomationRule
from automation.services.rules import create_rule, update_rule


class AutomationConditionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AutomationCondition
        fields = ["field", "operator", "value"]


class AutomationActionConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = AutomationActionConfig
        fields = ["action_id", "config"]


class AutomationRuleSerializer(serializers.ModelSerializer):
    conditions = AutomationConditionSerializer(many=True, read_only=True)
    actions = AutomationActionConfigSerializer(many=True, read_only=True)

    class Meta:
        model = AutomationRule
        fields = [
            "id", "name", "description", "trigger_type", "status", "version", "priority",
            "stop_on_failure", "max_runs_per_period", "conditions", "actions",
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
