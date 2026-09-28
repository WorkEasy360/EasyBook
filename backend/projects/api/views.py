"""Projects API views.

OBJECT-LEVEL AUTHORIZATION, the thing this module has that sales/purchases
do not: a time entry belongs to a PERSON, not just an organization. Holding
LOG_TIME lets you record your own hours; it must not let you read or edit a
colleague's, because an entry carries `cost_rate` — an indirect read on what
that person is paid. `VIEW_ALL_TIMESHEETS` is the permission that widens the
view, and `_scope_time_entries` is the single place the narrowing happens.
"""

import datetime
import uuid
from decimal import Decimal

from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView

from authz.permissions import HasOrgPermission
from authz.roles import Permission, role_has_permission
from core.exceptions import ApplicationError
from core.views import OrganizationScopedMixin
from projects.api.serializers import (
    InvoiceProjectTimeSerializer,
    ProjectCreateSerializer,
    ProjectMemberCreateSerializer,
    ProjectMemberSerializer,
    ProjectMemberUpdateSerializer,
    ProjectSerializer,
    ProjectUpdateSerializer,
    TaskCreateSerializer,
    TaskSerializer,
    TaskUpdateSerializer,
    TimeEntryBulkSerializer,
    TimeEntryCreateSerializer,
    TimeEntryRejectSerializer,
    TimeEntrySerializer,
    TimeEntryUpdateSerializer,
    TimesheetQuerySerializer,
    _get_org_user_or_404,
)
from projects.models.project import Project, ProjectMember, Task
from projects.models.time_entry import TimeEntry
from projects.selectors import get_project_profitability, get_user_timesheet
from projects.services.billing import get_unbilled_time
from projects.services.projects import (
    activate_project,
    cancel_project,
    complete_project,
    hold_project,
)
from projects.services.time_entries import (
    approve_time_entry,
    bulk_approve,
    bulk_submit,
    delete_time_entry,
    reject_time_entry,
    submit_time_entry,
)
from sales.api.serializers import InvoiceSerializer


def _can_see_all_timesheets(request) -> bool:
    return role_has_permission(request.membership.role, Permission.VIEW_ALL_TIMESHEETS)


def _scope_time_entries(request, qs):
    """Narrows a time-entry queryset to the caller's own entries unless they
    hold VIEW_ALL_TIMESHEETS. The ONE place this rule is applied."""
    if _can_see_all_timesheets(request):
        return qs
    return qs.filter(user=request.user)


def _get_project_or_404(pk) -> Project:
    project = Project.objects.filter(pk=pk).first()
    if project is None:
        raise ApplicationError("Project not found.", code="project_not_found", status_code=404)
    return project


# ------------------------------------------------------------ projects



def _query_date(request, name: str):
    """A YYYY-MM-DD query parameter, None when absent, 400 when malformed."""
    value = request.query_params.get(name)
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise ApplicationError(f"Invalid {name} '{value}', expected YYYY-MM-DD.", code="invalid_date")

class ProjectListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    permission_classes = [HasOrgPermission]

    @property
    def required_permission(self):
        return Permission.VIEW_PROJECTS if self.request.method == "GET" else Permission.MANAGE_PROJECTS

    def get_serializer_class(self):
        return ProjectCreateSerializer if self.request.method == "POST" else ProjectSerializer

    def get_queryset(self):
        qs = Project.objects.all()
        status_filter = self.request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        customer_id = self.request.query_params.get("customer")
        if customer_id:
            qs = qs.filter(customer_id=customer_id)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(ProjectSerializer(serializer.save()).data, status=201)


class ProjectDetailView(OrganizationScopedMixin, generics.RetrieveUpdateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = ProjectSerializer

    @property
    def required_permission(self):
        return Permission.VIEW_PROJECTS if self.request.method == "GET" else Permission.MANAGE_PROJECTS

    def get_queryset(self):
        return Project.objects.all()

    def update(self, request, *args, **kwargs):
        project = self.get_object()
        serializer = ProjectUpdateSerializer(
            data=request.data, partial=True, context={"request": request, "project": project}
        )
        serializer.is_valid(raise_exception=True)
        return Response(ProjectSerializer(serializer.save()).data)


class _ProjectTransitionView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.MANAGE_PROJECTS
    transition = staticmethod(lambda **kwargs: None)

    def post(self, request, pk):
        project = self.transition(project_id=pk, organization=request.organization, actor=request.user)
        return Response(ProjectSerializer(project).data)


class ProjectActivateView(_ProjectTransitionView):
    transition = staticmethod(activate_project)


class ProjectHoldView(_ProjectTransitionView):
    transition = staticmethod(hold_project)


class ProjectCompleteView(_ProjectTransitionView):
    transition = staticmethod(complete_project)


class ProjectCancelView(_ProjectTransitionView):
    transition = staticmethod(cancel_project)


class ProjectProfitabilityView(OrganizationScopedMixin, APIView):
    """Revenue, cost and margin, derived on demand. Read-only and never
    cached — see projects/selectors.py."""

    permission_classes = [HasOrgPermission]
    # Margin exposes cost rates, so this needs the wider timesheet permission
    # rather than plain VIEW_PROJECTS.
    required_permission = Permission.VIEW_ALL_TIMESHEETS

    def get(self, request, pk):
        report = get_project_profitability(project=_get_project_or_404(pk))
        # Decimal -> str, never float. DRF's JSON encoder turns a bare Decimal
        # into a float, so returning the selector dict as-is put money on the
        # wire as `5850.0` — the same figures reports/projects/profitability
        # already sends as strings (reports.selectors.params.money).
        return Response({key: str(value) if isinstance(value, Decimal) else value for key, value in report.items()})


# ------------------------------------------------------------- members


class ProjectMemberListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    permission_classes = [HasOrgPermission]

    @property
    def required_permission(self):
        return Permission.VIEW_PROJECTS if self.request.method == "GET" else Permission.MANAGE_PROJECTS

    def get_serializer_class(self):
        return ProjectMemberCreateSerializer if self.request.method == "POST" else ProjectMemberSerializer

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "project": _get_project_or_404(self.kwargs["pk"])}

    def get_queryset(self):
        return ProjectMember.objects.filter(project_id=self.kwargs["pk"]).select_related("user")

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(ProjectMemberSerializer(serializer.save()).data, status=201)


class ProjectMemberDetailView(OrganizationScopedMixin, generics.RetrieveUpdateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = ProjectMemberSerializer

    @property
    def required_permission(self):
        return Permission.VIEW_PROJECTS if self.request.method == "GET" else Permission.MANAGE_PROJECTS

    def get_queryset(self):
        return ProjectMember.objects.select_related("user")

    def update(self, request, *args, **kwargs):
        member = self.get_object()
        serializer = ProjectMemberUpdateSerializer(
            data=request.data, partial=True, context={"request": request, "member": member}
        )
        serializer.is_valid(raise_exception=True)
        return Response(ProjectMemberSerializer(serializer.save()).data)


# --------------------------------------------------------------- tasks


class TaskListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    permission_classes = [HasOrgPermission]

    @property
    def required_permission(self):
        return Permission.VIEW_PROJECTS if self.request.method == "GET" else Permission.MANAGE_PROJECTS

    def get_serializer_class(self):
        return TaskCreateSerializer if self.request.method == "POST" else TaskSerializer

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "project": _get_project_or_404(self.kwargs["pk"])}

    def get_queryset(self):
        qs = Task.objects.filter(project_id=self.kwargs["pk"])
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() == "true")
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(TaskSerializer(serializer.save()).data, status=201)


class TaskDetailView(OrganizationScopedMixin, generics.RetrieveUpdateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = TaskSerializer

    @property
    def required_permission(self):
        return Permission.VIEW_PROJECTS if self.request.method == "GET" else Permission.MANAGE_PROJECTS

    def get_queryset(self):
        return Task.objects.all()

    def update(self, request, *args, **kwargs):
        task = self.get_object()
        serializer = TaskUpdateSerializer(
            data=request.data, partial=True, context={"request": request, "task": task}
        )
        serializer.is_valid(raise_exception=True)
        return Response(TaskSerializer(serializer.save()).data)


# -------------------------------------------------------- time entries


class TimeEntryListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    permission_classes = [HasOrgPermission]

    @property
    def required_permission(self):
        # Reading is gated on LOG_TIME, not a VIEW_ permission: everyone who
        # can record time can read back what they recorded, and
        # _scope_time_entries decides how much of it they see.
        return Permission.LOG_TIME

    def get_serializer_class(self):
        return TimeEntryCreateSerializer if self.request.method == "POST" else TimeEntrySerializer

    def get_queryset(self):
        qs = TimeEntry.objects.select_related("project", "task")
        # Each filter is validated before it reaches the ORM: a malformed id or
        # date raised Django's ValidationError inside the query and returned a
        # 500 instead of telling the caller which parameter was wrong.
        project_id = self.request.query_params.get("project")
        if project_id:
            try:
                uuid.UUID(project_id)
            except ValueError:
                raise ApplicationError("project must be a valid id.", code="invalid_project_id")
            qs = qs.filter(project_id=project_id)
        status_filter = self.request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        from_date = _query_date(self.request, "from_date")
        if from_date:
            qs = qs.filter(entry_date__gte=from_date)
        to_date = _query_date(self.request, "to_date")
        if to_date:
            qs = qs.filter(entry_date__lte=to_date)
        return _scope_time_entries(self.request, qs)

    def create(self, request, *args, **kwargs):
        # Logging time FOR someone else is a supervisory act, so it needs the
        # wider permission. Without this check, LOG_TIME alone would let
        # anyone attribute hours to a colleague.
        target_user_id = request.data.get("user_id")
        if target_user_id and str(target_user_id) != str(request.user.id):
            if not _can_see_all_timesheets(request):
                raise ApplicationError(
                    "You may only log time for yourself.", code="log_time_for_others_forbidden", status_code=403
                )
            _get_org_user_or_404(target_user_id, request.organization)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(TimeEntrySerializer(serializer.save()).data, status=201)


class TimeEntryDetailView(OrganizationScopedMixin, generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = TimeEntrySerializer
    required_permission = Permission.LOG_TIME

    def get_queryset(self):
        return _scope_time_entries(self.request, TimeEntry.objects.select_related("project", "task"))

    def update(self, request, *args, **kwargs):
        entry = self.get_object()
        serializer = TimeEntryUpdateSerializer(
            data=request.data, partial=True, context={"request": request, "entry": entry}
        )
        serializer.is_valid(raise_exception=True)
        return Response(TimeEntrySerializer(serializer.save()).data)

    def destroy(self, request, *args, **kwargs):
        entry = self.get_object()
        delete_time_entry(entry_id=entry.id, organization=request.organization, actor=request.user)
        return Response(status=204)


class TimeEntrySubmitView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.LOG_TIME

    def post(self, request, pk):
        # Scoped so a contributor cannot submit someone else's entry.
        if not _scope_time_entries(request, TimeEntry.objects.filter(pk=pk)).exists():
            raise ApplicationError("Time entry not found.", code="time_entry_not_found", status_code=404)
        entry = submit_time_entry(entry_id=pk, organization=request.organization, actor=request.user)
        return Response(TimeEntrySerializer(entry).data)


class TimeEntryApproveView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.APPROVE_TIME

    def post(self, request, pk):
        entry = approve_time_entry(entry_id=pk, organization=request.organization, actor=request.user)
        return Response(TimeEntrySerializer(entry).data)


class TimeEntryRejectView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.APPROVE_TIME

    def post(self, request, pk):
        serializer = TimeEntryRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry = reject_time_entry(
            entry_id=pk, organization=request.organization, actor=request.user,
            reason=serializer.validated_data["reason"],
        )
        return Response(TimeEntrySerializer(entry).data)


class TimeEntryBulkSubmitView(OrganizationScopedMixin, APIView):
    """Submitting a week at once — atomic, so one bad entry rolls the whole
    submission back rather than leaving half a timesheet submitted."""

    permission_classes = [HasOrgPermission]
    required_permission = Permission.LOG_TIME

    def post(self, request):
        serializer = TimeEntryBulkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry_ids = serializer.validated_data["entry_ids"]
        visible = set(
            _scope_time_entries(request, TimeEntry.objects.filter(pk__in=entry_ids))
            .values_list("id", flat=True)
        )
        if len(visible) != len(set(entry_ids)):
            raise ApplicationError(
                "One or more time entries were not found.", code="time_entry_not_found", status_code=404
            )
        entries = bulk_submit(organization=request.organization, entry_ids=entry_ids, actor=request.user)
        return Response(TimeEntrySerializer(entries, many=True).data)


class TimeEntryBulkApproveView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.APPROVE_TIME

    def post(self, request):
        serializer = TimeEntryBulkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entries = bulk_approve(
            organization=request.organization,
            entry_ids=serializer.validated_data["entry_ids"],
            actor=request.user,
        )
        return Response(TimeEntrySerializer(entries, many=True).data)


class TimesheetView(OrganizationScopedMixin, APIView):
    """One person's entries over a date range. Defaults to the caller; naming
    another user requires VIEW_ALL_TIMESHEETS."""

    permission_classes = [HasOrgPermission]
    required_permission = Permission.LOG_TIME

    def get(self, request):
        query = TimesheetQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        data = query.validated_data

        user = request.user
        if data["user_id"] and str(data["user_id"]) != str(request.user.id):
            if not _can_see_all_timesheets(request):
                raise ApplicationError(
                    "You may only view your own timesheet.", code="timesheet_forbidden", status_code=403
                )
            user = _get_org_user_or_404(data["user_id"], request.organization)

        entries = get_user_timesheet(
            organization=request.organization, user=user,
            from_date=data["from_date"], to_date=data["to_date"],
        )
        return Response(TimeEntrySerializer(entries, many=True).data)


# ------------------------------------------------------------- billing


class ProjectUnbilledTimeView(OrganizationScopedMixin, APIView):
    """Preview of exactly what `invoice` would bill."""

    permission_classes = [HasOrgPermission]
    required_permission = Permission.INVOICE_TIME

    def get(self, request, pk):
        up_to_date = request.query_params.get("up_to_date") or None
        entries = get_unbilled_time(project=_get_project_or_404(pk), up_to_date=up_to_date)
        return Response(TimeEntrySerializer(entries, many=True).data)


class ProjectInvoiceTimeView(OrganizationScopedMixin, APIView):
    """Creates a DRAFT sales invoice from approved billable time. The invoice
    is posted through the sales API like any other — this endpoint never
    posts accounting itself."""

    permission_classes = [HasOrgPermission]
    required_permission = Permission.INVOICE_TIME

    def post(self, request, pk):
        project = _get_project_or_404(pk)
        serializer = InvoiceProjectTimeSerializer(
            data=request.data, context={"request": request, "project": project}
        )
        serializer.is_valid(raise_exception=True)
        return Response(InvoiceSerializer(serializer.save()).data, status=201)
