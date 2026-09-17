"""Purchases API views.

Every queryset is built in `get_queryset()`, never as a bare
`queryset = Model.objects.all()` class attribute — a class-level queryset is
evaluated at import time, before tenant context exists, and permanently
regresses tenant scoping (see core/CLAUDE.md).

Detail views split `required_permission` by HTTP method via a `@property`,
so a viewer holding only VIEW_X can still retrieve a single record.
"""

import hashlib
import json
from decimal import Decimal

from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView

from authz.permissions import HasOrgPermission
from authz.roles import Permission
from core.exceptions import ApplicationError
from core.models import IdempotencyKey
from core.views import OrganizationScopedMixin
from purchases.api.serializers import (
    BillCreateSerializer,
    BillFromGoodsReceiptSerializer,
    BillLinesUpdateSerializer,
    BillSerializer,
    BillVoidSerializer,
    ExpenseCreateSerializer,
    ExpenseSerializer,
    ExpenseUpdateSerializer,
    ExpenseVoidSerializer,
    GoodsReceiptCreateSerializer,
    GoodsReceiptLinesUpdateSerializer,
    GoodsReceiptSerializer,
    PurchaseOrderCreateSerializer,
    PurchaseOrderLinesUpdateSerializer,
    PurchaseOrderSerializer,
    RecurringBillTemplateCreateSerializer,
    RecurringBillTemplateSerializer,
    RecurringBillTemplateUpdateSerializer,
    RecurringExpenseTemplateCreateSerializer,
    RecurringExpenseTemplateSerializer,
    RecurringExpenseTemplateUpdateSerializer,
    ThreeWayMatchQuerySerializer,
    VendorCreditCreateSerializer,
    VendorCreditLinesUpdateSerializer,
    VendorCreditSerializer,
    VendorPaymentCreateSerializer,
    VendorPaymentSerializer,
    VendorSerializer,
)
from purchases.models.bill import Bill, BillStatus
from purchases.models.expense import Expense, ExpenseStatus
from purchases.models.goods_receipt import GoodsReceipt, GoodsReceiptStatus
from purchases.models.payment import VendorPayment
from purchases.models.purchase_order import PurchaseOrder, PurchaseOrderStatus
from purchases.models.recurring import RecurringBillTemplate, RecurringExpenseTemplate
from purchases.models.vendor import Vendor
from purchases.models.vendor_credit import VendorCredit, VendorCreditStatus
from purchases.services.bills import post_bill
from purchases.services.expenses import post_expense
from purchases.services.goods_receipts import cancel_goods_receipt, receive_goods
from purchases.services.purchase_orders import (
    approve_purchase_order,
    cancel_purchase_order,
    close_purchase_order,
)
from purchases.services.recurring import (
    activate_bill_template,
    activate_expense_template,
    deactivate_bill_template,
    deactivate_expense_template,
)
from purchases.services.three_way_match import match_bill, match_purchase_order
from purchases.services.vendor_credits import issue_vendor_credit, void_vendor_credit


def _decimals_as_strings(value):
    """Match results are plain dicts from services/three_way_match.py, not
    serializer output, so their Decimals never pass through a DecimalField.
    DRF's JSONEncoder renders a bare Decimal as a float — quantities and unit
    prices would reach the client as binary floating point (100.0), unlike
    every other amount in the API. Stringified here, at the edge, so the
    service keeps returning exact Decimals to its Python callers."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _decimals_as_strings(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_decimals_as_strings(item) for item in value]
    return value


def _hash_request_body(data) -> str:
    # Mirrors core.idempotency._hash_body. Not reused directly for the same
    # reason sales/api/views.py inlines it: IdempotentCreateMixin assumes the
    # create serializer's own `.data` IS the response shape, but every create
    # serializer here is a plain input Serializer whose response comes from a
    # different read serializer.
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


# ------------------------------------------------------------- vendors


class VendorListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = VendorSerializer

    @property
    def required_permission(self):
        return Permission.VIEW_VENDORS if self.request.method == "GET" else Permission.MANAGE_VENDORS

    def get_queryset(self):
        qs = Vendor.objects.all()
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() == "true")
        return qs


class VendorDetailView(OrganizationScopedMixin, generics.RetrieveUpdateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = VendorSerializer

    @property
    def required_permission(self):
        return Permission.VIEW_VENDORS if self.request.method == "GET" else Permission.MANAGE_VENDORS

    def get_queryset(self):
        return Vendor.objects.all()


# ----------------------------------------------------- purchase orders


class PurchaseOrderListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    permission_classes = [HasOrgPermission]

    @property
    def required_permission(self):
        return Permission.VIEW_PURCHASE_ORDERS if self.request.method == "GET" else Permission.MANAGE_PURCHASE_ORDERS

    def get_serializer_class(self):
        return PurchaseOrderCreateSerializer if self.request.method == "POST" else PurchaseOrderSerializer

    def get_queryset(self):
        qs = PurchaseOrder.objects.prefetch_related("lines")
        status_filter = self.request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        vendor_id = self.request.query_params.get("vendor")
        if vendor_id:
            qs = qs.filter(vendor_id=vendor_id)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = serializer.save()
        return Response(PurchaseOrderSerializer(order).data, status=201)


class PurchaseOrderDetailView(OrganizationScopedMixin, generics.RetrieveUpdateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = PurchaseOrderSerializer

    @property
    def required_permission(self):
        return Permission.VIEW_PURCHASE_ORDERS if self.request.method == "GET" else Permission.MANAGE_PURCHASE_ORDERS

    def get_queryset(self):
        return PurchaseOrder.objects.prefetch_related("lines")

    def update(self, request, *args, **kwargs):
        order = self.get_object()
        if order.status != PurchaseOrderStatus.DRAFT:
            raise ApplicationError("Only draft purchase orders can be modified.", code="purchase_order_not_draft")

        if "lines" in request.data:
            lines_serializer = PurchaseOrderLinesUpdateSerializer(
                data={"lines": request.data["lines"]}, context={"request": request, "order": order}
            )
            lines_serializer.is_valid(raise_exception=True)
            lines_serializer.save()

        header_data = {
            k: v for k, v in request.data.items()
            if k in ("order_date", "expected_date", "reference", "notes", "terms")
        }
        if header_data:
            header_serializer = self.get_serializer(order, data=header_data, partial=True)
            header_serializer.is_valid(raise_exception=True)
            header_serializer.save()

        order.refresh_from_db()
        return Response(PurchaseOrderSerializer(order).data)


class _PurchaseOrderTransitionView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.MANAGE_PURCHASE_ORDERS
    transition = staticmethod(lambda **kwargs: None)

    def post(self, request, pk):
        order = self.transition(order_id=pk, organization=request.organization, actor=request.user)
        return Response(PurchaseOrderSerializer(order).data)


class PurchaseOrderApproveView(_PurchaseOrderTransitionView):
    transition = staticmethod(approve_purchase_order)


class PurchaseOrderCancelView(_PurchaseOrderTransitionView):
    transition = staticmethod(cancel_purchase_order)


class PurchaseOrderCloseView(_PurchaseOrderTransitionView):
    transition = staticmethod(close_purchase_order)


class PurchaseOrderMatchView(OrganizationScopedMixin, APIView):
    """Three-way match oriented around one purchase order. Read-only —
    reports discrepancies, never blocks or adjusts anything."""

    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_PURCHASE_ORDERS

    def get(self, request, pk):
        order = PurchaseOrder.objects.prefetch_related("lines").filter(pk=pk).first()
        if order is None:
            raise ApplicationError("Purchase order not found.", code="purchase_order_not_found", status_code=404)
        query = ThreeWayMatchQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        return Response(
            _decimals_as_strings(
                match_purchase_order(
                    organization=request.organization,
                    purchase_order=order,
                    quantity_tolerance=query.validated_data["quantity_tolerance"],
                    price_tolerance=query.validated_data["price_tolerance"],
                )
            )
        )


# ------------------------------------------------------ goods receipts


class GoodsReceiptListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    permission_classes = [HasOrgPermission]

    @property
    def required_permission(self):
        return Permission.VIEW_GOODS_RECEIPTS if self.request.method == "GET" else Permission.MANAGE_GOODS_RECEIPTS

    def get_serializer_class(self):
        return GoodsReceiptCreateSerializer if self.request.method == "POST" else GoodsReceiptSerializer

    def get_queryset(self):
        qs = GoodsReceipt.objects.prefetch_related("lines")
        status_filter = self.request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        vendor_id = self.request.query_params.get("vendor")
        if vendor_id:
            qs = qs.filter(vendor_id=vendor_id)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        receipt = serializer.save()
        return Response(GoodsReceiptSerializer(receipt).data, status=201)


class GoodsReceiptDetailView(OrganizationScopedMixin, generics.RetrieveUpdateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = GoodsReceiptSerializer

    @property
    def required_permission(self):
        return Permission.VIEW_GOODS_RECEIPTS if self.request.method == "GET" else Permission.MANAGE_GOODS_RECEIPTS

    def get_queryset(self):
        return GoodsReceipt.objects.prefetch_related("lines")

    def update(self, request, *args, **kwargs):
        receipt = self.get_object()
        if receipt.status != GoodsReceiptStatus.DRAFT:
            raise ApplicationError("Only draft goods receipts can be modified.", code="goods_receipt_not_draft")

        if "lines" in request.data:
            lines_serializer = GoodsReceiptLinesUpdateSerializer(
                data={"lines": request.data["lines"]}, context={"request": request, "receipt": receipt}
            )
            lines_serializer.is_valid(raise_exception=True)
            lines_serializer.save()

        header_data = {
            k: v for k, v in request.data.items() if k in ("receipt_date", "vendor_document_number", "notes")
        }
        if header_data:
            header_serializer = self.get_serializer(receipt, data=header_data, partial=True)
            header_serializer.is_valid(raise_exception=True)
            header_serializer.save()

        receipt.refresh_from_db()
        return Response(GoodsReceiptSerializer(receipt).data)


class _GoodsReceiptTransitionView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.MANAGE_GOODS_RECEIPTS
    transition = staticmethod(lambda **kwargs: None)

    def post(self, request, pk):
        receipt = self.transition(receipt_id=pk, organization=request.organization, actor=request.user)
        return Response(GoodsReceiptSerializer(receipt).data)


class GoodsReceiptReceiveView(_GoodsReceiptTransitionView):
    transition = staticmethod(receive_goods)


class GoodsReceiptCancelView(_GoodsReceiptTransitionView):
    transition = staticmethod(cancel_goods_receipt)


class GoodsReceiptConvertToBillView(OrganizationScopedMixin, APIView):
    """Builds a DRAFT bill from a RECEIVED goods receipt, linking each line
    back to the receipt line it bills so posting cannot receive the stock a
    second time. Requires CREATE_BILL, not MANAGE_GOODS_RECEIPTS — the thing
    being created is a bill."""

    permission_classes = [HasOrgPermission]
    required_permission = Permission.CREATE_BILL

    def post(self, request, pk):
        receipt = GoodsReceipt.objects.prefetch_related("lines").filter(pk=pk).first()
        if receipt is None:
            raise ApplicationError("Goods receipt not found.", code="goods_receipt_not_found", status_code=404)
        serializer = BillFromGoodsReceiptSerializer(
            data=request.data, context={"request": request, "receipt": receipt}
        )
        serializer.is_valid(raise_exception=True)
        return Response(BillSerializer(serializer.save()).data, status=201)


# --------------------------------------------------------------- bills


class BillListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    permission_classes = [HasOrgPermission]

    @property
    def required_permission(self):
        return Permission.VIEW_BILLS if self.request.method == "GET" else Permission.CREATE_BILL

    def get_serializer_class(self):
        return BillCreateSerializer if self.request.method == "POST" else BillSerializer

    def get_queryset(self):
        qs = Bill.objects.prefetch_related("lines")
        status_filter = self.request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        vendor_id = self.request.query_params.get("vendor")
        if vendor_id:
            qs = qs.filter(vendor_id=vendor_id)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        bill = serializer.save()
        return Response(BillSerializer(bill).data, status=201)


class BillDetailView(OrganizationScopedMixin, generics.RetrieveUpdateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = BillSerializer

    @property
    def required_permission(self):
        return Permission.VIEW_BILLS if self.request.method == "GET" else Permission.CREATE_BILL

    def get_queryset(self):
        return Bill.objects.prefetch_related("lines")

    def update(self, request, *args, **kwargs):
        bill = self.get_object()
        if bill.status != BillStatus.DRAFT:
            raise ApplicationError("Only draft bills can be modified.", code="bill_not_draft")

        if "lines" in request.data:
            lines_serializer = BillLinesUpdateSerializer(
                data={"lines": request.data["lines"]}, context={"request": request, "bill": bill}
            )
            lines_serializer.is_valid(raise_exception=True)
            lines_serializer.save()

        header_data = {
            k: v for k, v in request.data.items()
            if k in ("bill_date", "due_date", "reference", "vendor_bill_number", "notes")
        }
        if header_data:
            header_serializer = self.get_serializer(bill, data=header_data, partial=True)
            header_serializer.is_valid(raise_exception=True)
            header_serializer.save()

        bill.refresh_from_db()
        return Response(BillSerializer(bill).data)


class BillPostView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.POST_BILL

    def post(self, request, pk):
        bill = post_bill(bill_id=pk, organization=request.organization, actor=request.user)
        return Response(BillSerializer(bill).data)


class BillVoidView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VOID_BILL

    def post(self, request, pk):
        bill = Bill.objects.filter(pk=pk).first()
        if bill is None:
            raise ApplicationError("Bill not found.", code="bill_not_found", status_code=404)
        serializer = BillVoidSerializer(data=request.data, context={"request": request, "bill": bill})
        serializer.is_valid(raise_exception=True)
        return Response(BillSerializer(serializer.save()).data)


class BillMatchView(OrganizationScopedMixin, APIView):
    """Three-way match oriented around one bill — the view a person wants
    when deciding whether to approve it. Read-only."""

    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_BILLS

    def get(self, request, pk):
        bill = Bill.objects.prefetch_related("lines").filter(pk=pk).first()
        if bill is None:
            raise ApplicationError("Bill not found.", code="bill_not_found", status_code=404)
        query = ThreeWayMatchQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        return Response(
            _decimals_as_strings(
                match_bill(
                    organization=request.organization,
                    bill=bill,
                    quantity_tolerance=query.validated_data["quantity_tolerance"],
                    price_tolerance=query.validated_data["price_tolerance"],
                )
            )
        )


# ------------------------------------------------------------ expenses


class ExpenseListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    permission_classes = [HasOrgPermission]

    @property
    def required_permission(self):
        return Permission.VIEW_EXPENSES if self.request.method == "GET" else Permission.MANAGE_EXPENSES

    def get_serializer_class(self):
        return ExpenseCreateSerializer if self.request.method == "POST" else ExpenseSerializer

    def get_queryset(self):
        qs = Expense.objects.all()
        status_filter = self.request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        vendor_id = self.request.query_params.get("vendor")
        if vendor_id:
            qs = qs.filter(vendor_id=vendor_id)
        is_billable = self.request.query_params.get("is_billable")
        if is_billable is not None:
            qs = qs.filter(is_billable=is_billable.lower() == "true")
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        expense = serializer.save()
        return Response(ExpenseSerializer(expense).data, status=201)


class ExpenseDetailView(OrganizationScopedMixin, generics.RetrieveUpdateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = ExpenseSerializer

    @property
    def required_permission(self):
        return Permission.VIEW_EXPENSES if self.request.method == "GET" else Permission.MANAGE_EXPENSES

    def get_queryset(self):
        return Expense.objects.all()

    def update(self, request, *args, **kwargs):
        expense = self.get_object()
        if expense.status != ExpenseStatus.DRAFT:
            raise ApplicationError("Only draft expenses can be modified.", code="expense_not_draft")
        serializer = ExpenseUpdateSerializer(
            data=request.data, partial=True, context={"request": request, "expense": expense}
        )
        serializer.is_valid(raise_exception=True)
        return Response(ExpenseSerializer(serializer.save()).data)


class ExpensePostView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.MANAGE_EXPENSES

    def post(self, request, pk):
        expense = post_expense(expense_id=pk, organization=request.organization, actor=request.user)
        return Response(ExpenseSerializer(expense).data)


class ExpenseVoidView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.MANAGE_EXPENSES

    def post(self, request, pk):
        expense = Expense.objects.filter(pk=pk).first()
        if expense is None:
            raise ApplicationError("Expense not found.", code="expense_not_found", status_code=404)
        serializer = ExpenseVoidSerializer(data=request.data, context={"request": request, "expense": expense})
        serializer.is_valid(raise_exception=True)
        return Response(ExpenseSerializer(serializer.save()).data)


# ------------------------------------------------------------ payments


class VendorPaymentListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    """List/create + retrieve only — no update or delete. A vendor payment is
    append-only (see models/payment.py); duplicate-request safety comes from
    the `Idempotency-Key` header, since there is no DRAFT/POSTED lifecycle to
    make posting idempotent by construction."""

    permission_classes = [HasOrgPermission]

    @property
    def required_permission(self):
        return (
            Permission.VIEW_VENDOR_PAYMENTS if self.request.method == "GET"
            else Permission.RECORD_VENDOR_PAYMENT
        )

    def get_serializer_class(self):
        return VendorPaymentCreateSerializer if self.request.method == "POST" else VendorPaymentSerializer

    def get_queryset(self):
        qs = VendorPayment.objects.prefetch_related("allocations")
        vendor_id = self.request.query_params.get("vendor")
        if vendor_id:
            qs = qs.filter(vendor_id=vendor_id)
        return qs

    def create(self, request, *args, **kwargs):
        idempotency_key = request.headers.get("Idempotency-Key")
        body_hash = _hash_request_body(request.data) if idempotency_key else None
        if idempotency_key:
            existing = IdempotencyKey.objects.filter(
                key=idempotency_key, request_path=request.path, request_body_hash=body_hash
            ).first()
            if existing is not None:
                return Response(existing.response_body, status=existing.response_status)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payment = serializer.save()
        response = Response(VendorPaymentSerializer(payment).data, status=201)

        if idempotency_key:
            IdempotencyKey.objects.create(
                organization=request.organization, key=idempotency_key, request_path=request.path,
                request_body_hash=body_hash, response_status=response.status_code, response_body=response.data,
            )
        return response


class VendorPaymentDetailView(OrganizationScopedMixin, generics.RetrieveAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = VendorPaymentSerializer
    required_permission = Permission.VIEW_VENDOR_PAYMENTS

    def get_queryset(self):
        return VendorPayment.objects.prefetch_related("allocations")


# ------------------------------------------------------ vendor credits


class VendorCreditListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    permission_classes = [HasOrgPermission]

    @property
    def required_permission(self):
        return Permission.VIEW_VENDOR_CREDITS if self.request.method == "GET" else Permission.ISSUE_VENDOR_CREDIT

    def get_serializer_class(self):
        return VendorCreditCreateSerializer if self.request.method == "POST" else VendorCreditSerializer

    def get_queryset(self):
        qs = VendorCredit.objects.prefetch_related("lines")
        status_filter = self.request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        vendor_id = self.request.query_params.get("vendor")
        if vendor_id:
            qs = qs.filter(vendor_id=vendor_id)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        vendor_credit = serializer.save()
        return Response(VendorCreditSerializer(vendor_credit).data, status=201)


class VendorCreditDetailView(OrganizationScopedMixin, generics.RetrieveUpdateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = VendorCreditSerializer

    @property
    def required_permission(self):
        return Permission.VIEW_VENDOR_CREDITS if self.request.method == "GET" else Permission.ISSUE_VENDOR_CREDIT

    def get_queryset(self):
        return VendorCredit.objects.prefetch_related("lines")

    def update(self, request, *args, **kwargs):
        vendor_credit = self.get_object()
        if vendor_credit.status != VendorCreditStatus.DRAFT:
            raise ApplicationError("Only draft vendor credits can be modified.", code="vendor_credit_not_draft")

        if "lines" in request.data:
            lines_serializer = VendorCreditLinesUpdateSerializer(
                data={"lines": request.data["lines"]},
                context={"request": request, "vendor_credit": vendor_credit},
            )
            lines_serializer.is_valid(raise_exception=True)
            lines_serializer.save()

        header_data = {
            k: v for k, v in request.data.items()
            if k in ("credit_date", "reference", "vendor_credit_number", "reason", "notes")
        }
        if header_data:
            header_serializer = self.get_serializer(vendor_credit, data=header_data, partial=True)
            header_serializer.is_valid(raise_exception=True)
            header_serializer.save()

        vendor_credit.refresh_from_db()
        return Response(VendorCreditSerializer(vendor_credit).data)


class VendorCreditIssueView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.ISSUE_VENDOR_CREDIT

    def post(self, request, pk):
        vendor_credit = issue_vendor_credit(
            vendor_credit_id=pk, organization=request.organization, actor=request.user
        )
        return Response(VendorCreditSerializer(vendor_credit).data)


class VendorCreditVoidView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.ISSUE_VENDOR_CREDIT

    def post(self, request, pk):
        vendor_credit = void_vendor_credit(
            vendor_credit_id=pk, organization=request.organization, actor=request.user
        )
        return Response(VendorCreditSerializer(vendor_credit).data)


# ----------------------------------------------------------- recurring
# No manual "generate now" endpoint for either template kind — generation is
# Celery-only (see tasks.py), same as sales' recurring invoices.


class RecurringBillTemplateListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    permission_classes = [HasOrgPermission]

    @property
    def required_permission(self):
        return Permission.VIEW_RECURRING_BILLS if self.request.method == "GET" else Permission.MANAGE_RECURRING_BILLS

    def get_serializer_class(self):
        return (
            RecurringBillTemplateCreateSerializer if self.request.method == "POST"
            else RecurringBillTemplateSerializer
        )

    def get_queryset(self):
        qs = RecurringBillTemplate.objects.prefetch_related("lines")
        vendor_id = self.request.query_params.get("vendor")
        if vendor_id:
            qs = qs.filter(vendor_id=vendor_id)
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() == "true")
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        template = serializer.save()
        return Response(RecurringBillTemplateSerializer(template).data, status=201)


class RecurringBillTemplateDetailView(OrganizationScopedMixin, generics.RetrieveUpdateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = RecurringBillTemplateSerializer

    @property
    def required_permission(self):
        return Permission.VIEW_RECURRING_BILLS if self.request.method == "GET" else Permission.MANAGE_RECURRING_BILLS

    def get_queryset(self):
        return RecurringBillTemplate.objects.prefetch_related("lines")

    def update(self, request, *args, **kwargs):
        template = self.get_object()
        serializer = RecurringBillTemplateUpdateSerializer(
            data=request.data, partial=True, context={"request": request, "template": template}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        template.refresh_from_db()
        return Response(RecurringBillTemplateSerializer(template).data)


class _RecurringBillTemplateToggleView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.MANAGE_RECURRING_BILLS
    toggle = staticmethod(lambda **kwargs: None)

    def post(self, request, pk):
        template = RecurringBillTemplate.objects.filter(pk=pk).first()
        if template is None:
            raise ApplicationError(
                "Recurring bill template not found.", code="recurring_bill_template_not_found", status_code=404
            )
        return Response(RecurringBillTemplateSerializer(self.toggle(template=template, actor=request.user)).data)


class RecurringBillTemplateActivateView(_RecurringBillTemplateToggleView):
    toggle = staticmethod(activate_bill_template)


class RecurringBillTemplateDeactivateView(_RecurringBillTemplateToggleView):
    toggle = staticmethod(deactivate_bill_template)


class RecurringExpenseTemplateListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    permission_classes = [HasOrgPermission]

    @property
    def required_permission(self):
        return Permission.VIEW_RECURRING_BILLS if self.request.method == "GET" else Permission.MANAGE_RECURRING_BILLS

    def get_serializer_class(self):
        return (
            RecurringExpenseTemplateCreateSerializer if self.request.method == "POST"
            else RecurringExpenseTemplateSerializer
        )

    def get_queryset(self):
        qs = RecurringExpenseTemplate.objects.all()
        vendor_id = self.request.query_params.get("vendor")
        if vendor_id:
            qs = qs.filter(vendor_id=vendor_id)
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() == "true")
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        template = serializer.save()
        return Response(RecurringExpenseTemplateSerializer(template).data, status=201)


class RecurringExpenseTemplateDetailView(OrganizationScopedMixin, generics.RetrieveUpdateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = RecurringExpenseTemplateSerializer

    @property
    def required_permission(self):
        return Permission.VIEW_RECURRING_BILLS if self.request.method == "GET" else Permission.MANAGE_RECURRING_BILLS

    def get_queryset(self):
        return RecurringExpenseTemplate.objects.all()

    def update(self, request, *args, **kwargs):
        template = self.get_object()
        serializer = RecurringExpenseTemplateUpdateSerializer(
            data=request.data, partial=True, context={"request": request, "template": template}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        template.refresh_from_db()
        return Response(RecurringExpenseTemplateSerializer(template).data)


class _RecurringExpenseTemplateToggleView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.MANAGE_RECURRING_BILLS
    toggle = staticmethod(lambda **kwargs: None)

    def post(self, request, pk):
        template = RecurringExpenseTemplate.objects.filter(pk=pk).first()
        if template is None:
            raise ApplicationError(
                "Recurring expense template not found.",
                code="recurring_expense_template_not_found",
                status_code=404,
            )
        return Response(RecurringExpenseTemplateSerializer(self.toggle(template=template, actor=request.user)).data)


class RecurringExpenseTemplateActivateView(_RecurringExpenseTemplateToggleView):
    toggle = staticmethod(activate_expense_template)


class RecurringExpenseTemplateDeactivateView(_RecurringExpenseTemplateToggleView):
    toggle = staticmethod(deactivate_expense_template)
