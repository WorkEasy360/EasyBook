from django.urls import path

from accounting.api.views import (
    AccountDetailView,
    AccountLedgerView,
    AccountListCreateView,
    FiscalYearListCreateView,
    FiscalYearSetupStatusView,
    JournalEntryDetailView,
    JournalEntryListCreateView,
    JournalEntryPostView,
    JournalEntryReverseView,
    TrialBalanceView,
)

urlpatterns = [
    path("accounting/accounts/", AccountListCreateView.as_view(), name="accounting-account-list-create"),
    path("accounting/accounts/<uuid:pk>/", AccountDetailView.as_view(), name="accounting-account-detail"),
    path("accounting/accounts/<uuid:pk>/ledger/", AccountLedgerView.as_view(), name="accounting-account-ledger"),
    path("accounting/journals/", JournalEntryListCreateView.as_view(), name="accounting-journal-list-create"),
    path("accounting/journals/<uuid:pk>/", JournalEntryDetailView.as_view(), name="accounting-journal-detail"),
    path("accounting/journals/<uuid:pk>/post/", JournalEntryPostView.as_view(), name="accounting-journal-post"),
    path("accounting/journals/<uuid:pk>/reverse/", JournalEntryReverseView.as_view(), name="accounting-journal-reverse"),
    path("accounting/fiscal-years/", FiscalYearListCreateView.as_view(), name="accounting-fiscal-year-list-create"),
    path(
        "accounting/fiscal-years/setup-status/",
        FiscalYearSetupStatusView.as_view(),
        name="accounting-fiscal-year-setup-status",
    ),
    path("accounting/reports/trial-balance/", TrialBalanceView.as_view(), name="accounting-trial-balance"),
]
