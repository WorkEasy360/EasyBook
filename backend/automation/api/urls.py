from django.urls import path

from automation.api.views import (
    ActionCatalogView,
    AutomationExecutionDetailView,
    AutomationExecutionListView,
    AutomationExecutionRetryView,
    AutomationRuleActivateView,
    AutomationRuleArchiveView,
    AutomationRuleDetailView,
    AutomationRuleListCreateView,
    AutomationRulePauseView,
    AutomationRuleRunView,
    TriggerCatalogView,
)

urlpatterns = [
    path("automation/rules/", AutomationRuleListCreateView.as_view(), name="automation-rule-list-create"),
    path("automation/rules/<uuid:pk>/", AutomationRuleDetailView.as_view(), name="automation-rule-detail"),
    path(
        "automation/rules/<uuid:pk>/activate/", AutomationRuleActivateView.as_view(), name="automation-rule-activate"
    ),
    path("automation/rules/<uuid:pk>/pause/", AutomationRulePauseView.as_view(), name="automation-rule-pause"),
    path("automation/rules/<uuid:pk>/archive/", AutomationRuleArchiveView.as_view(), name="automation-rule-archive"),
    path("automation/rules/<uuid:pk>/run/", AutomationRuleRunView.as_view(), name="automation-rule-run"),
    path("automation/executions/", AutomationExecutionListView.as_view(), name="automation-execution-list"),
    path(
        "automation/executions/<uuid:pk>/", AutomationExecutionDetailView.as_view(), name="automation-execution-detail"
    ),
    path(
        "automation/executions/<uuid:pk>/retry/", AutomationExecutionRetryView.as_view(),
        name="automation-execution-retry",
    ),
    path("automation/catalog/triggers/", TriggerCatalogView.as_view(), name="automation-catalog-triggers"),
    path("automation/catalog/actions/", ActionCatalogView.as_view(), name="automation-catalog-actions"),
]
