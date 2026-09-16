from django.urls import path

from banking.api.views import (
    BankAccountDetailView,
    BankAccountListCreateView,
    BankAccountSummaryView,
    BankReconciliationAbandonView,
    BankReconciliationCompleteView,
    BankReconciliationDetailView,
    BankReconciliationListCreateView,
    BankReconciliationReopenView,
    BankReconciliationSummaryView,
    BankRuleDetailView,
    BankRuleListCreateView,
    BankTransactionCreateView,
    BankTransactionDetailView,
    BankTransactionExcludeView,
    BankTransactionListView,
    BankTransactionRestoreView,
    BankTransferListCreateView,
    BankTransferVoidView,
    ConfirmTransferPairView,
    MatchConfirmView,
    MatchDeleteView,
    StatementImportListCreateView,
    TransactionApplyRulesView,
    TransactionAutoMatchView,
    TransactionCategorizeView,
    TransactionMatchCreateView,
    TransactionSuggestionsView,
    TransactionUncategorizeView,
    TransferCandidatesView,
)

urlpatterns = [
    path("bank-accounts/", BankAccountListCreateView.as_view(), name="banking-account-list-create"),
    path("bank-accounts/<uuid:pk>/", BankAccountDetailView.as_view(), name="banking-account-detail"),
    path("bank-accounts/<uuid:pk>/summary/", BankAccountSummaryView.as_view(), name="banking-account-summary"),
    path(
        "bank-accounts/<uuid:pk>/statement-imports/",
        StatementImportListCreateView.as_view(),
        name="banking-statement-import-list-create",
    ),
    path(
        "bank-accounts/<uuid:pk>/transactions/",
        BankTransactionCreateView.as_view(),
        name="banking-transaction-create",
    ),

    path("bank-transactions/", BankTransactionListView.as_view(), name="banking-transaction-list"),
    path(
        "bank-transactions/<uuid:pk>/", BankTransactionDetailView.as_view(), name="banking-transaction-detail"
    ),
    path(
        "bank-transactions/<uuid:pk>/exclude/",
        BankTransactionExcludeView.as_view(),
        name="banking-transaction-exclude",
    ),
    path(
        "bank-transactions/<uuid:pk>/restore/",
        BankTransactionRestoreView.as_view(),
        name="banking-transaction-restore",
    ),
    path(
        "bank-transactions/<uuid:pk>/suggestions/",
        TransactionSuggestionsView.as_view(),
        name="banking-transaction-suggestions",
    ),
    path(
        "bank-transactions/<uuid:pk>/auto-match/",
        TransactionAutoMatchView.as_view(),
        name="banking-transaction-auto-match",
    ),
    path(
        "bank-transactions/<uuid:pk>/matches/",
        TransactionMatchCreateView.as_view(),
        name="banking-transaction-match-create",
    ),
    path(
        "bank-transactions/<uuid:pk>/categorize/",
        TransactionCategorizeView.as_view(),
        name="banking-transaction-categorize",
    ),
    path(
        "bank-transactions/<uuid:pk>/transfer-candidates/",
        TransferCandidatesView.as_view(),
        name="banking-transaction-transfer-candidates",
    ),
    path(
        "bank-transactions/<uuid:pk>/apply-rules/",
        TransactionApplyRulesView.as_view(),
        name="banking-transaction-apply-rules",
    ),

    path("bank-matches/<uuid:pk>/confirm/", MatchConfirmView.as_view(), name="banking-match-confirm"),
    path("bank-matches/<uuid:pk>/", MatchDeleteView.as_view(), name="banking-match-delete"),
    path(
        "bank-matches/<uuid:pk>/uncategorize/",
        TransactionUncategorizeView.as_view(),
        name="banking-match-uncategorize",
    ),

    path("bank-transfers/", BankTransferListCreateView.as_view(), name="banking-transfer-list-create"),
    path("bank-transfers/<uuid:pk>/void/", BankTransferVoidView.as_view(), name="banking-transfer-void"),
    path(
        "bank-transfers/confirm-pair/", ConfirmTransferPairView.as_view(), name="banking-transfer-confirm-pair"
    ),

    path("bank-rules/", BankRuleListCreateView.as_view(), name="banking-rule-list-create"),
    path("bank-rules/<uuid:pk>/", BankRuleDetailView.as_view(), name="banking-rule-detail"),

    path(
        "bank-reconciliations/",
        BankReconciliationListCreateView.as_view(),
        name="banking-reconciliation-list-create",
    ),
    path(
        "bank-reconciliations/<uuid:pk>/",
        BankReconciliationDetailView.as_view(),
        name="banking-reconciliation-detail",
    ),
    path(
        "bank-reconciliations/<uuid:pk>/summary/",
        BankReconciliationSummaryView.as_view(),
        name="banking-reconciliation-summary",
    ),
    path(
        "bank-reconciliations/<uuid:pk>/complete/",
        BankReconciliationCompleteView.as_view(),
        name="banking-reconciliation-complete",
    ),
    path(
        "bank-reconciliations/<uuid:pk>/reopen/",
        BankReconciliationReopenView.as_view(),
        name="banking-reconciliation-reopen",
    ),
    path(
        "bank-reconciliations/<uuid:pk>/abandon/",
        BankReconciliationAbandonView.as_view(),
        name="banking-reconciliation-abandon",
    ),
]
