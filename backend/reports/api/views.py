import datetime

from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView

from accounting.api.serializers import AccountSerializer, JournalEntrySerializer
from accounting.models.account import Account
from accounting.selectors import get_trial_balance
from authz.permissions import HasOrgPermission
from authz.roles import Permission
from core.exceptions import ApplicationError
from core.pagination import DefaultPagination
from core.views import OrganizationScopedMixin
from reports.exports.csv import rows_to_csv_response, wants_csv
from reports.selectors.balance_sheet import get_balance_sheet
from reports.selectors.cash_flow import get_cash_flow_statement
from reports.selectors.gl_journal import get_general_ledger_report, get_journal_report_queryset
from reports.selectors.inventory import (
    get_inventory_movement_queryset,
    get_inventory_valuation,
    get_low_stock_report,
    get_stock_adjustment_queryset,
    get_stock_summary,
)
from reports.selectors.params import money, parse_date, to_json_safe
from reports.selectors.payables import (
    get_ap_ageing,
    get_expenses_by_category,
    get_outstanding_bills_report,
    get_overdue_bills_report,
    get_purchases_by_item,
    get_purchases_by_vendor,
    get_vendor_balances,
)
from reports.selectors.pnl import get_profit_and_loss
from reports.selectors.projects import get_project_profitability_report
from reports.selectors.receivables import (
    get_ar_ageing,
    get_customer_balances,
    get_outstanding_invoices_report,
    get_overdue_invoices_report,
    get_sales_by_customer,
    get_sales_by_item,
)
from reports.selectors.tax import (
    get_gst_summary,
    get_gstr1_summary,
    get_gstr3b_summary,
    get_input_tax_register,
    get_output_tax_register,
)


def _pnl_totals_json(totals: dict) -> dict:
    return {key: money(value) for key, value in totals.items()}


def _pnl_sections_json(sections: dict) -> dict:
    return {
        section: [
            {
                "account_id": row["account_id"],
                "account_code": row["account_code"],
                "account_name": row["account_name"],
                "amount": money(row["amount"]),
            }
            for row in rows
        ]
        for section, rows in sections.items()
    }


class ProfitAndLossView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_REPORTS

    def get(self, request):
        from_date = parse_date(request.query_params.get("from_date"), param_name="from_date")
        to_date = parse_date(request.query_params.get("to_date"), param_name="to_date") or datetime.date.today()
        comparison_from = parse_date(request.query_params.get("comparison_from"), param_name="comparison_from")
        comparison_to = parse_date(request.query_params.get("comparison_to"), param_name="comparison_to")

        result = get_profit_and_loss(
            organization=request.organization,
            from_date=from_date,
            to_date=to_date,
            comparison_from=comparison_from,
            comparison_to=comparison_to,
        )

        payload = {
            "period": result["period"],
            "currency": request.organization.default_currency_id,
            "sections": _pnl_sections_json(result["sections"]),
            "totals": _pnl_totals_json(result["totals"]),
        }
        if "comparison_period" in result:
            payload["comparison_period"] = result["comparison_period"]
            payload["comparison_totals"] = _pnl_totals_json(result["comparison_totals"])
            payload["variance"] = result["variance"]
        return Response(payload)


def _balance_sheet_sections_json(sections: dict) -> dict:
    return {
        section: [
            {
                "account_id": row["account_id"],
                "account_code": row["account_code"],
                "account_name": row["account_name"],
                "amount": money(row["amount"]),
            }
            for row in rows
        ]
        for section, rows in sections.items()
    }


class BalanceSheetView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_REPORTS

    def get(self, request):
        as_of_date = parse_date(request.query_params.get("as_of_date"), param_name="as_of_date") or datetime.date.today()

        result = get_balance_sheet(organization=request.organization, as_of_date=as_of_date)

        return Response(
            {
                "as_of_date": result["as_of_date"],
                "currency": request.organization.default_currency_id,
                "assets": _balance_sheet_sections_json(result["assets"]),
                "liabilities": _balance_sheet_sections_json(result["liabilities"]),
                "equity": [
                    {
                        "account_id": row["account_id"],
                        "account_code": row["account_code"],
                        "account_name": row["account_name"],
                        "amount": money(row["amount"]),
                    }
                    for row in result["equity"]
                ],
                "totals": {key: money(value) for key, value in result["totals"].items()},
                "is_balanced": result["is_balanced"],
            }
        )


def _cash_flow_rows_json(rows):
    return [
        {
            "account_id": row["account_id"],
            "account_code": row["account_code"],
            "account_name": row["account_name"],
            "amount": money(row["amount"]),
        }
        for row in rows
    ]


class CashFlowView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_REPORTS

    def get(self, request):
        from_date = parse_date(request.query_params.get("from_date"), param_name="from_date")
        if from_date is None:
            raise ApplicationError("from_date is required.", code="from_date_required")
        to_date = parse_date(request.query_params.get("to_date"), param_name="to_date") or datetime.date.today()

        result = get_cash_flow_statement(organization=request.organization, from_date=from_date, to_date=to_date)

        return Response(
            {
                "period": result["period"],
                "currency": request.organization.default_currency_id,
                "opening_cash": money(result["opening_cash"]),
                "operating_activities": {
                    "net_profit": money(result["operating_activities"]["net_profit"]),
                    "adjustments": _cash_flow_rows_json(result["operating_activities"]["adjustments"]),
                    "total": money(result["operating_activities"]["total"]),
                },
                "investing_activities": {
                    "items": _cash_flow_rows_json(result["investing_activities"]["items"]),
                    "total": money(result["investing_activities"]["total"]),
                },
                "financing_activities": {
                    "items": _cash_flow_rows_json(result["financing_activities"]["items"]),
                    "total": money(result["financing_activities"]["total"]),
                },
                "net_change_in_cash": money(result["net_change_in_cash"]),
                "closing_cash": money(result["closing_cash"]),
                "cash_accounts": result["cash_accounts"],
                "reconciles": result["reconciles"],
            }
        )


class TrialBalanceView(OrganizationScopedMixin, APIView):
    """Thin reporting-namespace wrapper — reuses `accounting.selectors.
    get_trial_balance` verbatim (PHASE 8 spec §6: do not duplicate). The
    authoritative Trial Balance endpoint remains
    `/api/v1/accounting/reports/trial-balance/`; this one exists only to
    give the reports API surface the URL the phase spec lists (§21)."""

    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_REPORTS

    def get(self, request):
        as_of_date = parse_date(request.query_params.get("as_of_date"), param_name="as_of_date") or datetime.date.today()
        from_date = parse_date(request.query_params.get("from_date"), param_name="from_date")

        result = get_trial_balance(organization=request.organization, as_of_date=as_of_date, from_date=from_date)

        return Response(
            {
                "as_of_date": as_of_date,
                "from_date": from_date,
                "is_balanced": result["is_balanced"],
                "total_period_debit": money(result["total_period_debit"]),
                "total_period_credit": money(result["total_period_credit"]),
                "total_closing_debit": money(result["total_closing_debit"]),
                "total_closing_credit": money(result["total_closing_credit"]),
                "rows": [
                    {
                        "account": AccountSerializer(row["account"]).data,
                        "opening_balance": money(row["opening_balance"]),
                        "period_debit": money(row["period_debit"]),
                        "period_credit": money(row["period_credit"]),
                        "closing_debit": money(row["closing_debit"]),
                        "closing_credit": money(row["closing_credit"]),
                    }
                    for row in result["rows"]
                ],
            }
        )


class GeneralLedgerReportView(OrganizationScopedMixin, APIView):
    """Paginated wrapper over `accounting.selectors.get_account_running_ledger`
    (PHASE 8 spec §7). The running balance is computed once over the full,
    deterministically-ordered entry list and only the requested page is
    sliced out of it, so pagination never changes a balance's value."""

    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_TRANSACTIONS
    pagination_class = DefaultPagination

    def get(self, request):
        account_id = request.query_params.get("account")
        if not account_id:
            raise ApplicationError("account is required.", code="account_required")
        try:
            account = Account.objects.get(pk=account_id)
        except (Account.DoesNotExist, ValueError, TypeError):
            raise ApplicationError("Account not found.", code="account_not_found", status_code=404)

        from_date = parse_date(request.query_params.get("from_date"), param_name="from_date")
        to_date = parse_date(request.query_params.get("to_date"), param_name="to_date")

        ledger = get_general_ledger_report(account=account, from_date=from_date, to_date=to_date)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(ledger["entries"], request, view=self)
        entries_json = [
            {
                "journal_entry_id": entry["journal_entry"].id,
                "journal_number": entry["journal_entry"].journal_number,
                "posting_date": entry["journal_entry"].posting_date,
                "source_type": entry["journal_entry"].source_type,
                "source_id": entry["journal_entry"].source_id,
                "description": entry["line"].description,
                "debit": money(entry["debit"]),
                "credit": money(entry["credit"]),
                "running_balance": money(entry["running_balance"]),
            }
            for entry in page
        ]
        response = paginator.get_paginated_response(entries_json)
        response.data["account"] = AccountSerializer(account).data
        response.data["opening_balance"] = money(ledger["opening_balance"])
        response.data["closing_balance"] = money(ledger["closing_balance"])
        return response


class JournalReportView(OrganizationScopedMixin, generics.ListAPIView):
    """Journal report + detail drill-down list (PHASE 8 spec §8). Defaults
    to POSTED entries only; filters: date range, journal_number, account,
    source_type, status."""

    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_TRANSACTIONS
    serializer_class = JournalEntrySerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        params = self.request.query_params
        account = None
        account_id = params.get("account")
        if account_id:
            try:
                account = Account.objects.get(pk=account_id)
            except (Account.DoesNotExist, ValueError, TypeError):
                raise ApplicationError("Account not found.", code="account_not_found", status_code=404)

        return get_journal_report_queryset(
            organization=self.request.organization,
            from_date=parse_date(params.get("from_date"), param_name="from_date"),
            to_date=parse_date(params.get("to_date"), param_name="to_date"),
            journal_number=params.get("journal_number"),
            account=account,
            source_type=params.get("source_type"),
            status=params.get("status"),
        )


_INVOICE_ROW_FIELDS = (
    "invoice_id", "invoice_number", "customer_id", "customer_name", "invoice_date", "due_date", "total",
    "amount_due", "status",
)
_BILL_ROW_FIELDS = (
    "bill_id", "bill_number", "vendor_id", "vendor_name", "bill_date", "due_date", "total", "amount_due", "status",
)


def _rows_response(request, rows, *, fieldnames, filename, extra_json=None):
    """Shared JSON/CSV responder for flat row-list reports (PHASE 8 spec
    §24: export uses the exact same filters/authorization as the displayed
    report — this is the same `rows` list either way, only the rendering
    differs)."""
    if wants_csv(request):
        return rows_to_csv_response(rows=to_json_safe(rows), fieldnames=fieldnames, filename=filename)
    payload = {"rows": to_json_safe(rows)}
    if extra_json:
        payload.update(to_json_safe(extra_json))
    return Response(payload)


# --- Receivables / Sales ----------------------------------------------------


class ARAgeingView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_INVOICES

    def get(self, request):
        as_of = parse_date(request.query_params.get("as_of_date"), param_name="as_of_date") or datetime.date.today()
        return Response(to_json_safe(get_ar_ageing(organization=request.organization, as_of=as_of)))


class CustomerBalancesView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_CUSTOMERS

    def get(self, request):
        rows = get_customer_balances(organization=request.organization)
        return _rows_response(
            request, rows, fieldnames=["customer_id", "customer_name", "balance"], filename="customer_balances.csv"
        )


class OutstandingInvoicesView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_INVOICES

    def get(self, request):
        rows = get_outstanding_invoices_report(organization=request.organization)
        return _rows_response(request, rows, fieldnames=list(_INVOICE_ROW_FIELDS), filename="outstanding_invoices.csv")


class OverdueInvoicesView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_INVOICES

    def get(self, request):
        as_of = parse_date(request.query_params.get("as_of_date"), param_name="as_of_date")
        rows = get_overdue_invoices_report(organization=request.organization, as_of=as_of)
        return _rows_response(request, rows, fieldnames=list(_INVOICE_ROW_FIELDS), filename="overdue_invoices.csv")


class SalesByCustomerView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_INVOICES

    def get(self, request):
        from_date = parse_date(request.query_params.get("from_date"), param_name="from_date")
        to_date = parse_date(request.query_params.get("to_date"), param_name="to_date")
        rows = get_sales_by_customer(organization=request.organization, from_date=from_date, to_date=to_date)
        return _rows_response(
            request, rows,
            fieldnames=["customer_id", "customer_name", "invoice_count", "taxable_value", "tax_total", "total_sales"],
            filename="sales_by_customer.csv",
            extra_json={"period": {"from_date": from_date, "to_date": to_date}},
        )


class SalesByItemView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_INVOICES

    def get(self, request):
        from_date = parse_date(request.query_params.get("from_date"), param_name="from_date")
        to_date = parse_date(request.query_params.get("to_date"), param_name="to_date")
        rows = get_sales_by_item(organization=request.organization, from_date=from_date, to_date=to_date)
        return _rows_response(
            request, rows,
            fieldnames=["item_id", "item_name", "item_sku", "quantity", "taxable_value", "tax_total", "total_sales"],
            filename="sales_by_item.csv",
            extra_json={"period": {"from_date": from_date, "to_date": to_date}},
        )


# --- Payables / Purchases ----------------------------------------------------


class APAgeingView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_BILLS

    def get(self, request):
        as_of = parse_date(request.query_params.get("as_of_date"), param_name="as_of_date") or datetime.date.today()
        return Response(to_json_safe(get_ap_ageing(organization=request.organization, as_of=as_of)))


class VendorBalancesView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_VENDORS

    def get(self, request):
        rows = get_vendor_balances(organization=request.organization)
        return _rows_response(
            request, rows, fieldnames=["vendor_id", "vendor_name", "balance"], filename="vendor_balances.csv"
        )


class OutstandingBillsView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_BILLS

    def get(self, request):
        rows = get_outstanding_bills_report(organization=request.organization)
        return _rows_response(request, rows, fieldnames=list(_BILL_ROW_FIELDS), filename="outstanding_bills.csv")


class OverdueBillsView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_BILLS

    def get(self, request):
        as_of = parse_date(request.query_params.get("as_of_date"), param_name="as_of_date")
        rows = get_overdue_bills_report(organization=request.organization, as_of=as_of)
        return _rows_response(request, rows, fieldnames=list(_BILL_ROW_FIELDS), filename="overdue_bills.csv")


class PurchasesByVendorView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_BILLS

    def get(self, request):
        from_date = parse_date(request.query_params.get("from_date"), param_name="from_date")
        to_date = parse_date(request.query_params.get("to_date"), param_name="to_date")
        rows = get_purchases_by_vendor(organization=request.organization, from_date=from_date, to_date=to_date)
        return _rows_response(
            request, rows,
            fieldnames=["vendor_id", "vendor_name", "bill_count", "taxable_value", "tax_total", "total_purchases"],
            filename="purchases_by_vendor.csv",
            extra_json={"period": {"from_date": from_date, "to_date": to_date}},
        )


class PurchasesByItemView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_BILLS

    def get(self, request):
        from_date = parse_date(request.query_params.get("from_date"), param_name="from_date")
        to_date = parse_date(request.query_params.get("to_date"), param_name="to_date")
        rows = get_purchases_by_item(organization=request.organization, from_date=from_date, to_date=to_date)
        return _rows_response(
            request, rows,
            fieldnames=["item_id", "item_name", "item_sku", "quantity", "taxable_value", "tax_total", "total_purchases"],
            filename="purchases_by_item.csv",
            extra_json={"period": {"from_date": from_date, "to_date": to_date}},
        )


class ExpensesByCategoryView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_EXPENSES

    def get(self, request):
        from_date = parse_date(request.query_params.get("from_date"), param_name="from_date")
        to_date = parse_date(request.query_params.get("to_date"), param_name="to_date")
        rows = get_expenses_by_category(organization=request.organization, from_date=from_date, to_date=to_date)
        return _rows_response(
            request, rows,
            fieldnames=["account_id", "account_code", "account_name", "expense_count", "tax_total", "total_amount"],
            filename="expenses_by_category.csv",
            extra_json={"period": {"from_date": from_date, "to_date": to_date}},
        )


# --- Inventory ----------------------------------------------------------


def _resolve_warehouse(request):
    warehouse_id = request.query_params.get("warehouse")
    if not warehouse_id:
        return None
    from inventory.models.warehouse import Warehouse

    try:
        return Warehouse.objects.get(pk=warehouse_id)
    except (Warehouse.DoesNotExist, ValueError, TypeError):
        raise ApplicationError("Warehouse not found.", code="warehouse_not_found", status_code=404)


def _resolve_item(request):
    item_id = request.query_params.get("item")
    if not item_id:
        return None
    from items.models.item import Item

    try:
        return Item.objects.get(pk=item_id)
    except (Item.DoesNotExist, ValueError, TypeError):
        raise ApplicationError("Item not found.", code="item_not_found", status_code=404)


class StockSummaryView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_INVENTORY

    def get(self, request):
        warehouse = _resolve_warehouse(request)
        item = _resolve_item(request)
        as_of = parse_date(request.query_params.get("as_of_date"), param_name="as_of_date")
        rows = get_stock_summary(organization=request.organization, warehouse=warehouse, item=item, as_of=as_of)
        return _rows_response(
            request, rows,
            fieldnames=[
                "item_id", "item_name", "item_sku", "warehouse_id", "warehouse_name",
                "quantity_on_hand", "average_cost", "inventory_value",
            ],
            filename="stock_summary.csv",
            extra_json={"as_of": as_of},
        )


class InventoryValuationView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_INVENTORY

    def get(self, request):
        warehouse = _resolve_warehouse(request)
        as_of = parse_date(request.query_params.get("as_of_date"), param_name="as_of_date")
        result = get_inventory_valuation(organization=request.organization, warehouse=warehouse, as_of=as_of)
        return Response(to_json_safe(result))


class InventoryMovementView(OrganizationScopedMixin, generics.ListAPIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_INVENTORY
    pagination_class = DefaultPagination

    def get_serializer_class(self):
        from inventory.api.serializers import StockMovementSerializer

        return StockMovementSerializer

    def get_queryset(self):
        params = self.request.query_params
        return get_inventory_movement_queryset(
            organization=self.request.organization,
            item=_resolve_item(self.request),
            warehouse=_resolve_warehouse(self.request),
            from_date=parse_date(params.get("from_date"), param_name="from_date"),
            to_date=parse_date(params.get("to_date"), param_name="to_date"),
        )


class StockAdjustmentReportView(OrganizationScopedMixin, generics.ListAPIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_INVENTORY
    pagination_class = DefaultPagination

    def get_serializer_class(self):
        from inventory.api.serializers import StockAdjustmentSerializer

        return StockAdjustmentSerializer

    def get_queryset(self):
        params = self.request.query_params
        return get_stock_adjustment_queryset(
            organization=self.request.organization,
            from_date=parse_date(params.get("from_date"), param_name="from_date"),
            to_date=parse_date(params.get("to_date"), param_name="to_date"),
            status=params.get("status"),
            warehouse=_resolve_warehouse(self.request),
        )


class LowStockReportView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_INVENTORY

    def get(self, request):
        warehouse = _resolve_warehouse(request)
        rows = get_low_stock_report(organization=request.organization, warehouse=warehouse)
        return _rows_response(
            request, rows,
            fieldnames=["item_id", "item_name", "item_sku", "on_hand", "reorder_level"],
            filename="low_stock.csv",
        )


# --- GST / Tax ------------------------------------------------------------


def _tax_period(request):
    date_from = parse_date(request.query_params.get("from_date"), param_name="from_date")
    date_to = parse_date(request.query_params.get("to_date"), param_name="to_date")
    if date_from is None or date_to is None:
        raise ApplicationError("from_date and to_date are both required.", code="date_range_required")
    return date_from, date_to


class OutputTaxRegisterView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_RETURNS

    def get(self, request):
        date_from, date_to = _tax_period(request)
        rows = get_output_tax_register(organization=request.organization, date_from=date_from, date_to=date_to)
        return Response(to_json_safe({"period": {"from_date": date_from, "to_date": date_to}, "rows": rows}))


class InputTaxRegisterView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_RETURNS

    def get(self, request):
        date_from, date_to = _tax_period(request)
        rows = get_input_tax_register(organization=request.organization, date_from=date_from, date_to=date_to)
        return Response(to_json_safe({"period": {"from_date": date_from, "to_date": date_to}, "rows": rows}))


class GSTR1SummaryView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_RETURNS

    def get(self, request):
        date_from, date_to = _tax_period(request)
        result = get_gstr1_summary(organization=request.organization, date_from=date_from, date_to=date_to)
        return Response(to_json_safe(result))


class GSTR3BSummaryView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_RETURNS

    def get(self, request):
        date_from, date_to = _tax_period(request)
        result = get_gstr3b_summary(organization=request.organization, date_from=date_from, date_to=date_to)
        return Response(to_json_safe(result))


class GSTSummaryView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_RETURNS

    def get(self, request):
        date_from, date_to = _tax_period(request)
        result = get_gst_summary(organization=request.organization, date_from=date_from, date_to=date_to)
        return Response(to_json_safe(result))


# --- Projects ---------------------------------------------------------------


class ProjectProfitabilityReportView(OrganizationScopedMixin, APIView):
    """Gated on VIEW_ALL_TIMESHEETS, not VIEW_PROJECTS — margin exposes
    labour cost (cost_rate), the same boundary projects/CLAUDE.md draws for
    the single-project profitability view (api/views.py there)."""

    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_ALL_TIMESHEETS

    def get(self, request):
        status_filter = request.query_params.get("status")
        rows = get_project_profitability_report(organization=request.organization, status=status_filter)
        return _rows_response(
            request, rows,
            fieldnames=[
                "project_id", "project_code", "name", "status", "billing_method", "revenue", "unbilled_value",
                "labour_cost", "expense_cost", "total_cost", "margin", "margin_percent", "total_hours",
                "billable_hours", "non_billable_hours",
            ],
            filename="project_profitability.csv",
        )
