import uuid

from django.db import transaction
from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import AuditLog
from audit.services import record as record_audit
from authz.permissions import HasOrgPermission
from authz.roles import Permission
from automation.actions.registry import all_actions
from automation.api.serializers import (
    AutomationExecutionSerializer,
    AutomationRuleCreateSerializer,
    AutomationRuleSerializer,
    AutomationRuleUpdateSerializer,
)
from automation.models.execution import AutomationExecution
from automation.models.rule import AutomationRule, RuleStatus
from automation.services.execution import create_manual_execution, retry_execution
from automation.services.rules import activate_rule, archive_rule, pause_rule
from automation.triggers.facts import trigger_requires_entity
from automation.triggers.registry import all_triggers
from core.exceptions import ApplicationError
from core.views import OrganizationScopedMixin


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return True

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


class AutomationRuleRunView(OrganizationScopedMixin, APIView):
    """Manual trigger (phase section 8). Enqueues execution rather than
    running it inline in the request thread (phase section 13), and only once
    the request's transaction commits: enqueued earlier, a worker can pick the
    message up before the execution row is visible, skip it as not found, and
    leave it PENDING forever. The response therefore reports it PENDING."""

    permission_classes = [HasOrgPermission]
    required_permission = Permission.RUN_AUTOMATION

    def post(self, request, pk):
        from automation.tasks import run_execution_task

        rule = AutomationRule.objects.filter(pk=pk).first()
        if rule is None:
            raise ApplicationError("Automation rule not found.", code="automation_rule_not_found", status_code=404)
        if rule.status != RuleStatus.ACTIVE:
            raise ApplicationError("Only an ACTIVE rule can be run manually.", code="automation_rule_not_active")

        # Validated BEFORE the execution exists. A record-based trigger run
        # without a usable entity id used to create the execution and enqueue
        # it; the worker's pk lookup then raised and left it pending forever.
        entity_id = request.data.get("entity_id") if hasattr(request.data, "get") else None
        if entity_id and not _is_uuid(str(entity_id)):
            raise ApplicationError("entity_id must be a valid id.", code="invalid_entity_id")
        if not entity_id and trigger_requires_entity(rule.trigger_type):
            raise ApplicationError(
                f"A '{rule.trigger_type}' rule needs the entity_id of the record to run against.",
                code="entity_id_required",
            )

        execution = create_manual_execution(rule=rule, actor=request.user)
        if entity_id:
            execution.entity_id = str(entity_id)
            execution.save(update_fields=["entity_id"])

        record_audit(
            organization_id=request.organization.id, actor=request.user, action=AuditLog.Action.CREATE,
            object_type="automation.AutomationExecution", object_id=execution.id,
            changes={"trigger_source": "manual", "rule_id": str(rule.id)},
        )
        execution_id, organization_id = str(execution.id), str(request.organization.id)
        transaction.on_commit(lambda: run_execution_task.delay(execution_id, organization_id))
        execution.refresh_from_db()
        return Response(AutomationExecutionSerializer(execution).data, status=201)


class AutomationExecutionListView(OrganizationScopedMixin, generics.ListAPIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_AUTOMATION_HISTORY
    serializer_class = AutomationExecutionSerializer

    def get_queryset(self):
        qs = AutomationExecution.objects.select_related("rule").prefetch_related("steps")
        rule_id = self.request.query_params.get("rule")
        if rule_id:
            # A non-UUID made the filter raise Django's ValidationError -> 500.
            if not _is_uuid(rule_id):
                raise ApplicationError("rule must be a valid id.", code="invalid_rule_id")
            qs = qs.filter(rule_id=rule_id)
        status_filter = self.request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        return qs


class AutomationExecutionDetailView(OrganizationScopedMixin, generics.RetrieveAPIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_AUTOMATION_HISTORY
    serializer_class = AutomationExecutionSerializer

    def get_queryset(self):
        return AutomationExecution.objects.select_related("rule").prefetch_related("steps")


class AutomationExecutionRetryView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.RETRY_AUTOMATION

    def post(self, request, pk):
        from automation.tasks import run_execution_task

        execution = AutomationExecution.objects.filter(pk=pk).first()
        if execution is None:
            raise ApplicationError(
                "Automation execution not found.", code="automation_execution_not_found", status_code=404
            )
        retry_execution(execution, actor=request.user)
        record_audit(
            organization_id=request.organization.id, actor=request.user, action=AuditLog.Action.UPDATE,
            object_type="automation.AutomationExecution", object_id=execution.id, changes={"action": "manual_retry"},
        )
        # After commit, for the same reason as AutomationRuleRunView: enqueued
        # earlier, a worker can still see the pre-retry FAILED state.
        execution_id, organization_id = str(execution.id), str(request.organization.id)
        transaction.on_commit(lambda: run_execution_task.delay(execution_id, organization_id))
        execution.refresh_from_db()
        return Response(AutomationExecutionSerializer(execution).data)


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
