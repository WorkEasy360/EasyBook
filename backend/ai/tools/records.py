"""Single-record lookups (invoice, bill).

Records are resolved ONLY through the tenant-scoped `.objects` manager under
the caller's tenant context (and RLS). An id or number belonging to another
organization simply does not resolve: the tool answers `not_found` — the
same semantics the REST API gives — never "forbidden", which would confirm
the record exists elsewhere.
"""

from django.urls import reverse
from rest_framework import serializers

from ai.sources import Source
from ai.tools.base import Tool, ToolContext, ToolError, ToolResult
from ai.tools.registry import register
from ai.tools.schemas import StrictSerializer
from authz.roles import Permission


class _LookupArgs(StrictSerializer):
    number_field = ""
    id_field = ""

    def validate(self, attrs):
        provided = [key for key in (self.number_field, self.id_field) if attrs.get(key)]
        if len(provided) != 1:
            raise serializers.ValidationError(
                {"non_field_errors": [f"Provide exactly one of {self.number_field} or {self.id_field}."]}
            )
        return attrs


class InvoiceLookupArgs(_LookupArgs):
    number_field, id_field = "invoice_number", "invoice_id"
    invoice_number = serializers.CharField(required=False, max_length=32, help_text="Invoice number, e.g. INV-1024.")
    invoice_id = serializers.UUIDField(required=False)


class BillLookupArgs(_LookupArgs):
    number_field, id_field = "bill_number", "bill_id"
    bill_number = serializers.CharField(
        required=False, max_length=64, help_text="Bill number or the vendor's own bill reference."
    )
    bill_id = serializers.UUIDField(required=False)


def _get_invoice(ctx: ToolContext, args: dict) -> ToolResult:
    from sales.models.invoice import Invoice
    from sales.selectors import get_invoice_amount_due

    queryset = Invoice.objects.select_related("customer")
    invoice = (
        queryset.filter(pk=args["invoice_id"]).first()
        if args.get("invoice_id")
        else queryset.filter(invoice_number__iexact=args["invoice_number"].strip()).first()
    )
    if invoice is None:
        raise ToolError("not_found", "No invoice with that reference was found.")
    return ToolResult(
        tool="get_invoice", status="ok",
        summary={
            "invoice_number": invoice.invoice_number or "(draft)",
            "status": invoice.status,
            "customer_name": invoice.customer.display_name,
            "invoice_date": invoice.invoice_date,
            "due_date": invoice.due_date,
            "currency": invoice.currency_id,
            "subtotal": invoice.subtotal,
            "tax_total": invoice.tax_total,
            "total": invoice.total,
            "amount_due": get_invoice_amount_due(invoice=invoice),
        },
        sources=[Source(
            source_id=f"invoice:{invoice.pk}", type="invoice", id=str(invoice.pk),
            label=f"Invoice {invoice.invoice_number or '(draft)'}",
            route=reverse("sales-invoice-detail", kwargs={"pk": invoice.pk}),
        )],
    )


def _get_bill(ctx: ToolContext, args: dict) -> ToolResult:
    from django.db.models import Q

    from purchases.models.bill import Bill
    from purchases.selectors import get_bill_amount_due

    queryset = Bill.objects.select_related("vendor")
    if args.get("bill_id"):
        bill = queryset.filter(pk=args["bill_id"]).first()
    else:
        number = args["bill_number"].strip()
        bill = queryset.filter(Q(bill_number__iexact=number) | Q(vendor_bill_number__iexact=number)).order_by("-bill_date").first()
    if bill is None:
        raise ToolError("not_found", "No bill with that reference was found.")
    return ToolResult(
        tool="get_bill", status="ok",
        summary={
            "bill_number": bill.bill_number or "(draft)",
            "vendor_bill_number": bill.vendor_bill_number,
            "status": bill.status,
            "vendor_name": bill.vendor.display_name,
            "bill_date": bill.bill_date,
            "due_date": bill.due_date,
            "currency": bill.currency_id,
            "total": bill.total,
            "amount_due": get_bill_amount_due(bill=bill),
        },
        sources=[Source(
            source_id=f"bill:{bill.pk}", type="bill", id=str(bill.pk),
            label=f"Bill {bill.bill_number or bill.vendor_bill_number or '(draft)'}",
            route=reverse("purchases-bill-detail", kwargs={"pk": bill.pk}),
        )],
    )


register(Tool(
    name="get_invoice",
    description="Look up one customer invoice by invoice number or id: status, dates, totals and amount due.",
    required_permissions=(Permission.VIEW_INVOICES,),
    input_serializer=InvoiceLookupArgs,
    handler=_get_invoice,
))
register(Tool(
    name="get_bill",
    description="Look up one vendor bill by bill number (or the vendor's bill reference) or id.",
    required_permissions=(Permission.VIEW_BILLS,),
    input_serializer=BillLookupArgs,
    handler=_get_bill,
))
