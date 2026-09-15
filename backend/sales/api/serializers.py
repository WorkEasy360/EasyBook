from decimal import Decimal

from rest_framework import serializers

from accounting.models.account import Account
from accounts.models import Currency
from core.exceptions import ApplicationError
from inventory.models.warehouse import Warehouse
from items.models.item import Item
from sales.models.credit_note import CreditNote, CreditNoteLine
from sales.models.customer import Customer
from sales.models.delivery import DeliveryChallan, DeliveryChallanLine
from sales.models.invoice import Invoice, InvoiceLine
from sales.models.payment import CustomerPayment, PaymentAllocation
from sales.models.quote import Quote, QuoteLine
from sales.models.recurring_invoice import RecurringInvoiceTemplate, RecurringInvoiceTemplateLine
from sales.models.sales_order import SalesOrder, SalesOrderLine
from sales.services.credit_notes import create_credit_note, replace_credit_note_lines
from sales.services.customers import create_customer, update_customer
from sales.services.deliveries import create_delivery_challan, replace_delivery_lines
from sales.services.invoices import convert_quote_to_invoice, create_invoice, replace_invoice_lines
from sales.services.payments import record_payment
from sales.services.quotes import create_quote, replace_quote_lines
from sales.services.recurring_invoices import create_recurring_template, update_recurring_template
from sales.services.sales_orders import convert_quote_to_sales_order, create_sales_order, replace_order_lines


def _get_or_404(model, pk, label: str):
    # NOT `model.objects.filter(...)` built as a class-level queryset — this
    # runs inside create()/save() at request time, after tenant context is
    # set, so TenantManager scopes it correctly (see core/CLAUDE.md and
    # inventory/api/serializers.py, which this mirrors).
    try:
        return model.objects.get(pk=pk)
    except model.DoesNotExist:
        raise ApplicationError(f"{label} not found.", code=f"{label.lower()}_not_found", status_code=404)


def _resolve_document_lines(raw_lines: list[dict]) -> list[dict]:
    item_ids = {line["item_id"] for line in raw_lines}
    items_by_id = {i.id: i for i in Item.objects.filter(id__in=item_ids)}
    if len(items_by_id) != len(item_ids):
        raise ApplicationError(
            "One or more items were not found in this organization.", code="item_not_found", status_code=404
        )
    return [
        {
            "item": items_by_id[line["item_id"]],
            "description": line.get("description", ""),
            "quantity": line["quantity"],
            "unit_price": line["unit_price"],
            "discount_percent": line.get("discount_percent", Decimal("0")),
            "tax_rate": line.get("tax_rate", Decimal("0")),
        }
        for line in raw_lines
    ]


class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = [
            "id", "customer_code", "display_name", "legal_name", "email", "phone",
            "gstin", "pan", "billing_address", "shipping_address", "currency",
            "payment_terms_days", "credit_limit", "is_active", "notes",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def create(self, validated_data):
        request = self.context["request"]
        return create_customer(organization=request.organization, actor=request.user, **validated_data)

    def update(self, instance, validated_data):
        request = self.context["request"]
        return update_customer(customer=instance, actor=request.user, **validated_data)


class QuoteLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuoteLine
        fields = [
            "id", "item", "line_number", "description", "hsn_sac_snapshot", "tax_label",
            "quantity", "unit_price", "discount_percent", "tax_rate",
            "line_base", "discount_amount", "taxable_amount", "tax_amount", "line_total",
        ]
        read_only_fields = fields


class QuoteLineInputSerializer(serializers.Serializer):
    item_id = serializers.UUIDField()
    description = serializers.CharField(required=False, allow_blank=True, default="")
    quantity = serializers.DecimalField(max_digits=18, decimal_places=4)
    unit_price = serializers.DecimalField(max_digits=18, decimal_places=2)
    discount_percent = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, default=Decimal("0"))
    tax_rate = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, default=Decimal("0"))


class QuoteSerializer(serializers.ModelSerializer):
    lines = QuoteLineSerializer(many=True, read_only=True)

    class Meta:
        model = Quote
        fields = [
            "id", "customer", "quote_number", "status", "issue_date", "expiry_date",
            "currency", "exchange_rate", "subtotal", "discount_total", "tax_total", "total",
            "notes", "terms", "lines", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "quote_number", "status", "subtotal", "discount_total", "tax_total", "total",
            "lines", "created_at", "updated_at",
        ]


class QuoteCreateSerializer(serializers.Serializer):
    customer_id = serializers.UUIDField()
    issue_date = serializers.DateField()
    expiry_date = serializers.DateField(required=False, allow_null=True, default=None)
    # Currency is global reference data (accounts.Currency), not org-scoped —
    # safe as a class-level queryset, unlike customer_id/item_id above.
    currency = serializers.PrimaryKeyRelatedField(queryset=Currency.objects.all(), required=False, default=None)
    exchange_rate = serializers.DecimalField(max_digits=18, decimal_places=8, required=False, default=Decimal("1"))
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    terms = serializers.CharField(required=False, allow_blank=True, default="")
    lines = QuoteLineInputSerializer(many=True)

    def create(self, validated_data):
        request = self.context["request"]
        customer = _get_or_404(Customer, validated_data.pop("customer_id"), "Customer")
        lines = _resolve_document_lines(validated_data.pop("lines"))
        return create_quote(
            organization=request.organization, customer=customer, actor=request.user, lines=lines, **validated_data
        )


class QuoteLinesUpdateSerializer(serializers.Serializer):
    lines = QuoteLineInputSerializer(many=True)

    def save(self, **kwargs):
        request = self.context["request"]
        lines = _resolve_document_lines(self.validated_data["lines"])
        return replace_quote_lines(quote=self.context["quote"], lines=lines, actor=request.user)


class QuoteConvertSerializer(serializers.Serializer):
    target = serializers.ChoiceField(choices=["sales_order", "invoice"], required=False, default="sales_order")
    order_date = serializers.DateField(required=False, default=None)
    invoice_date = serializers.DateField(required=False, default=None)
    due_date = serializers.DateField(required=False, allow_null=True, default=None)
    receivable_account_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    tax_payable_account_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    warehouse_id = serializers.UUIDField(required=False, allow_null=True, default=None)

    def validate(self, attrs):
        if attrs["target"] == "invoice":
            if attrs.get("due_date") is None:
                raise serializers.ValidationError({"due_date": "Required when target is 'invoice'."})
            if attrs.get("receivable_account_id") is None:
                raise serializers.ValidationError({"receivable_account_id": "Required when target is 'invoice'."})
        return attrs

    def save(self, **kwargs):
        request = self.context["request"]
        quote = self.context["quote"]
        data = self.validated_data

        if data["target"] == "sales_order":
            order_date = data["order_date"] or quote.issue_date
            return convert_quote_to_sales_order(quote=quote, order_date=order_date, actor=request.user)

        receivable_account = _get_or_404(Account, data["receivable_account_id"], "Account")
        tax_payable_account = (
            _get_or_404(Account, data["tax_payable_account_id"], "Account")
            if data.get("tax_payable_account_id") else None
        )
        warehouse = _get_or_404(Warehouse, data["warehouse_id"], "Warehouse") if data.get("warehouse_id") else None
        invoice_date = data["invoice_date"] or quote.issue_date
        return convert_quote_to_invoice(
            quote=quote, invoice_date=invoice_date, due_date=data["due_date"], receivable_account=receivable_account,
            warehouse=warehouse, tax_payable_account=tax_payable_account, actor=request.user,
        )


class SalesOrderLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalesOrderLine
        fields = [
            "id", "item", "line_number", "description", "hsn_sac_snapshot", "tax_label",
            "quantity", "unit_price", "discount_percent", "tax_rate",
            "line_base", "discount_amount", "taxable_amount", "tax_amount", "line_total",
        ]
        read_only_fields = fields


class SalesOrderLineInputSerializer(serializers.Serializer):
    item_id = serializers.UUIDField()
    description = serializers.CharField(required=False, allow_blank=True, default="")
    quantity = serializers.DecimalField(max_digits=18, decimal_places=4)
    unit_price = serializers.DecimalField(max_digits=18, decimal_places=2)
    discount_percent = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, default=Decimal("0"))
    tax_rate = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, default=Decimal("0"))


class SalesOrderSerializer(serializers.ModelSerializer):
    lines = SalesOrderLineSerializer(many=True, read_only=True)

    class Meta:
        model = SalesOrder
        fields = [
            "id", "customer", "order_number", "status", "source_quote", "order_date",
            "currency", "exchange_rate", "subtotal", "discount_total", "tax_total", "total",
            "notes", "terms", "lines", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "order_number", "status", "source_quote", "subtotal", "discount_total", "tax_total", "total",
            "lines", "created_at", "updated_at",
        ]


class SalesOrderCreateSerializer(serializers.Serializer):
    customer_id = serializers.UUIDField()
    order_date = serializers.DateField()
    currency = serializers.PrimaryKeyRelatedField(queryset=Currency.objects.all(), required=False, default=None)
    exchange_rate = serializers.DecimalField(max_digits=18, decimal_places=8, required=False, default=Decimal("1"))
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    terms = serializers.CharField(required=False, allow_blank=True, default="")
    lines = SalesOrderLineInputSerializer(many=True)

    def create(self, validated_data):
        request = self.context["request"]
        customer = _get_or_404(Customer, validated_data.pop("customer_id"), "Customer")
        lines = _resolve_document_lines(validated_data.pop("lines"))
        return create_sales_order(
            organization=request.organization, customer=customer, actor=request.user, lines=lines, **validated_data
        )


class SalesOrderLinesUpdateSerializer(serializers.Serializer):
    lines = SalesOrderLineInputSerializer(many=True)

    def save(self, **kwargs):
        request = self.context["request"]
        lines = _resolve_document_lines(self.validated_data["lines"])
        return replace_order_lines(order=self.context["order"], lines=lines, actor=request.user)


def _resolve_delivery_lines(raw_lines: list[dict]) -> list[dict]:
    item_ids = {line["item_id"] for line in raw_lines}
    items_by_id = {i.id: i for i in Item.objects.filter(id__in=item_ids)}
    if len(items_by_id) != len(item_ids):
        raise ApplicationError(
            "One or more items were not found in this organization.", code="item_not_found", status_code=404
        )
    order_line_ids = {line["source_order_line_id"] for line in raw_lines if line.get("source_order_line_id")}
    order_lines_by_id = (
        {ol.id: ol for ol in SalesOrderLine.objects.filter(id__in=order_line_ids)} if order_line_ids else {}
    )
    if len(order_lines_by_id) != len(order_line_ids):
        raise ApplicationError(
            "One or more sales order lines were not found in this organization.",
            code="sales_order_line_not_found", status_code=404,
        )
    return [
        {
            "item": items_by_id[line["item_id"]],
            "source_order_line": order_lines_by_id.get(line["source_order_line_id"])
            if line.get("source_order_line_id") else None,
            "description": line.get("description", ""),
            "quantity": line["quantity"],
        }
        for line in raw_lines
    ]


class DeliveryChallanLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryChallanLine
        fields = ["id", "item", "source_order_line", "line_number", "description", "quantity"]
        read_only_fields = fields


class DeliveryChallanLineInputSerializer(serializers.Serializer):
    item_id = serializers.UUIDField()
    source_order_line_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    quantity = serializers.DecimalField(max_digits=18, decimal_places=4)


class DeliveryChallanSerializer(serializers.ModelSerializer):
    lines = DeliveryChallanLineSerializer(many=True, read_only=True)

    class Meta:
        model = DeliveryChallan
        fields = [
            "id", "customer", "challan_number", "status", "source_sales_order", "warehouse", "challan_date",
            "notes", "dispatched_at", "delivered_at", "lines", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "challan_number", "status", "dispatched_at", "delivered_at", "lines", "created_at", "updated_at",
        ]


class DeliveryChallanCreateSerializer(serializers.Serializer):
    customer_id = serializers.UUIDField()
    warehouse_id = serializers.UUIDField()
    challan_date = serializers.DateField()
    source_sales_order_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    lines = DeliveryChallanLineInputSerializer(many=True)

    def create(self, validated_data):
        request = self.context["request"]
        customer = _get_or_404(Customer, validated_data.pop("customer_id"), "Customer")
        warehouse = _get_or_404(Warehouse, validated_data.pop("warehouse_id"), "Warehouse")
        source_sales_order_id = validated_data.pop("source_sales_order_id")
        source_sales_order = (
            _get_or_404(SalesOrder, source_sales_order_id, "SalesOrder") if source_sales_order_id else None
        )
        lines = _resolve_delivery_lines(validated_data.pop("lines"))
        return create_delivery_challan(
            organization=request.organization, customer=customer, warehouse=warehouse,
            source_sales_order=source_sales_order, actor=request.user, lines=lines, **validated_data
        )


class DeliveryChallanLinesUpdateSerializer(serializers.Serializer):
    lines = DeliveryChallanLineInputSerializer(many=True)

    def save(self, **kwargs):
        request = self.context["request"]
        lines = _resolve_delivery_lines(self.validated_data["lines"])
        return replace_delivery_lines(challan=self.context["challan"], lines=lines, actor=request.user)


def _resolve_invoice_lines(raw_lines: list[dict]) -> list[dict]:
    item_ids = {line["item_id"] for line in raw_lines}
    items_by_id = {i.id: i for i in Item.objects.filter(id__in=item_ids)}
    if len(items_by_id) != len(item_ids):
        raise ApplicationError(
            "One or more items were not found in this organization.", code="item_not_found", status_code=404
        )
    challan_line_ids = {
        line["source_delivery_challan_line_id"] for line in raw_lines if line.get("source_delivery_challan_line_id")
    }
    challan_lines_by_id = (
        {cl.id: cl for cl in DeliveryChallanLine.objects.filter(id__in=challan_line_ids)} if challan_line_ids else {}
    )
    if len(challan_lines_by_id) != len(challan_line_ids):
        raise ApplicationError(
            "One or more delivery challan lines were not found in this organization.",
            code="delivery_line_not_found", status_code=404,
        )
    return [
        {
            "item": items_by_id[line["item_id"]],
            "source_delivery_challan_line": challan_lines_by_id.get(line["source_delivery_challan_line_id"])
            if line.get("source_delivery_challan_line_id") else None,
            "description": line.get("description", ""),
            "quantity": line["quantity"],
            "unit_price": line["unit_price"],
            "discount_percent": line.get("discount_percent", Decimal("0")),
            "tax_rate": line.get("tax_rate", Decimal("0")),
        }
        for line in raw_lines
    ]


class InvoiceLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceLine
        fields = [
            "id", "item", "source_delivery_challan_line", "line_number", "description", "hsn_sac_snapshot",
            "tax_label", "quantity", "unit_price", "discount_percent", "tax_rate",
            "line_base", "discount_amount", "taxable_amount", "tax_amount", "line_total",
        ]
        read_only_fields = fields


class InvoiceLineInputSerializer(serializers.Serializer):
    item_id = serializers.UUIDField()
    source_delivery_challan_line_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    quantity = serializers.DecimalField(max_digits=18, decimal_places=4)
    unit_price = serializers.DecimalField(max_digits=18, decimal_places=2)
    discount_percent = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, default=Decimal("0"))
    tax_rate = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, default=Decimal("0"))


class InvoiceSerializer(serializers.ModelSerializer):
    lines = InvoiceLineSerializer(many=True, read_only=True)
    amount_paid = serializers.SerializerMethodField()
    amount_due = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = [
            "id", "customer", "invoice_number", "status", "source_quote", "invoice_date", "due_date", "reference",
            "currency", "exchange_rate", "receivable_account", "tax_payable_account", "warehouse",
            "subtotal", "discount_total", "tax_total", "total", "amount_paid", "amount_due",
            "notes", "terms", "posted_by", "posted_at", "voided_by", "voided_at", "void_reason",
            "lines", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "invoice_number", "status", "source_quote", "subtotal", "discount_total", "tax_total", "total",
            "amount_paid", "amount_due", "posted_by", "posted_at", "voided_by", "voided_at", "void_reason",
            "lines", "created_at", "updated_at",
        ]

    def get_amount_paid(self, invoice):
        from sales.selectors import get_invoice_amount_paid

        return str(get_invoice_amount_paid(invoice=invoice))

    def get_amount_due(self, invoice):
        from sales.selectors import get_invoice_amount_due

        return str(get_invoice_amount_due(invoice=invoice))


class InvoiceCreateSerializer(serializers.Serializer):
    customer_id = serializers.UUIDField()
    invoice_date = serializers.DateField()
    due_date = serializers.DateField()
    reference = serializers.CharField(required=False, allow_blank=True, default="")
    receivable_account_id = serializers.UUIDField()
    tax_payable_account_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    warehouse_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    currency = serializers.PrimaryKeyRelatedField(queryset=Currency.objects.all(), required=False, default=None)
    exchange_rate = serializers.DecimalField(max_digits=18, decimal_places=8, required=False, default=Decimal("1"))
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    terms = serializers.CharField(required=False, allow_blank=True, default="")
    lines = InvoiceLineInputSerializer(many=True)

    def create(self, validated_data):
        request = self.context["request"]
        customer = _get_or_404(Customer, validated_data.pop("customer_id"), "Customer")
        receivable_account = _get_or_404(Account, validated_data.pop("receivable_account_id"), "Account")
        tax_payable_account_id = validated_data.pop("tax_payable_account_id")
        tax_payable_account = (
            _get_or_404(Account, tax_payable_account_id, "Account") if tax_payable_account_id else None
        )
        warehouse_id = validated_data.pop("warehouse_id")
        warehouse = _get_or_404(Warehouse, warehouse_id, "Warehouse") if warehouse_id else None
        lines = _resolve_invoice_lines(validated_data.pop("lines"))
        return create_invoice(
            organization=request.organization, customer=customer, receivable_account=receivable_account,
            tax_payable_account=tax_payable_account, warehouse=warehouse, actor=request.user, lines=lines,
            **validated_data,
        )


class InvoiceLinesUpdateSerializer(serializers.Serializer):
    lines = InvoiceLineInputSerializer(many=True)

    def save(self, **kwargs):
        request = self.context["request"]
        lines = _resolve_invoice_lines(self.validated_data["lines"])
        return replace_invoice_lines(invoice=self.context["invoice"], lines=lines, actor=request.user)


class InvoiceVoidSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="")

    def save(self, **kwargs):
        from sales.services.invoices import void_invoice

        request = self.context["request"]
        invoice = self.context["invoice"]
        return void_invoice(
            invoice_id=invoice.id, organization=request.organization, actor=request.user,
            reason=self.validated_data["reason"],
        )


class PaymentAllocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentAllocation
        fields = ["id", "invoice", "amount"]
        read_only_fields = fields


class PaymentAllocationInputSerializer(serializers.Serializer):
    invoice_id = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=18, decimal_places=2)


class CustomerPaymentSerializer(serializers.ModelSerializer):
    allocations = PaymentAllocationSerializer(many=True, read_only=True)

    class Meta:
        model = CustomerPayment
        fields = [
            "id", "customer", "payment_number", "payment_date", "amount", "currency", "payment_method",
            "reference", "destination_account", "notes", "allocations", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "payment_number", "allocations", "created_at", "updated_at"]


class CustomerPaymentCreateSerializer(serializers.Serializer):
    customer_id = serializers.UUIDField()
    payment_date = serializers.DateField()
    amount = serializers.DecimalField(max_digits=18, decimal_places=2)
    destination_account_id = serializers.UUIDField()
    currency = serializers.PrimaryKeyRelatedField(queryset=Currency.objects.all(), required=False, default=None)
    payment_method = serializers.ChoiceField(
        choices=["cash", "bank_transfer", "cheque", "card", "upi", "other"], required=False, default="other"
    )
    reference = serializers.CharField(required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    unapplied_credit_account_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    allocations = PaymentAllocationInputSerializer(many=True, required=False, default=list)

    def create(self, validated_data):
        request = self.context["request"]
        customer = _get_or_404(Customer, validated_data.pop("customer_id"), "Customer")
        destination_account = _get_or_404(Account, validated_data.pop("destination_account_id"), "Account")
        unapplied_credit_account_id = validated_data.pop("unapplied_credit_account_id")
        unapplied_credit_account = (
            _get_or_404(Account, unapplied_credit_account_id, "Account") if unapplied_credit_account_id else None
        )
        raw_allocations = validated_data.pop("allocations")
        invoice_ids = {a["invoice_id"] for a in raw_allocations}
        invoices_by_id = {i.id: i for i in Invoice.objects.filter(id__in=invoice_ids)}
        if len(invoices_by_id) != len(invoice_ids):
            raise ApplicationError(
                "One or more invoices were not found in this organization.", code="invoice_not_found",
                status_code=404,
            )
        allocations = [{"invoice": invoices_by_id[a["invoice_id"]], "amount": a["amount"]} for a in raw_allocations]

        return record_payment(
            organization=request.organization, customer=customer, destination_account=destination_account,
            unapplied_credit_account=unapplied_credit_account, allocations=allocations, actor=request.user,
            **validated_data,
        )


def _resolve_credit_note_lines(raw_lines: list[dict]) -> list[dict]:
    item_ids = {line["item_id"] for line in raw_lines}
    items_by_id = {i.id: i for i in Item.objects.filter(id__in=item_ids)}
    if len(items_by_id) != len(item_ids):
        raise ApplicationError(
            "One or more items were not found in this organization.", code="item_not_found", status_code=404
        )
    invoice_line_ids = {
        line["source_invoice_line_id"] for line in raw_lines if line.get("source_invoice_line_id")
    }
    invoice_lines_by_id = (
        {il.id: il for il in InvoiceLine.objects.filter(id__in=invoice_line_ids)} if invoice_line_ids else {}
    )
    if len(invoice_lines_by_id) != len(invoice_line_ids):
        raise ApplicationError(
            "One or more invoice lines were not found in this organization.",
            code="invoice_line_not_found", status_code=404,
        )
    return [
        {
            "item": items_by_id[line["item_id"]],
            "source_invoice_line": invoice_lines_by_id.get(line["source_invoice_line_id"])
            if line.get("source_invoice_line_id") else None,
            "description": line.get("description", ""),
            "quantity": line["quantity"],
            "unit_price": line["unit_price"],
            "discount_percent": line.get("discount_percent", Decimal("0")),
            "tax_rate": line.get("tax_rate", Decimal("0")),
            "restock": line.get("restock", False),
            "unit_cost": line.get("unit_cost"),
        }
        for line in raw_lines
    ]


class CreditNoteLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = CreditNoteLine
        fields = [
            "id", "item", "source_invoice_line", "line_number", "description", "hsn_sac_snapshot", "tax_label",
            "quantity", "unit_price", "discount_percent", "tax_rate",
            "line_base", "discount_amount", "taxable_amount", "tax_amount", "line_total",
            "restock", "unit_cost",
        ]
        read_only_fields = fields


class CreditNoteLineInputSerializer(serializers.Serializer):
    item_id = serializers.UUIDField()
    source_invoice_line_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    quantity = serializers.DecimalField(max_digits=18, decimal_places=4)
    unit_price = serializers.DecimalField(max_digits=18, decimal_places=2)
    discount_percent = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, default=Decimal("0"))
    tax_rate = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, default=Decimal("0"))
    restock = serializers.BooleanField(required=False, default=False)
    unit_cost = serializers.DecimalField(
        max_digits=18, decimal_places=4, required=False, allow_null=True, default=None
    )


class CreditNoteSerializer(serializers.ModelSerializer):
    lines = CreditNoteLineSerializer(many=True, read_only=True)

    class Meta:
        model = CreditNote
        fields = [
            "id", "customer", "credit_note_number", "status", "reason", "source_invoice", "credit_note_date",
            "reference", "currency", "exchange_rate", "receivable_account", "tax_payable_account",
            "unapplied_credit_account", "warehouse", "subtotal", "discount_total", "tax_total", "total",
            "notes", "issued_by", "issued_at", "voided_by", "voided_at", "lines", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "credit_note_number", "status", "subtotal", "discount_total", "tax_total", "total",
            "issued_by", "issued_at", "voided_by", "voided_at", "lines", "created_at", "updated_at",
        ]


class CreditNoteCreateSerializer(serializers.Serializer):
    customer_id = serializers.UUIDField()
    credit_note_date = serializers.DateField()
    reason = serializers.ChoiceField(
        choices=["return", "pricing_error", "discount", "goodwill", "other"], required=False, default="other"
    )
    source_invoice_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    reference = serializers.CharField(required=False, allow_blank=True, default="")
    currency = serializers.PrimaryKeyRelatedField(queryset=Currency.objects.all(), required=False, default=None)
    exchange_rate = serializers.DecimalField(max_digits=18, decimal_places=8, required=False, default=Decimal("1"))
    receivable_account_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    tax_payable_account_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    unapplied_credit_account_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    warehouse_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    lines = CreditNoteLineInputSerializer(many=True)

    def create(self, validated_data):
        request = self.context["request"]
        customer = _get_or_404(Customer, validated_data.pop("customer_id"), "Customer")
        source_invoice_id = validated_data.pop("source_invoice_id")
        source_invoice = _get_or_404(Invoice, source_invoice_id, "Invoice") if source_invoice_id else None
        receivable_account_id = validated_data.pop("receivable_account_id")
        receivable_account = _get_or_404(Account, receivable_account_id, "Account") if receivable_account_id else None
        tax_payable_account_id = validated_data.pop("tax_payable_account_id")
        tax_payable_account = (
            _get_or_404(Account, tax_payable_account_id, "Account") if tax_payable_account_id else None
        )
        unapplied_credit_account_id = validated_data.pop("unapplied_credit_account_id")
        unapplied_credit_account = (
            _get_or_404(Account, unapplied_credit_account_id, "Account") if unapplied_credit_account_id else None
        )
        warehouse_id = validated_data.pop("warehouse_id")
        warehouse = _get_or_404(Warehouse, warehouse_id, "Warehouse") if warehouse_id else None
        lines = _resolve_credit_note_lines(validated_data.pop("lines"))

        return create_credit_note(
            organization=request.organization, customer=customer, source_invoice=source_invoice,
            receivable_account=receivable_account, tax_payable_account=tax_payable_account,
            unapplied_credit_account=unapplied_credit_account, warehouse=warehouse, actor=request.user,
            lines=lines, **validated_data,
        )


class CreditNoteLinesUpdateSerializer(serializers.Serializer):
    lines = CreditNoteLineInputSerializer(many=True)

    def save(self, **kwargs):
        request = self.context["request"]
        lines = _resolve_credit_note_lines(self.validated_data["lines"])
        return replace_credit_note_lines(credit_note=self.context["credit_note"], lines=lines, actor=request.user)


def _resolve_recurring_lines(raw_lines: list[dict]) -> list[dict]:
    item_ids = {line["item_id"] for line in raw_lines}
    items_by_id = {i.id: i for i in Item.objects.filter(id__in=item_ids)}
    if len(items_by_id) != len(item_ids):
        raise ApplicationError(
            "One or more items were not found in this organization.", code="item_not_found", status_code=404
        )
    return [
        {
            "item": items_by_id[line["item_id"]],
            "description": line.get("description", ""),
            "quantity": line["quantity"],
            "unit_price": line["unit_price"],
            "discount_percent": line.get("discount_percent", Decimal("0")),
            "tax_rate": line.get("tax_rate", Decimal("0")),
        }
        for line in raw_lines
    ]


class RecurringInvoiceTemplateLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecurringInvoiceTemplateLine
        fields = ["id", "item", "line_number", "description", "quantity", "unit_price", "discount_percent", "tax_rate"]
        read_only_fields = fields


class RecurringInvoiceTemplateLineInputSerializer(serializers.Serializer):
    item_id = serializers.UUIDField()
    description = serializers.CharField(required=False, allow_blank=True, default="")
    quantity = serializers.DecimalField(max_digits=18, decimal_places=4)
    unit_price = serializers.DecimalField(max_digits=18, decimal_places=2)
    discount_percent = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, default=Decimal("0"))
    tax_rate = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, default=Decimal("0"))


class RecurringInvoiceTemplateSerializer(serializers.ModelSerializer):
    lines = RecurringInvoiceTemplateLineSerializer(many=True, read_only=True)

    class Meta:
        model = RecurringInvoiceTemplate
        fields = [
            "id", "customer", "frequency", "start_date", "end_date", "next_run_at", "is_active", "due_days",
            "receivable_account", "tax_payable_account", "warehouse", "currency", "exchange_rate",
            "reference", "notes", "terms", "lines", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "next_run_at", "lines", "created_at", "updated_at"]


class RecurringInvoiceTemplateCreateSerializer(serializers.Serializer):
    customer_id = serializers.UUIDField()
    frequency = serializers.ChoiceField(choices=["weekly", "monthly", "quarterly", "yearly"])
    start_date = serializers.DateField()
    end_date = serializers.DateField(required=False, allow_null=True, default=None)
    due_days = serializers.IntegerField(required=False, default=0, min_value=0)
    is_active = serializers.BooleanField(required=False, default=True)
    receivable_account_id = serializers.UUIDField()
    tax_payable_account_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    warehouse_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    currency = serializers.PrimaryKeyRelatedField(queryset=Currency.objects.all(), required=False, default=None)
    exchange_rate = serializers.DecimalField(max_digits=18, decimal_places=8, required=False, default=Decimal("1"))
    reference = serializers.CharField(required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    terms = serializers.CharField(required=False, allow_blank=True, default="")
    lines = RecurringInvoiceTemplateLineInputSerializer(many=True)

    def create(self, validated_data):
        request = self.context["request"]
        customer = _get_or_404(Customer, validated_data.pop("customer_id"), "Customer")
        receivable_account = _get_or_404(Account, validated_data.pop("receivable_account_id"), "Account")
        tax_payable_account_id = validated_data.pop("tax_payable_account_id")
        tax_payable_account = (
            _get_or_404(Account, tax_payable_account_id, "Account") if tax_payable_account_id else None
        )
        warehouse_id = validated_data.pop("warehouse_id")
        warehouse = _get_or_404(Warehouse, warehouse_id, "Warehouse") if warehouse_id else None
        lines = _resolve_recurring_lines(validated_data.pop("lines"))

        return create_recurring_template(
            organization=request.organization, customer=customer, receivable_account=receivable_account,
            tax_payable_account=tax_payable_account, warehouse=warehouse, actor=request.user, lines=lines,
            **validated_data,
        )


class RecurringInvoiceTemplateUpdateSerializer(serializers.Serializer):
    end_date = serializers.DateField(required=False, allow_null=True)
    due_days = serializers.IntegerField(required=False, min_value=0)
    reference = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    terms = serializers.CharField(required=False, allow_blank=True)
    lines = RecurringInvoiceTemplateLineInputSerializer(many=True, required=False)

    def save(self, **kwargs):
        request = self.context["request"]
        data = dict(self.validated_data)
        lines = data.pop("lines", None)
        if lines is not None:
            lines = _resolve_recurring_lines(lines)
        return update_recurring_template(
            template=self.context["template"], lines=lines, actor=request.user, **data
        )
