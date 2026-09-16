"""Purchases API serializers.

Two conventions inherited from `sales/api/serializers.py`, both of which
matter for correctness rather than style:

1. Tenant-scoped model references (`vendor_id`, `item_id`, account ids, ...)
   are plain UUID fields resolved inside `create()`/`save()` at request
   time, never `PrimaryKeyRelatedField(queryset=Model.objects.all())` as a
   class attribute. A class-level queryset is evaluated once at import time,
   before any tenant context exists, permanently baking in `.none()` (see
   core/CLAUDE.md). `accounts.Currency` is the one safe exception: it is
   global reference data, not org-scoped.

2. Create-serializers are plain `Serializer`s that call a domain service and
   return the model instance; the VIEW then renders it with the read
   serializer. Totals are never accepted from the client.
"""

from decimal import Decimal

from rest_framework import serializers

from accounting.models.account import Account
from accounts.models import Currency
from core.enums import PaymentMethod, RecurringFrequency
from core.exceptions import ApplicationError
from inventory.models.warehouse import Warehouse
from items.models.item import Item
from purchases.models.bill import Bill, BillLine
from purchases.models.expense import Expense
from purchases.models.goods_receipt import GoodsReceipt, GoodsReceiptLine
from purchases.models.payment import VendorPayment, VendorPaymentAllocation
from purchases.models.purchase_order import PurchaseOrder, PurchaseOrderLine
from purchases.models.recurring import (
    RecurringBillTemplate,
    RecurringBillTemplateLine,
    RecurringExpenseTemplate,
)
from purchases.models.vendor import Vendor
from purchases.models.vendor_credit import VendorCredit, VendorCreditLine
from purchases.services.bills import create_bill, create_bill_from_goods_receipt, replace_bill_lines, void_bill
from purchases.services.expenses import create_expense, update_expense, void_expense
from purchases.services.goods_receipts import create_goods_receipt, replace_receipt_lines
from purchases.services.payments import record_vendor_payment
from purchases.services.purchase_orders import create_purchase_order, replace_order_lines
from purchases.services.recurring import (
    create_recurring_bill_template,
    create_recurring_expense_template,
    update_recurring_bill_template,
    update_recurring_expense_template,
)
from purchases.services.vendor_credits import create_vendor_credit, replace_vendor_credit_lines
from purchases.services.vendors import create_vendor, update_vendor


def _get_or_404(model, pk, label: str):
    try:
        return model.objects.get(pk=pk)
    except model.DoesNotExist:
        raise ApplicationError(
            f"{label} not found.", code=f"{label.lower().replace(' ', '_')}_not_found", status_code=404
        )


def _maybe(model, pk, label: str):
    return _get_or_404(model, pk, label) if pk else None


def _resolve_items(raw_lines: list[dict]) -> dict:
    item_ids = {line["item_id"] for line in raw_lines}
    items_by_id = {i.id: i for i in Item.objects.filter(id__in=item_ids)}
    if len(items_by_id) != len(item_ids):
        raise ApplicationError(
            "One or more items were not found in this organization.", code="item_not_found", status_code=404
        )
    return items_by_id


def _resolve_priced_lines(raw_lines: list[dict]) -> list[dict]:
    items_by_id = _resolve_items(raw_lines)
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


# ------------------------------------------------------------- vendors


class VendorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vendor
        fields = [
            "id", "vendor_code", "display_name", "legal_name", "email", "phone",
            "gstin", "pan", "billing_address", "shipping_address", "currency",
            "payment_terms_days", "default_payable_account", "is_active", "notes",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def create(self, validated_data):
        request = self.context["request"]
        return create_vendor(organization=request.organization, actor=request.user, **validated_data)

    def update(self, instance, validated_data):
        request = self.context["request"]
        return update_vendor(vendor=instance, actor=request.user, **validated_data)


# ----------------------------------------------------- purchase orders


class PurchaseOrderLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = PurchaseOrderLine
        fields = [
            "id", "item", "line_number", "description", "hsn_sac_snapshot", "tax_label",
            "quantity", "unit_price", "discount_percent", "tax_rate",
            "line_base", "discount_amount", "taxable_amount", "tax_amount", "line_total",
        ]
        read_only_fields = fields


class PricedLineInputSerializer(serializers.Serializer):
    item_id = serializers.UUIDField()
    description = serializers.CharField(required=False, allow_blank=True, default="")
    quantity = serializers.DecimalField(max_digits=18, decimal_places=4)
    unit_price = serializers.DecimalField(max_digits=18, decimal_places=2)
    discount_percent = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, default=Decimal("0"))
    tax_rate = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, default=Decimal("0"))


class PurchaseOrderSerializer(serializers.ModelSerializer):
    lines = PurchaseOrderLineSerializer(many=True, read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = [
            "id", "vendor", "order_number", "status", "order_date", "expected_date", "reference",
            "warehouse", "currency", "exchange_rate",
            "subtotal", "discount_total", "tax_total", "total",
            "notes", "terms", "lines", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "order_number", "status", "subtotal", "discount_total", "tax_total", "total",
            "lines", "created_at", "updated_at",
        ]


class PurchaseOrderCreateSerializer(serializers.Serializer):
    vendor_id = serializers.UUIDField()
    order_date = serializers.DateField()
    expected_date = serializers.DateField(required=False, allow_null=True, default=None)
    warehouse_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    currency = serializers.PrimaryKeyRelatedField(queryset=Currency.objects.all(), required=False, default=None)
    exchange_rate = serializers.DecimalField(max_digits=18, decimal_places=8, required=False, default=Decimal("1"))
    reference = serializers.CharField(required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    terms = serializers.CharField(required=False, allow_blank=True, default="")
    lines = PricedLineInputSerializer(many=True)

    def create(self, validated_data):
        request = self.context["request"]
        vendor = _get_or_404(Vendor, validated_data.pop("vendor_id"), "Vendor")
        warehouse = _maybe(Warehouse, validated_data.pop("warehouse_id"), "Warehouse")
        lines = _resolve_priced_lines(validated_data.pop("lines"))
        return create_purchase_order(
            organization=request.organization, vendor=vendor, warehouse=warehouse, lines=lines,
            actor=request.user, **validated_data,
        )


class PurchaseOrderLinesUpdateSerializer(serializers.Serializer):
    lines = PricedLineInputSerializer(many=True)

    def save(self, **kwargs):
        request = self.context["request"]
        lines = _resolve_priced_lines(self.validated_data["lines"])
        return replace_order_lines(order=self.context["order"], lines=lines, actor=request.user)


# ------------------------------------------------------ goods receipts


class GoodsReceiptLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = GoodsReceiptLine
        fields = [
            "id", "item", "source_order_line", "line_number", "description", "quantity", "unit_cost",
        ]
        read_only_fields = fields


class GoodsReceiptLineInputSerializer(serializers.Serializer):
    item_id = serializers.UUIDField()
    source_order_line_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    quantity = serializers.DecimalField(max_digits=18, decimal_places=4)
    # Optional: falls back to the linked PO line's agreed price. The service
    # refuses a line with neither rather than defaulting to zero.
    unit_cost = serializers.DecimalField(max_digits=18, decimal_places=4, required=False, allow_null=True, default=None)


class GoodsReceiptSerializer(serializers.ModelSerializer):
    lines = GoodsReceiptLineSerializer(many=True, read_only=True)

    class Meta:
        model = GoodsReceipt
        fields = [
            "id", "vendor", "receipt_number", "status", "source_purchase_order", "warehouse",
            "receipt_date", "vendor_document_number", "notes", "received_at",
            "lines", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "receipt_number", "status", "received_at", "lines", "created_at", "updated_at",
        ]


def _resolve_receipt_lines(raw_lines: list[dict]) -> list[dict]:
    items_by_id = _resolve_items(raw_lines)
    return [
        {
            "item": items_by_id[line["item_id"]],
            "source_order_line": _maybe(PurchaseOrderLine, line.get("source_order_line_id"), "Purchase order line"),
            "description": line.get("description", ""),
            "quantity": line["quantity"],
            "unit_cost": line.get("unit_cost"),
        }
        for line in raw_lines
    ]


class GoodsReceiptCreateSerializer(serializers.Serializer):
    vendor_id = serializers.UUIDField()
    warehouse_id = serializers.UUIDField()
    receipt_date = serializers.DateField()
    source_purchase_order_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    vendor_document_number = serializers.CharField(required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    lines = GoodsReceiptLineInputSerializer(many=True)

    def create(self, validated_data):
        request = self.context["request"]
        vendor = _get_or_404(Vendor, validated_data.pop("vendor_id"), "Vendor")
        warehouse = _get_or_404(Warehouse, validated_data.pop("warehouse_id"), "Warehouse")
        source_purchase_order = _maybe(
            PurchaseOrder, validated_data.pop("source_purchase_order_id"), "Purchase order"
        )
        lines = _resolve_receipt_lines(validated_data.pop("lines"))
        return create_goods_receipt(
            organization=request.organization, vendor=vendor, warehouse=warehouse,
            source_purchase_order=source_purchase_order, lines=lines, actor=request.user, **validated_data,
        )


class GoodsReceiptLinesUpdateSerializer(serializers.Serializer):
    lines = GoodsReceiptLineInputSerializer(many=True)

    def save(self, **kwargs):
        request = self.context["request"]
        lines = _resolve_receipt_lines(self.validated_data["lines"])
        return replace_receipt_lines(receipt=self.context["receipt"], lines=lines, actor=request.user)


# --------------------------------------------------------------- bills


class BillLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = BillLine
        fields = [
            "id", "item", "source_goods_receipt_line", "source_order_line", "expense_account",
            "line_number", "description", "hsn_sac_snapshot", "tax_label",
            "quantity", "unit_price", "discount_percent", "tax_rate",
            "line_base", "discount_amount", "taxable_amount", "tax_amount", "line_total",
        ]
        read_only_fields = fields


class BillLineInputSerializer(PricedLineInputSerializer):
    source_goods_receipt_line_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    source_order_line_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    expense_account_id = serializers.UUIDField(required=False, allow_null=True, default=None)


class BillSerializer(serializers.ModelSerializer):
    lines = BillLineSerializer(many=True, read_only=True)
    amount_paid = serializers.SerializerMethodField()
    amount_due = serializers.SerializerMethodField()

    class Meta:
        model = Bill
        fields = [
            "id", "vendor", "bill_number", "vendor_bill_number", "status", "source_purchase_order",
            "bill_date", "due_date", "reference", "currency", "exchange_rate",
            "payable_account", "tax_recoverable_account", "price_variance_account", "warehouse",
            "accounting_journal", "subtotal", "discount_total", "tax_total", "total",
            "amount_paid", "amount_due", "notes", "lines", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "bill_number", "status", "accounting_journal",
            "subtotal", "discount_total", "tax_total", "total",
            "amount_paid", "amount_due", "lines", "created_at", "updated_at",
        ]

    # Derived on read, never stored — see purchases/selectors.py.
    def get_amount_paid(self, obj):
        from purchases.selectors import get_bill_amount_paid

        return get_bill_amount_paid(bill=obj)

    def get_amount_due(self, obj):
        from purchases.selectors import get_bill_amount_due

        return get_bill_amount_due(bill=obj)


def _resolve_bill_lines(raw_lines: list[dict]) -> list[dict]:
    items_by_id = _resolve_items(raw_lines)
    return [
        {
            "item": items_by_id[line["item_id"]],
            "source_goods_receipt_line": _maybe(
                GoodsReceiptLine, line.get("source_goods_receipt_line_id"), "Goods receipt line"
            ),
            "source_order_line": _maybe(PurchaseOrderLine, line.get("source_order_line_id"), "Purchase order line"),
            "expense_account": _maybe(Account, line.get("expense_account_id"), "Account"),
            "description": line.get("description", ""),
            "quantity": line["quantity"],
            "unit_price": line["unit_price"],
            "discount_percent": line.get("discount_percent", Decimal("0")),
            "tax_rate": line.get("tax_rate", Decimal("0")),
        }
        for line in raw_lines
    ]


class BillCreateSerializer(serializers.Serializer):
    vendor_id = serializers.UUIDField()
    bill_date = serializers.DateField()
    due_date = serializers.DateField()
    payable_account_id = serializers.UUIDField()
    vendor_bill_number = serializers.CharField(required=False, allow_blank=True, default="")
    reference = serializers.CharField(required=False, allow_blank=True, default="")
    source_purchase_order_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    warehouse_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    tax_recoverable_account_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    price_variance_account_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    currency = serializers.PrimaryKeyRelatedField(queryset=Currency.objects.all(), required=False, default=None)
    exchange_rate = serializers.DecimalField(max_digits=18, decimal_places=8, required=False, default=Decimal("1"))
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    lines = BillLineInputSerializer(many=True)

    def create(self, validated_data):
        request = self.context["request"]
        vendor = _get_or_404(Vendor, validated_data.pop("vendor_id"), "Vendor")
        payable_account = _get_or_404(Account, validated_data.pop("payable_account_id"), "Account")
        return create_bill(
            organization=request.organization,
            vendor=vendor,
            payable_account=payable_account,
            warehouse=_maybe(Warehouse, validated_data.pop("warehouse_id"), "Warehouse"),
            tax_recoverable_account=_maybe(
                Account, validated_data.pop("tax_recoverable_account_id"), "Account"
            ),
            price_variance_account=_maybe(Account, validated_data.pop("price_variance_account_id"), "Account"),
            source_purchase_order=_maybe(
                PurchaseOrder, validated_data.pop("source_purchase_order_id"), "Purchase order"
            ),
            lines=_resolve_bill_lines(validated_data.pop("lines")),
            actor=request.user,
            **validated_data,
        )


class BillLinesUpdateSerializer(serializers.Serializer):
    lines = BillLineInputSerializer(many=True)

    def save(self, **kwargs):
        request = self.context["request"]
        lines = _resolve_bill_lines(self.validated_data["lines"])
        return replace_bill_lines(bill=self.context["bill"], lines=lines, actor=request.user)


class BillFromGoodsReceiptSerializer(serializers.Serializer):
    bill_date = serializers.DateField()
    due_date = serializers.DateField()
    payable_account_id = serializers.UUIDField()
    tax_recoverable_account_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    price_variance_account_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    vendor_bill_number = serializers.CharField(required=False, allow_blank=True, default="")

    def save(self, **kwargs):
        request = self.context["request"]
        data = self.validated_data
        return create_bill_from_goods_receipt(
            receipt=self.context["receipt"],
            bill_date=data["bill_date"],
            due_date=data["due_date"],
            payable_account=_get_or_404(Account, data["payable_account_id"], "Account"),
            tax_recoverable_account=_maybe(Account, data["tax_recoverable_account_id"], "Account"),
            price_variance_account=_maybe(Account, data["price_variance_account_id"], "Account"),
            vendor_bill_number=data["vendor_bill_number"],
            actor=request.user,
        )


class BillVoidSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="")

    def save(self, **kwargs):
        request = self.context["request"]
        return void_bill(
            bill_id=self.context["bill"].id, organization=request.organization,
            actor=request.user, reason=self.validated_data["reason"],
        )


# ------------------------------------------------------------ expenses


class ExpenseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Expense
        fields = [
            "id", "vendor", "expense_number", "status", "expense_date", "reference", "description",
            "currency", "exchange_rate", "expense_account", "paid_through_account",
            "tax_recoverable_account", "accounting_journal",
            "amount", "tax_rate", "tax_amount", "total",
            "is_billable", "customer", "project", "notes", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "expense_number", "status", "accounting_journal", "tax_amount", "total",
            "created_at", "updated_at",
        ]


class ExpenseCreateSerializer(serializers.Serializer):
    expense_date = serializers.DateField()
    amount = serializers.DecimalField(max_digits=18, decimal_places=2)
    expense_account_id = serializers.UUIDField()
    paid_through_account_id = serializers.UUIDField()
    vendor_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    tax_rate = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, default=Decimal("0"))
    tax_recoverable_account_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    currency = serializers.PrimaryKeyRelatedField(queryset=Currency.objects.all(), required=False, default=None)
    exchange_rate = serializers.DecimalField(max_digits=18, decimal_places=8, required=False, default=Decimal("1"))
    reference = serializers.CharField(required=False, allow_blank=True, default="")
    description = serializers.CharField(required=False, allow_blank=True, default="")
    is_billable = serializers.BooleanField(required=False, default=False)
    customer_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    # Attributes the cost to a project for profitability (Phase 5).
    project_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def create(self, validated_data):
        from projects.models.project import Project
        from sales.models.customer import Customer

        request = self.context["request"]
        return create_expense(
            organization=request.organization,
            expense_account=_get_or_404(Account, validated_data.pop("expense_account_id"), "Account"),
            paid_through_account=_get_or_404(Account, validated_data.pop("paid_through_account_id"), "Account"),
            tax_recoverable_account=_maybe(Account, validated_data.pop("tax_recoverable_account_id"), "Account"),
            vendor=_maybe(Vendor, validated_data.pop("vendor_id"), "Vendor"),
            customer=_maybe(Customer, validated_data.pop("customer_id"), "Customer"),
            project=_maybe(Project, validated_data.pop("project_id"), "Project"),
            actor=request.user,
            **validated_data,
        )


class ExpenseUpdateSerializer(serializers.Serializer):
    """DRAFT-only header edits. `tax_amount`/`total` are never accepted — the
    service recomputes them from amount/tax_rate."""

    expense_date = serializers.DateField(required=False)
    amount = serializers.DecimalField(max_digits=18, decimal_places=2, required=False)
    tax_rate = serializers.DecimalField(max_digits=5, decimal_places=2, required=False)
    reference = serializers.CharField(required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)
    is_billable = serializers.BooleanField(required=False)
    notes = serializers.CharField(required=False, allow_blank=True)

    def save(self, **kwargs):
        request = self.context["request"]
        return update_expense(expense=self.context["expense"], actor=request.user, **self.validated_data)


class ExpenseVoidSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="")

    def save(self, **kwargs):
        request = self.context["request"]
        return void_expense(
            expense_id=self.context["expense"].id, organization=request.organization,
            actor=request.user, reason=self.validated_data["reason"],
        )


# ------------------------------------------------------------ payments


class VendorPaymentAllocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = VendorPaymentAllocation
        fields = ["id", "bill", "amount"]
        read_only_fields = fields


class VendorPaymentSerializer(serializers.ModelSerializer):
    allocations = VendorPaymentAllocationSerializer(many=True, read_only=True)

    class Meta:
        model = VendorPayment
        fields = [
            "id", "vendor", "payment_number", "payment_date", "amount", "currency",
            "payment_method", "reference", "source_account", "accounting_journal",
            "notes", "allocations", "created_at", "updated_at",
        ]
        read_only_fields = fields


class VendorPaymentAllocationInputSerializer(serializers.Serializer):
    bill_id = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=18, decimal_places=2)


class VendorPaymentCreateSerializer(serializers.Serializer):
    vendor_id = serializers.UUIDField()
    payment_date = serializers.DateField()
    amount = serializers.DecimalField(max_digits=18, decimal_places=2)
    source_account_id = serializers.UUIDField()
    allocations = VendorPaymentAllocationInputSerializer(many=True, required=False, default=list)
    vendor_advance_account_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    currency = serializers.PrimaryKeyRelatedField(queryset=Currency.objects.all(), required=False, default=None)
    payment_method = serializers.ChoiceField(
        choices=PaymentMethod.choices, required=False, default=PaymentMethod.OTHER
    )
    reference = serializers.CharField(required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def create(self, validated_data):
        request = self.context["request"]
        allocations = [
            {"bill": _get_or_404(Bill, entry["bill_id"], "Bill"), "amount": entry["amount"]}
            for entry in validated_data.pop("allocations")
        ]
        return record_vendor_payment(
            organization=request.organization,
            vendor=_get_or_404(Vendor, validated_data.pop("vendor_id"), "Vendor"),
            source_account=_get_or_404(Account, validated_data.pop("source_account_id"), "Account"),
            vendor_advance_account=_maybe(Account, validated_data.pop("vendor_advance_account_id"), "Account"),
            allocations=allocations,
            actor=request.user,
            **validated_data,
        )


# ------------------------------------------------------ vendor credits


class VendorCreditLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = VendorCreditLine
        fields = [
            "id", "item", "source_bill_line", "expense_account", "line_number",
            "description", "hsn_sac_snapshot", "tax_label",
            "quantity", "unit_price", "discount_percent", "tax_rate",
            "line_base", "discount_amount", "taxable_amount", "tax_amount", "line_total",
            "return_stock", "unit_cost",
        ]
        read_only_fields = fields


class VendorCreditLineInputSerializer(PricedLineInputSerializer):
    source_bill_line_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    expense_account_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    return_stock = serializers.BooleanField(required=False, default=False)
    unit_cost = serializers.DecimalField(max_digits=18, decimal_places=4, required=False, allow_null=True, default=None)


class VendorCreditSerializer(serializers.ModelSerializer):
    lines = VendorCreditLineSerializer(many=True, read_only=True)

    class Meta:
        model = VendorCredit
        fields = [
            "id", "vendor", "credit_number", "vendor_credit_number", "status", "reason", "source_bill",
            "credit_date", "reference", "currency", "exchange_rate",
            "payable_account", "tax_recoverable_account", "unapplied_credit_account", "warehouse",
            "accounting_journal", "subtotal", "discount_total", "tax_total", "total",
            "amount_applied_to_bill", "notes", "lines", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "credit_number", "status", "accounting_journal",
            "subtotal", "discount_total", "tax_total", "total", "amount_applied_to_bill",
            "lines", "created_at", "updated_at",
        ]


def _resolve_vendor_credit_lines(raw_lines: list[dict]) -> list[dict]:
    items_by_id = _resolve_items(raw_lines)
    return [
        {
            "item": items_by_id[line["item_id"]],
            "source_bill_line": _maybe(BillLine, line.get("source_bill_line_id"), "Bill line"),
            "expense_account": _maybe(Account, line.get("expense_account_id"), "Account"),
            "description": line.get("description", ""),
            "quantity": line["quantity"],
            "unit_price": line["unit_price"],
            "discount_percent": line.get("discount_percent", Decimal("0")),
            "tax_rate": line.get("tax_rate", Decimal("0")),
            "return_stock": line.get("return_stock", False),
            "unit_cost": line.get("unit_cost"),
        }
        for line in raw_lines
    ]


class VendorCreditCreateSerializer(serializers.Serializer):
    vendor_id = serializers.UUIDField()
    credit_date = serializers.DateField()
    source_bill_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    reason = serializers.CharField(required=False, default="other")
    vendor_credit_number = serializers.CharField(required=False, allow_blank=True, default="")
    reference = serializers.CharField(required=False, allow_blank=True, default="")
    payable_account_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    tax_recoverable_account_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    unapplied_credit_account_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    warehouse_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    currency = serializers.PrimaryKeyRelatedField(queryset=Currency.objects.all(), required=False, default=None)
    exchange_rate = serializers.DecimalField(max_digits=18, decimal_places=8, required=False, default=Decimal("1"))
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    lines = VendorCreditLineInputSerializer(many=True)

    def create(self, validated_data):
        request = self.context["request"]
        return create_vendor_credit(
            organization=request.organization,
            vendor=_get_or_404(Vendor, validated_data.pop("vendor_id"), "Vendor"),
            source_bill=_maybe(Bill, validated_data.pop("source_bill_id"), "Bill"),
            payable_account=_maybe(Account, validated_data.pop("payable_account_id"), "Account"),
            tax_recoverable_account=_maybe(Account, validated_data.pop("tax_recoverable_account_id"), "Account"),
            unapplied_credit_account=_maybe(Account, validated_data.pop("unapplied_credit_account_id"), "Account"),
            warehouse=_maybe(Warehouse, validated_data.pop("warehouse_id"), "Warehouse"),
            lines=_resolve_vendor_credit_lines(validated_data.pop("lines")),
            actor=request.user,
            **validated_data,
        )


class VendorCreditLinesUpdateSerializer(serializers.Serializer):
    lines = VendorCreditLineInputSerializer(many=True)

    def save(self, **kwargs):
        request = self.context["request"]
        lines = _resolve_vendor_credit_lines(self.validated_data["lines"])
        return replace_vendor_credit_lines(
            vendor_credit=self.context["vendor_credit"], lines=lines, actor=request.user
        )


# ----------------------------------------------------------- recurring


class RecurringBillTemplateLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecurringBillTemplateLine
        fields = [
            "id", "item", "expense_account", "line_number", "description",
            "quantity", "unit_price", "discount_percent", "tax_rate",
        ]
        read_only_fields = fields


class RecurringBillTemplateLineInputSerializer(PricedLineInputSerializer):
    expense_account_id = serializers.UUIDField(required=False, allow_null=True, default=None)


class RecurringBillTemplateSerializer(serializers.ModelSerializer):
    lines = RecurringBillTemplateLineSerializer(many=True, read_only=True)

    class Meta:
        model = RecurringBillTemplate
        fields = [
            "id", "vendor", "frequency", "start_date", "end_date", "next_run_at", "is_active", "due_days",
            "payable_account", "tax_recoverable_account", "currency", "exchange_rate",
            "reference", "notes", "lines", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "next_run_at", "lines", "created_at", "updated_at"]


def _resolve_recurring_bill_lines(raw_lines: list[dict]) -> list[dict]:
    items_by_id = _resolve_items(raw_lines)
    return [
        {
            "item": items_by_id[line["item_id"]],
            "expense_account": _maybe(Account, line.get("expense_account_id"), "Account"),
            "description": line.get("description", ""),
            "quantity": line["quantity"],
            "unit_price": line["unit_price"],
            "discount_percent": line.get("discount_percent", Decimal("0")),
            "tax_rate": line.get("tax_rate", Decimal("0")),
        }
        for line in raw_lines
    ]


class RecurringBillTemplateCreateSerializer(serializers.Serializer):
    vendor_id = serializers.UUIDField()
    frequency = serializers.ChoiceField(choices=RecurringFrequency.choices)
    start_date = serializers.DateField()
    end_date = serializers.DateField(required=False, allow_null=True, default=None)
    due_days = serializers.IntegerField(required=False, default=0, min_value=0)
    payable_account_id = serializers.UUIDField()
    tax_recoverable_account_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    currency = serializers.PrimaryKeyRelatedField(queryset=Currency.objects.all(), required=False, default=None)
    exchange_rate = serializers.DecimalField(max_digits=18, decimal_places=8, required=False, default=Decimal("1"))
    reference = serializers.CharField(required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    is_active = serializers.BooleanField(required=False, default=True)
    lines = RecurringBillTemplateLineInputSerializer(many=True)

    def create(self, validated_data):
        request = self.context["request"]
        return create_recurring_bill_template(
            organization=request.organization,
            vendor=_get_or_404(Vendor, validated_data.pop("vendor_id"), "Vendor"),
            payable_account=_get_or_404(Account, validated_data.pop("payable_account_id"), "Account"),
            tax_recoverable_account=_maybe(Account, validated_data.pop("tax_recoverable_account_id"), "Account"),
            lines=_resolve_recurring_bill_lines(validated_data.pop("lines")),
            actor=request.user,
            **validated_data,
        )


class RecurringBillTemplateUpdateSerializer(serializers.Serializer):
    frequency = serializers.ChoiceField(choices=RecurringFrequency.choices, required=False)
    end_date = serializers.DateField(required=False, allow_null=True)
    due_days = serializers.IntegerField(required=False, min_value=0)
    reference = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    is_active = serializers.BooleanField(required=False)
    lines = RecurringBillTemplateLineInputSerializer(many=True, required=False)

    def save(self, **kwargs):
        request = self.context["request"]
        data = dict(self.validated_data)
        raw_lines = data.pop("lines", None)
        lines = _resolve_recurring_bill_lines(raw_lines) if raw_lines is not None else None
        return update_recurring_bill_template(
            template=self.context["template"], lines=lines, actor=request.user, **data
        )


class RecurringExpenseTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecurringExpenseTemplate
        fields = [
            "id", "vendor", "frequency", "start_date", "end_date", "next_run_at", "is_active",
            "expense_account", "paid_through_account", "tax_recoverable_account",
            "currency", "exchange_rate", "amount", "tax_rate",
            "description", "reference", "notes", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "next_run_at", "created_at", "updated_at"]


class RecurringExpenseTemplateCreateSerializer(serializers.Serializer):
    frequency = serializers.ChoiceField(choices=RecurringFrequency.choices)
    start_date = serializers.DateField()
    amount = serializers.DecimalField(max_digits=18, decimal_places=2)
    expense_account_id = serializers.UUIDField()
    paid_through_account_id = serializers.UUIDField()
    vendor_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    end_date = serializers.DateField(required=False, allow_null=True, default=None)
    tax_rate = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, default=Decimal("0"))
    tax_recoverable_account_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    currency = serializers.PrimaryKeyRelatedField(queryset=Currency.objects.all(), required=False, default=None)
    exchange_rate = serializers.DecimalField(max_digits=18, decimal_places=8, required=False, default=Decimal("1"))
    description = serializers.CharField(required=False, allow_blank=True, default="")
    reference = serializers.CharField(required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    is_active = serializers.BooleanField(required=False, default=True)

    def create(self, validated_data):
        request = self.context["request"]
        return create_recurring_expense_template(
            organization=request.organization,
            expense_account=_get_or_404(Account, validated_data.pop("expense_account_id"), "Account"),
            paid_through_account=_get_or_404(Account, validated_data.pop("paid_through_account_id"), "Account"),
            tax_recoverable_account=_maybe(Account, validated_data.pop("tax_recoverable_account_id"), "Account"),
            vendor=_maybe(Vendor, validated_data.pop("vendor_id"), "Vendor"),
            actor=request.user,
            **validated_data,
        )


class RecurringExpenseTemplateUpdateSerializer(serializers.Serializer):
    frequency = serializers.ChoiceField(choices=RecurringFrequency.choices, required=False)
    end_date = serializers.DateField(required=False, allow_null=True)
    amount = serializers.DecimalField(max_digits=18, decimal_places=2, required=False)
    tax_rate = serializers.DecimalField(max_digits=5, decimal_places=2, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    reference = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    is_active = serializers.BooleanField(required=False)

    def save(self, **kwargs):
        request = self.context["request"]
        return update_recurring_expense_template(
            template=self.context["template"], actor=request.user, **self.validated_data
        )


# -------------------------------------------------- three-way matching


class ThreeWayMatchQuerySerializer(serializers.Serializer):
    """Tolerances are per-request and default to an exact match. There is no
    organization-level setting — see services/three_way_match.py."""

    quantity_tolerance = serializers.DecimalField(
        max_digits=18, decimal_places=4, required=False, default=Decimal("0"), min_value=Decimal("0")
    )
    price_tolerance = serializers.DecimalField(
        max_digits=18, decimal_places=2, required=False, default=Decimal("0"), min_value=Decimal("0")
    )
