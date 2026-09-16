from django.urls import path

from ai.api.views import (
    AskView,
    ConversationDetailView,
    ConversationListView,
    DocumentIndexView,
    DocumentReindexView,
    ExpenseAccountSuggestionView,
    PaymentReminderDraftView,
    UsageView,
)

urlpatterns = [
    path("ai/ask/", AskView.as_view(), name="ai-ask"),
    path("ai/conversations/", ConversationListView.as_view(), name="ai-conversation-list"),
    path("ai/conversations/<uuid:pk>/", ConversationDetailView.as_view(), name="ai-conversation-detail"),
    path("ai/documents/<uuid:pk>/index/", DocumentIndexView.as_view(), name="ai-document-index"),
    path("ai/documents/<uuid:pk>/reindex/", DocumentReindexView.as_view(), name="ai-document-reindex"),
    path("ai/usage/", UsageView.as_view(), name="ai-usage"),
    path("ai/drafts/payment-reminder/", PaymentReminderDraftView.as_view(), name="ai-draft-payment-reminder"),
    path("ai/suggestions/expense-account/", ExpenseAccountSuggestionView.as_view(), name="ai-suggest-expense-account"),
]
