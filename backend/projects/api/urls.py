from django.urls import path

from projects.api.views import (
    ProjectActivateView,
    ProjectCancelView,
    ProjectCompleteView,
    ProjectDetailView,
    ProjectHoldView,
    ProjectInvoiceTimeView,
    ProjectListCreateView,
    ProjectMemberDetailView,
    ProjectMemberListCreateView,
    ProjectProfitabilityView,
    ProjectUnbilledTimeView,
    TaskDetailView,
    TaskListCreateView,
    TimeEntryApproveView,
    TimeEntryBulkApproveView,
    TimeEntryBulkSubmitView,
    TimeEntryDetailView,
    TimeEntryListCreateView,
    TimeEntryRejectView,
    TimeEntrySubmitView,
    TimesheetView,
)

urlpatterns = [
    path("projects/", ProjectListCreateView.as_view(), name="projects-project-list-create"),
    path("projects/<uuid:pk>/", ProjectDetailView.as_view(), name="projects-project-detail"),
    path("projects/<uuid:pk>/activate/", ProjectActivateView.as_view(), name="projects-project-activate"),
    path("projects/<uuid:pk>/hold/", ProjectHoldView.as_view(), name="projects-project-hold"),
    path("projects/<uuid:pk>/complete/", ProjectCompleteView.as_view(), name="projects-project-complete"),
    path("projects/<uuid:pk>/cancel/", ProjectCancelView.as_view(), name="projects-project-cancel"),
    path(
        "projects/<uuid:pk>/profitability/", ProjectProfitabilityView.as_view(),
        name="projects-project-profitability",
    ),
    path("projects/<uuid:pk>/members/", ProjectMemberListCreateView.as_view(), name="projects-member-list-create"),
    path("projects/members/<uuid:pk>/", ProjectMemberDetailView.as_view(), name="projects-member-detail"),
    path("projects/<uuid:pk>/tasks/", TaskListCreateView.as_view(), name="projects-task-list-create"),
    path("projects/tasks/<uuid:pk>/", TaskDetailView.as_view(), name="projects-task-detail"),
    path(
        "projects/<uuid:pk>/unbilled-time/", ProjectUnbilledTimeView.as_view(),
        name="projects-project-unbilled-time",
    ),
    path("projects/<uuid:pk>/invoice-time/", ProjectInvoiceTimeView.as_view(), name="projects-project-invoice-time"),

    path("time-entries/", TimeEntryListCreateView.as_view(), name="projects-time-entry-list-create"),
    path("time-entries/bulk-submit/", TimeEntryBulkSubmitView.as_view(), name="projects-time-entry-bulk-submit"),
    path("time-entries/bulk-approve/", TimeEntryBulkApproveView.as_view(), name="projects-time-entry-bulk-approve"),
    path("time-entries/<uuid:pk>/", TimeEntryDetailView.as_view(), name="projects-time-entry-detail"),
    path("time-entries/<uuid:pk>/submit/", TimeEntrySubmitView.as_view(), name="projects-time-entry-submit"),
    path("time-entries/<uuid:pk>/approve/", TimeEntryApproveView.as_view(), name="projects-time-entry-approve"),
    path("time-entries/<uuid:pk>/reject/", TimeEntryRejectView.as_view(), name="projects-time-entry-reject"),

    path("timesheet/", TimesheetView.as_view(), name="projects-timesheet"),
]
