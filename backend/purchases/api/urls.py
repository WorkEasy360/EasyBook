from django.urls import path

from purchases.api.views import (
    BillDetailView,
    BillListCreateView,
    BillMatchView,
    BillPostView,
    BillVoidView,
    ExpenseDetailView,
    ExpenseListCreateView,
    ExpensePostView,
    ExpenseVoidView,
    GoodsReceiptCancelView,
    GoodsReceiptConvertToBillView,
    GoodsReceiptDetailView,
    GoodsReceiptListCreateView,
    GoodsReceiptReceiveView,
    PurchaseOrderApproveView,
    PurchaseOrderCancelView,
    PurchaseOrderCloseView,
    PurchaseOrderDetailView,
    PurchaseOrderListCreateView,
    PurchaseOrderMatchView,
    RecurringBillTemplateActivateView,
    RecurringBillTemplateDeactivateView,
    RecurringBillTemplateDetailView,
    RecurringBillTemplateListCreateView,
    RecurringExpenseTemplateActivateView,
    RecurringExpenseTemplateDeactivateView,
    RecurringExpenseTemplateDetailView,
    RecurringExpenseTemplateListCreateView,
    VendorCreditDetailView,
    VendorCreditIssueView,
    VendorCreditListCreateView,
    VendorCreditVoidView,
    VendorDetailView,
    VendorListCreateView,
    VendorPaymentDetailView,
    VendorPaymentListCreateView,
)

urlpatterns = [
    path("purchases/vendors/", VendorListCreateView.as_view(), name="purchases-vendor-list-create"),
    path("purchases/vendors/<uuid:pk>/", VendorDetailView.as_view(), name="purchases-vendor-detail"),

    path("purchases/orders/", PurchaseOrderListCreateView.as_view(), name="purchases-order-list-create"),
    path("purchases/orders/<uuid:pk>/", PurchaseOrderDetailView.as_view(), name="purchases-order-detail"),
    path("purchases/orders/<uuid:pk>/approve/", PurchaseOrderApproveView.as_view(), name="purchases-order-approve"),
    path("purchases/orders/<uuid:pk>/cancel/", PurchaseOrderCancelView.as_view(), name="purchases-order-cancel"),
    path("purchases/orders/<uuid:pk>/close/", PurchaseOrderCloseView.as_view(), name="purchases-order-close"),
    path("purchases/orders/<uuid:pk>/match/", PurchaseOrderMatchView.as_view(), name="purchases-order-match"),

    path("purchases/goods-receipts/", GoodsReceiptListCreateView.as_view(), name="purchases-goods-receipt-list-create"),
    path(
        "purchases/goods-receipts/<uuid:pk>/", GoodsReceiptDetailView.as_view(),
        name="purchases-goods-receipt-detail",
    ),
    path(
        "purchases/goods-receipts/<uuid:pk>/receive/", GoodsReceiptReceiveView.as_view(),
        name="purchases-goods-receipt-receive",
    ),
    path(
        "purchases/goods-receipts/<uuid:pk>/cancel/", GoodsReceiptCancelView.as_view(),
        name="purchases-goods-receipt-cancel",
    ),
    path(
        "purchases/goods-receipts/<uuid:pk>/convert-to-bill/", GoodsReceiptConvertToBillView.as_view(),
        name="purchases-goods-receipt-convert-to-bill",
    ),

    path("purchases/bills/", BillListCreateView.as_view(), name="purchases-bill-list-create"),
    path("purchases/bills/<uuid:pk>/", BillDetailView.as_view(), name="purchases-bill-detail"),
    path("purchases/bills/<uuid:pk>/post/", BillPostView.as_view(), name="purchases-bill-post"),
    path("purchases/bills/<uuid:pk>/void/", BillVoidView.as_view(), name="purchases-bill-void"),
    path("purchases/bills/<uuid:pk>/match/", BillMatchView.as_view(), name="purchases-bill-match"),

    path("purchases/expenses/", ExpenseListCreateView.as_view(), name="purchases-expense-list-create"),
    path("purchases/expenses/<uuid:pk>/", ExpenseDetailView.as_view(), name="purchases-expense-detail"),
    path("purchases/expenses/<uuid:pk>/post/", ExpensePostView.as_view(), name="purchases-expense-post"),
    path("purchases/expenses/<uuid:pk>/void/", ExpenseVoidView.as_view(), name="purchases-expense-void"),

    path("purchases/payments/", VendorPaymentListCreateView.as_view(), name="purchases-payment-list-create"),
    path("purchases/payments/<uuid:pk>/", VendorPaymentDetailView.as_view(), name="purchases-payment-detail"),

    path("purchases/vendor-credits/", VendorCreditListCreateView.as_view(), name="purchases-vendor-credit-list-create"),
    path(
        "purchases/vendor-credits/<uuid:pk>/", VendorCreditDetailView.as_view(),
        name="purchases-vendor-credit-detail",
    ),
    path(
        "purchases/vendor-credits/<uuid:pk>/issue/", VendorCreditIssueView.as_view(),
        name="purchases-vendor-credit-issue",
    ),
    path(
        "purchases/vendor-credits/<uuid:pk>/void/", VendorCreditVoidView.as_view(),
        name="purchases-vendor-credit-void",
    ),

    path(
        "purchases/recurring-bills/", RecurringBillTemplateListCreateView.as_view(),
        name="purchases-recurring-bill-list-create",
    ),
    path(
        "purchases/recurring-bills/<uuid:pk>/", RecurringBillTemplateDetailView.as_view(),
        name="purchases-recurring-bill-detail",
    ),
    path(
        "purchases/recurring-bills/<uuid:pk>/activate/", RecurringBillTemplateActivateView.as_view(),
        name="purchases-recurring-bill-activate",
    ),
    path(
        "purchases/recurring-bills/<uuid:pk>/deactivate/", RecurringBillTemplateDeactivateView.as_view(),
        name="purchases-recurring-bill-deactivate",
    ),

    path(
        "purchases/recurring-expenses/", RecurringExpenseTemplateListCreateView.as_view(),
        name="purchases-recurring-expense-list-create",
    ),
    path(
        "purchases/recurring-expenses/<uuid:pk>/", RecurringExpenseTemplateDetailView.as_view(),
        name="purchases-recurring-expense-detail",
    ),
    path(
        "purchases/recurring-expenses/<uuid:pk>/activate/", RecurringExpenseTemplateActivateView.as_view(),
        name="purchases-recurring-expense-activate",
    ),
    path(
        "purchases/recurring-expenses/<uuid:pk>/deactivate/", RecurringExpenseTemplateDeactivateView.as_view(),
        name="purchases-recurring-expense-deactivate",
    ),
]
