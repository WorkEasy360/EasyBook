import hashlib
import json

from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView

from authz.permissions import HasOrgPermission
from authz.roles import Permission
from core.exceptions import ApplicationError
from core.models import IdempotencyKey
from core.views import OrganizationScopedMixin
from sales.api.serializers import (
    CreditNoteCreateSerializer,
    CreditNoteLinesUpdateSerializer,
    CreditNoteSerializer,
    CustomerPaymentCreateSerializer,
    CustomerPaymentSerializer,
    CustomerSerializer,
    DeliveryChallanCreateSerializer,
    DeliveryChallanLinesUpdateSerializer,
    DeliveryChallanSerializer,
    InvoiceCreateSerializer,
    InvoiceLinesUpdateSerializer,
    InvoiceSerializer,
    InvoiceVoidSerializer,
    QuoteConvertSerializer,
    QuoteCreateSerializer,
    QuoteLinesUpdateSerializer,
    QuoteSerializer,
    RecurringInvoiceTemplateCreateSerializer,
    RecurringInvoiceTemplateSerializer,
    RecurringInvoiceTemplateUpdateSerializer,
    SalesOrderCreateSerializer,
    SalesOrderLinesUpdateSerializer,
    SalesOrderSerializer,
)
from sales.models.credit_note import CreditNote, CreditNoteStatus
from sales.models.customer import Customer
from sales.models.delivery import DeliveryChallan, DeliveryChallanStatus
from sales.models.invoice import Invoice, InvoiceStatus
from sales.models.payment import CustomerPayment
from sales.models.quote import Quote, QuoteStatus
from sales.models.recurring_invoice import RecurringInvoiceTemplate
from sales.models.sales_order import SalesOrder, SalesOrderStatus
from sales.services.credit_notes import issue_credit_note, void_credit_note
from sales.services.deliveries import cancel_delivery, dispatch_delivery, mark_delivered
from sales.services.invoices import post_invoice
from sales.services.quotes import accept_quote, cancel_quote, reject_quote, send_quote
from sales.services.recurring_invoices import activate_template, deactivate_template
from sales.services.sales_orders import cancel_order, confirm_order

# NOTE: every queryset here is built in get_queryset(), never as a bare
# `queryset = Model.objects.all()` class attribute — see core/CLAUDE.md and
# items/api/views.py for why a class-level queryset regresses tenant scoping.


class CustomerListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = CustomerSerializer

    @property
    def required_permission(self):
        return Permission.VIEW_CUSTOMERS if self.request.method == "GET" else Permission.MANAGE_CUSTOMERS

    def get_queryset(self):
        qs = Customer.objects.all()
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() == "true")
        return qs


class CustomerDetailView(OrganizationScopedMixin, generics.RetrieveUpdateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = CustomerSerializer

    @property
    def required_permission(self):
        return Permission.VIEW_CUSTOMERS if self.request.method == "GET" else Permission.MANAGE_CUSTOMERS

    def get_queryset(self):
        return Customer.objects.all()


class QuoteListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    permission_classes = [HasOrgPermission]

    @property
    def required_permission(self):
        return Permission.VIEW_QUOTES if self.request.method == "GET" else Permission.MANAGE_QUOTES

    def get_serializer_class(self):
        return QuoteCreateSerializer if self.request.method == "POST" else QuoteSerializer

    def get_queryset(self):
        qs = Quote.objects.prefetch_related("lines")
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
        quote = serializer.save()
        return Response(QuoteSerializer(quote).data, status=201)


class QuoteDetailView(OrganizationScopedMixin, generics.RetrieveUpdateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = QuoteSerializer

    @property
    def required_permission(self):
        return Permission.VIEW_QUOTES if self.request.method == "GET" else Permission.MANAGE_QUOTES

    def get_queryset(self):
        return Quote.objects.prefetch_related("lines")

    def update(self, request, *args, **kwargs):
        quote = self.get_object()
        if quote.status != QuoteStatus.DRAFT:
            raise ApplicationError("Only draft quotes can be modified.", code="quote_not_draft")

        if "lines" in request.data:
            lines_serializer = QuoteLinesUpdateSerializer(
                data={"lines": request.data["lines"]}, context={"request": request, "quote": quote}
            )
            lines_serializer.is_valid(raise_exception=True)
            lines_serializer.save()

        header_data = {k: v for k, v in request.data.items() if k in ("issue_date", "expiry_date", "notes", "terms")}
        if header_data:
            header_serializer = self.get_serializer(quote, data=header_data, partial=True)
            header_serializer.is_valid(raise_exception=True)
            header_serializer.save()

        quote.refresh_from_db()
        return Response(QuoteSerializer(quote).data)


class _QuoteTransitionView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.MANAGE_QUOTES
    transition = staticmethod(lambda **kwargs: None)

    def post(self, request, pk):
        quote = self.transition(quote_id=pk, organization=request.organization, actor=request.user)
        return Response(QuoteSerializer(quote).data)


class QuoteSendView(_QuoteTransitionView):
    transition = staticmethod(send_quote)


class QuoteAcceptView(_QuoteTransitionView):
    transition = staticmethod(accept_quote)


class QuoteRejectView(_QuoteTransitionView):
    transition = staticmethod(reject_quote)


class QuoteCancelView(_QuoteTransitionView):
    transition = staticmethod(cancel_quote)


class QuoteConvertView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.MANAGE_QUOTES

    def post(self, request, pk):
        quote = Quote.objects.filter(pk=pk).first()
        if quote is None:
            raise ApplicationError("Quote not found.", code="quote_not_found", status_code=404)
        serializer = QuoteConvertSerializer(data=request.data, context={"request": request, "quote": quote})
        serializer.is_valid(raise_exception=True)
        result = serializer.save()
        response_serializer = InvoiceSerializer if isinstance(result, Invoice) else SalesOrderSerializer
        return Response(response_serializer(result).data, status=201)


class SalesOrderListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    permission_classes = [HasOrgPermission]

    @property
    def required_permission(self):
        return Permission.VIEW_ORDERS if self.request.method == "GET" else Permission.MANAGE_ORDERS

    def get_serializer_class(self):
        return SalesOrderCreateSerializer if self.request.method == "POST" else SalesOrderSerializer

    def get_queryset(self):
        qs = SalesOrder.objects.prefetch_related("lines")
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
        order = serializer.save()
        return Response(SalesOrderSerializer(order).data, status=201)


class SalesOrderDetailView(OrganizationScopedMixin, generics.RetrieveUpdateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = SalesOrderSerializer

    @property
    def required_permission(self):
        return Permission.VIEW_ORDERS if self.request.method == "GET" else Permission.MANAGE_ORDERS

    def get_queryset(self):
        return SalesOrder.objects.prefetch_related("lines")

    def update(self, request, *args, **kwargs):
        order = self.get_object()
        if order.status != SalesOrderStatus.DRAFT:
            raise ApplicationError("Only draft sales orders can be modified.", code="sales_order_not_draft")

        if "lines" in request.data:
            lines_serializer = SalesOrderLinesUpdateSerializer(
                data={"lines": request.data["lines"]}, context={"request": request, "order": order}
            )
            lines_serializer.is_valid(raise_exception=True)
            lines_serializer.save()

        header_data = {k: v for k, v in request.data.items() if k in ("order_date", "notes", "terms")}
        if header_data:
            header_serializer = self.get_serializer(order, data=header_data, partial=True)
            header_serializer.is_valid(raise_exception=True)
            header_serializer.save()

        order.refresh_from_db()
        return Response(SalesOrderSerializer(order).data)


class _SalesOrderTransitionView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.MANAGE_ORDERS
    transition = staticmethod(lambda **kwargs: None)

    def post(self, request, pk):
        order = self.transition(order_id=pk, organization=request.organization, actor=request.user)
        return Response(SalesOrderSerializer(order).data)


class SalesOrderConfirmView(_SalesOrderTransitionView):
    transition = staticmethod(confirm_order)


class SalesOrderCancelView(_SalesOrderTransitionView):
    transition = staticmethod(cancel_order)


class DeliveryChallanListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    permission_classes = [HasOrgPermission]

    @property
    def required_permission(self):
        return Permission.VIEW_DELIVERIES if self.request.method == "GET" else Permission.MANAGE_DELIVERIES

    def get_serializer_class(self):
        return DeliveryChallanCreateSerializer if self.request.method == "POST" else DeliveryChallanSerializer

    def get_queryset(self):
        qs = DeliveryChallan.objects.prefetch_related("lines")
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
        challan = serializer.save()
        return Response(DeliveryChallanSerializer(challan).data, status=201)


class DeliveryChallanDetailView(OrganizationScopedMixin, generics.RetrieveUpdateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = DeliveryChallanSerializer

    @property
    def required_permission(self):
        return Permission.VIEW_DELIVERIES if self.request.method == "GET" else Permission.MANAGE_DELIVERIES

    def get_queryset(self):
        return DeliveryChallan.objects.prefetch_related("lines")

    def update(self, request, *args, **kwargs):
        challan = self.get_object()
        if challan.status != DeliveryChallanStatus.DRAFT:
            raise ApplicationError("Only draft delivery challans can be modified.", code="delivery_not_draft")

        if "lines" in request.data:
            lines_serializer = DeliveryChallanLinesUpdateSerializer(
                data={"lines": request.data["lines"]}, context={"request": request, "challan": challan}
            )
            lines_serializer.is_valid(raise_exception=True)
            lines_serializer.save()

        header_data = {k: v for k, v in request.data.items() if k in ("challan_date", "notes")}
        if header_data:
            header_serializer = self.get_serializer(challan, data=header_data, partial=True)
            header_serializer.is_valid(raise_exception=True)
            header_serializer.save()

        challan.refresh_from_db()
        return Response(DeliveryChallanSerializer(challan).data)


class _DeliveryTransitionView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.MANAGE_DELIVERIES
    transition = staticmethod(lambda **kwargs: None)

    def post(self, request, pk):
        challan = self.transition(challan_id=pk, organization=request.organization, actor=request.user)
        return Response(DeliveryChallanSerializer(challan).data)


class DeliveryChallanDispatchView(_DeliveryTransitionView):
    transition = staticmethod(dispatch_delivery)


class DeliveryChallanDeliverView(_DeliveryTransitionView):
    transition = staticmethod(mark_delivered)


class DeliveryChallanCancelView(_DeliveryTransitionView):
    transition = staticmethod(cancel_delivery)


class InvoiceListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    permission_classes = [HasOrgPermission]

    @property
    def required_permission(self):
        return Permission.VIEW_INVOICES if self.request.method == "GET" else Permission.CREATE_INVOICE

    def get_serializer_class(self):
        return InvoiceCreateSerializer if self.request.method == "POST" else InvoiceSerializer

    def get_queryset(self):
        qs = Invoice.objects.prefetch_related("lines")
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
        invoice = serializer.save()
        return Response(InvoiceSerializer(invoice).data, status=201)


class InvoiceDetailView(OrganizationScopedMixin, generics.RetrieveUpdateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = InvoiceSerializer

    @property
    def required_permission(self):
        return Permission.VIEW_INVOICES if self.request.method == "GET" else Permission.CREATE_INVOICE

    def get_queryset(self):
        return Invoice.objects.prefetch_related("lines")

    def update(self, request, *args, **kwargs):
        invoice = self.get_object()
        if invoice.status != InvoiceStatus.DRAFT:
            raise ApplicationError("Only draft invoices can be modified.", code="invoice_not_draft")

        if "lines" in request.data:
            lines_serializer = InvoiceLinesUpdateSerializer(
                data={"lines": request.data["lines"]}, context={"request": request, "invoice": invoice}
            )
            lines_serializer.is_valid(raise_exception=True)
            lines_serializer.save()

        header_data = {
            k: v for k, v in request.data.items()
            if k in ("invoice_date", "due_date", "reference", "notes", "terms")
        }
        if header_data:
            header_serializer = self.get_serializer(invoice, data=header_data, partial=True)
            header_serializer.is_valid(raise_exception=True)
            header_serializer.save()

        invoice.refresh_from_db()
        return Response(InvoiceSerializer(invoice).data)


class InvoicePostView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.POST_INVOICE

    def post(self, request, pk):
        invoice = post_invoice(invoice_id=pk, organization=request.organization, actor=request.user)
        return Response(InvoiceSerializer(invoice).data)


class InvoiceVoidView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VOID_INVOICE

    def post(self, request, pk):
        invoice = Invoice.objects.filter(pk=pk).first()
        if invoice is None:
            raise ApplicationError("Invoice not found.", code="invoice_not_found", status_code=404)
        serializer = InvoiceVoidSerializer(data=request.data, context={"request": request, "invoice": invoice})
        serializer.is_valid(raise_exception=True)
        voided = serializer.save()
        return Response(InvoiceSerializer(voided).data)


def _hash_request_body(data) -> str:
    # Mirrors core.idempotency._hash_body exactly. Not reused directly
    # because core.idempotency.IdempotentCreateMixin assumes the create
    # serializer's own `.data` IS the final response shape — true for a
    # plain ModelSerializer, but every sales create-serializer here is a
    # plain input Serializer whose response is a DIFFERENT read serializer
    # (CustomerPaymentSerializer), so the replay/store logic is inlined here.
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


class CustomerPaymentListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    permission_classes = [HasOrgPermission]

    @property
    def required_permission(self):
        return Permission.VIEW_PAYMENTS if self.request.method == "GET" else Permission.RECORD_PAYMENT

    def get_serializer_class(self):
        return CustomerPaymentCreateSerializer if self.request.method == "POST" else CustomerPaymentSerializer

    def get_queryset(self):
        qs = CustomerPayment.objects.prefetch_related("allocations")
        customer_id = self.request.query_params.get("customer")
        if customer_id:
            qs = qs.filter(customer_id=customer_id)
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
        response = Response(CustomerPaymentSerializer(payment).data, status=201)

        if idempotency_key:
            IdempotencyKey.objects.create(
                organization=request.organization, key=idempotency_key, request_path=request.path,
                request_body_hash=body_hash, response_status=response.status_code, response_body=response.data,
            )
        return response


class CustomerPaymentDetailView(OrganizationScopedMixin, generics.RetrieveAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = CustomerPaymentSerializer
    required_permission = Permission.VIEW_PAYMENTS

    def get_queryset(self):
        return CustomerPayment.objects.prefetch_related("allocations")


class CreditNoteListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    permission_classes = [HasOrgPermission]

    @property
    def required_permission(self):
        return Permission.VIEW_CREDIT_NOTES if self.request.method == "GET" else Permission.ISSUE_CREDIT_NOTE

    def get_serializer_class(self):
        return CreditNoteCreateSerializer if self.request.method == "POST" else CreditNoteSerializer

    def get_queryset(self):
        qs = CreditNote.objects.prefetch_related("lines")
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
        credit_note = serializer.save()
        return Response(CreditNoteSerializer(credit_note).data, status=201)


class CreditNoteDetailView(OrganizationScopedMixin, generics.RetrieveUpdateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = CreditNoteSerializer

    @property
    def required_permission(self):
        return Permission.VIEW_CREDIT_NOTES if self.request.method == "GET" else Permission.ISSUE_CREDIT_NOTE

    def get_queryset(self):
        return CreditNote.objects.prefetch_related("lines")

    def update(self, request, *args, **kwargs):
        credit_note = self.get_object()
        if credit_note.status != CreditNoteStatus.DRAFT:
            raise ApplicationError("Only draft credit notes can be modified.", code="credit_note_not_draft")

        if "lines" in request.data:
            lines_serializer = CreditNoteLinesUpdateSerializer(
                data={"lines": request.data["lines"]}, context={"request": request, "credit_note": credit_note}
            )
            lines_serializer.is_valid(raise_exception=True)
            lines_serializer.save()

        header_data = {k: v for k, v in request.data.items() if k in ("credit_note_date", "reference", "notes")}
        if header_data:
            header_serializer = self.get_serializer(credit_note, data=header_data, partial=True)
            header_serializer.is_valid(raise_exception=True)
            header_serializer.save()

        credit_note.refresh_from_db()
        return Response(CreditNoteSerializer(credit_note).data)


class CreditNoteIssueView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.ISSUE_CREDIT_NOTE

    def post(self, request, pk):
        credit_note = issue_credit_note(credit_note_id=pk, organization=request.organization, actor=request.user)
        return Response(CreditNoteSerializer(credit_note).data)


class CreditNoteVoidView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.ISSUE_CREDIT_NOTE

    def post(self, request, pk):
        credit_note = void_credit_note(credit_note_id=pk, organization=request.organization, actor=request.user)
        return Response(CreditNoteSerializer(credit_note).data)


class RecurringInvoiceTemplateListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    permission_classes = [HasOrgPermission]

    @property
    def required_permission(self):
        return (
            Permission.VIEW_RECURRING_INVOICES if self.request.method == "GET"
            else Permission.MANAGE_RECURRING_INVOICES
        )

    def get_serializer_class(self):
        return (
            RecurringInvoiceTemplateCreateSerializer if self.request.method == "POST"
            else RecurringInvoiceTemplateSerializer
        )

    def get_queryset(self):
        qs = RecurringInvoiceTemplate.objects.prefetch_related("lines")
        customer_id = self.request.query_params.get("customer")
        if customer_id:
            qs = qs.filter(customer_id=customer_id)
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() == "true")
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        template = serializer.save()
        return Response(RecurringInvoiceTemplateSerializer(template).data, status=201)


class RecurringInvoiceTemplateDetailView(OrganizationScopedMixin, generics.RetrieveUpdateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = RecurringInvoiceTemplateSerializer

    @property
    def required_permission(self):
        return (
            Permission.VIEW_RECURRING_INVOICES if self.request.method == "GET"
            else Permission.MANAGE_RECURRING_INVOICES
        )

    def get_queryset(self):
        return RecurringInvoiceTemplate.objects.prefetch_related("lines")

    def update(self, request, *args, **kwargs):
        template = self.get_object()
        serializer = RecurringInvoiceTemplateUpdateSerializer(
            data=request.data, partial=True, context={"request": request, "template": template}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        template.refresh_from_db()
        return Response(RecurringInvoiceTemplateSerializer(template).data)


class _RecurringInvoiceTemplateTransitionView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.MANAGE_RECURRING_INVOICES
    transition = staticmethod(lambda **kwargs: None)

    def post(self, request, pk):
        template = RecurringInvoiceTemplate.objects.filter(pk=pk).first()
        if template is None:
            raise ApplicationError("Recurring invoice template not found.", code="recurring_template_not_found", status_code=404)
        updated = self.transition(template=template, actor=request.user)
        return Response(RecurringInvoiceTemplateSerializer(updated).data)


class RecurringInvoiceTemplateActivateView(_RecurringInvoiceTemplateTransitionView):
    transition = staticmethod(activate_template)


class RecurringInvoiceTemplateDeactivateView(_RecurringInvoiceTemplateTransitionView):
    transition = staticmethod(deactivate_template)
