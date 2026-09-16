from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView

from authz.permissions import HasOrgPermission
from authz.roles import Permission
from automation.actions.registry import all_actions
from automation.api.serializers import (
    AutomationRuleCreateSerializer,
    AutomationRuleSerializer,
    AutomationRuleUpdateSerializer,
)
from automation.models.rule import AutomationRule
from automation.services.rules import activate_rule, archive_rule, pause_rule
from automation.triggers.registry import all_triggers
from core.exceptions import ApplicationError
from core.views import OrganizationScopedMixin


class AutomationRuleListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    permission_classes = [HasOrgPermission]

    @property
    def required_permission(self):
        return Permission.VIEW_AUTOMATION if self.request.method == "GET" else Permission.CREATE_AUTOMATION

    def get_serializer_class(self):
        return AutomationRuleCreateSerializer if self.request.method == "POST" else AutomationRuleSerializer

    def get_queryset(self):
        qs = AutomationRule.objects.prefetch_related("conditions", "actions")
        status_filter = self.request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        trigger_type = self.request.query_params.get("trigger_type")
        if trigger_type:
            qs = qs.filter(trigger_type=trigger_type)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rule = serializer.save()
        return Response(AutomationRuleSerializer(rule).data, status=201)


class AutomationRuleDetailView(OrganizationScopedMixin, generics.RetrieveUpdateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = AutomationRuleSerializer

    @property
    def required_permission(self):
        return Permission.VIEW_AUTOMATION if self.request.method == "GET" else Permission.EDIT_AUTOMATION

    def get_queryset(self):
        return AutomationRule.objects.prefetch_related("conditions", "actions")

    def update(self, request, *args, **kwargs):
        rule = self.get_object()
        serializer = AutomationRuleUpdateSerializer(
            data=request.data, partial=True, context={"request": request, "rule": rule}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        rule.refresh_from_db()
        return Response(AutomationRuleSerializer(rule).data)


class _AutomationRuleTransitionView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    transition = staticmethod(lambda **kwargs: None)

    def post(self, request, pk):
        rule = AutomationRule.objects.filter(pk=pk).first()
        if rule is None:
            raise ApplicationError("Automation rule not found.", code="automation_rule_not_found", status_code=404)
        updated = self.transition(rule=rule, actor=request.user)
        return Response(AutomationRuleSerializer(updated).data)


class AutomationRuleActivateView(_AutomationRuleTransitionView):
    required_permission = Permission.ENABLE_AUTOMATION
    transition = staticmethod(activate_rule)


class AutomationRulePauseView(_AutomationRuleTransitionView):
    required_permission = Permission.DISABLE_AUTOMATION
    transition = staticmethod(pause_rule)


class AutomationRuleArchiveView(_AutomationRuleTransitionView):
    required_permission = Permission.EDIT_AUTOMATION
    transition = staticmethod(archive_rule)


class TriggerCatalogView(OrganizationScopedMixin, APIView):
    """Structured trigger metadata for a future workflow builder (phase
    section 65) — identifiers/labels only, never internal Python paths."""

    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_AUTOMATION

    def get(self, request):
        return Response(
            [{"id": t.id, "label": t.label, "category": t.category} for t in all_triggers()]
        )


class ActionCatalogView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_AUTOMATION

    def get(self, request):
        return Response(
            [
                {
                    "id": a.id,
                    "label": a.label,
                    "safety_level": a.safety_level,
                    "config_schema": a.config_schema,
                    "allowed_trigger_types": sorted(a.allowed_trigger_types),
                }
                for a in all_actions()
            ]
        )
