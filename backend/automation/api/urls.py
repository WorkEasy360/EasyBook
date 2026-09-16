from django.urls import path

from automation.api.views import (
    ActionCatalogView,
    AutomationRuleActivateView,
    AutomationRuleArchiveView,
    AutomationRuleDetailView,
    AutomationRuleListCreateView,
    AutomationRulePauseView,
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
    path("automation/catalog/triggers/", TriggerCatalogView.as_view(), name="automation-catalog-triggers"),
    path("automation/catalog/actions/", ActionCatalogView.as_view(), name="automation-catalog-actions"),
]
