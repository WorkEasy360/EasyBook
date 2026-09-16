"""Structured financial/report tools.

RULE (phase section 20): every figure comes from an EXISTING domain/report
selector, passed through unchanged. This module only validates arguments,
selects/sorts/limits rows for context size, and attaches genuine sources. It
contains no accounting, tax, ageing or valuation arithmetic — if a figure is
not returned by a selector, the tool does not return it (e.g. there is no
"net GST payable" figure in `compliance`, so get_gst_summary does not
invent one).
"""

from urllib.parse import urlencode

from django.urls import reverse
from rest_framework import serializers

from ai.sources import report_source
from ai.tools.base import Tool, ToolContext, ToolError, ToolResult
from ai.tools.registry import register
from ai.tools.schemas import AsOfArgs, LimitMixin, PeriodArgs, StrictSerializer, validate_period
from authz.roles import Permission
from reports.selectors.balance_sheet import get_balance_sheet
from reports.selectors.cash_flow import get_cash_flow_statement
from reports.selectors.inventory import get_inventory_valuation, get_low_stock_report, get_stock_summary
from reports.selectors.payables import (
    get_ap_ageing,
    get_expenses_by_category,
    get_outstanding_bills_report,
    get_purchases_by_vendor,
    get_vendor_balances,
)
from reports.selectors.pnl import get_profit_and_loss
from reports.selectors.projects import get_project_profitability_report
from reports.selectors.receivables import (
    AGEING_BUCKETS,
    get_ar_ageing,
    get_customer_balances,
    get_sales_by_customer,
    get_sales_by_item,
)
from reports.selectors.tax import get_gst_summary


def _route(name: str, **params) -> str:
    query = urlencode({key: value for key, value in params.items() if value is not None})
    return reverse(name) + (f"?{query}" if query else "")


def _limit(args: dict, ctx_default: int) -> int:
    from ai.config import get_ai_config

    cap = get_ai_config().max_tool_rows
    return min(args.get("limit") or ctx_default, cap)


def _top(rows: list[dict], key: str, limit: int) -> list[dict]:
    """Presentation ordering only (largest first) — never arithmetic."""
    return sorted(rows, key=lambda row: row[key], reverse=True)[:limit]


def _ids_to_str(rows: list[dict]) -> list[dict]:
    return [{key: (str(value) if key.endswith("_id") and value is not None else value) for key, value in row.items()} for row in rows]


# --- Profit & Loss ---------------------------------------------------------

class ProfitAndLossArgs(PeriodArgs):
    comparison_from = serializers.DateField(required=False, help_text="Optional comparison period start.")
    comparison_to = serializers.DateField(required=False, help_text="Optional comparison period end.")

    def validate(self, attrs):
        attrs = validate_period(attrs)
        if ("comparison_from" in attrs) != ("comparison_to" in attrs):
            raise serializers.ValidationError({"comparison_from": ["Provide both comparison dates or neither."]})
        return validate_period(attrs, "comparison_from", "comparison_to", required=False)


def _pnl(ctx: ToolContext, args: dict) -> ToolResult:
    result = get_profit_and_loss(
        organization=ctx.organization, from_date=args["from_date"], to_date=args["to_date"],
        comparison_from=args.get("comparison_from"), comparison_to=args.get("comparison_to"),
    )
    summary = {"from_date": args["from_date"], "to_date": args["to_date"], **result["totals"]}
    data = {
        f"{section}_top_accounts": _ids_to_str(_top(rows, "amount", 10))
        for section, rows in result["sections"].items()
    }
    sources = [
        report_source(
            report="profit_and_loss", key=f"{args['from_date']}:{args['to_date']}",
            label=f"Profit & Loss {args['from_date']} to {args['to_date']}",
            route=_route("reports-profit-loss", from_date=args["from_date"], to_date=args["to_date"]),
        )
    ]
    if "comparison_totals" in result:
        data["comparison_period"] = result["comparison_period"]
        data["comparison_totals"] = result["comparison_totals"]
        data["variance"] = result["variance"]
        summary["comparison_net_profit"] = result["comparison_totals"]["net_profit"]
        summary["comparison_revenue"] = result["comparison_totals"]["revenue"]
        summary["net_profit_variance_percent"] = result["variance"]["net_profit"]["variance_percent"]
        summary["revenue_variance_percent"] = result["variance"]["revenue"]["variance_percent"]
    return ToolResult(tool="get_profit_and_loss", status="ok", summary=summary, data=data, sources=sources)


register(Tool(
    name="get_profit_and_loss",
    description="Authoritative Profit & Loss (revenue, COGS, gross/operating/net profit) for a period, "
                "optionally compared with another period. Use for any profit, revenue or expense-total question.",
    required_permissions=(Permission.VIEW_REPORTS,),
    input_serializer=ProfitAndLossArgs,
    handler=_pnl,
))


# --- Balance Sheet ---------------------------------------------------------

def _balance_sheet(ctx: ToolContext, args: dict) -> ToolResult:
    as_of = args.get("as_of_date") or ctx.today
    result = get_balance_sheet(organization=ctx.organization, as_of_date=as_of)
    data = {}
    for group in ("assets", "liabilities"):
        for section, rows in result[group].items():
            data[f"{section}"] = _ids_to_str(_top(rows, "amount", 10))
    data["equity"] = _ids_to_str(_top(result["equity"], "amount", 10))
    return ToolResult(
        tool="get_balance_sheet", status="ok",
        summary={"as_of_date": as_of, **result["totals"], "is_balanced": result["is_balanced"]},
        data=data,
        sources=[report_source(
            report="balance_sheet", key=str(as_of), label=f"Balance Sheet as of {as_of}",
            route=_route("reports-balance-sheet", as_of_date=as_of),
        )],
    )


register(Tool(
    name="get_balance_sheet",
    description="Authoritative Balance Sheet (assets, liabilities, equity) as of a date.",
    required_permissions=(Permission.VIEW_REPORTS,),
    input_serializer=AsOfArgs,
    handler=_balance_sheet,
))


# --- Cash Flow -------------------------------------------------------------

def _cash_flow(ctx: ToolContext, args: dict) -> ToolResult:
    result = get_cash_flow_statement(organization=ctx.organization, from_date=args["from_date"], to_date=args["to_date"])
    return ToolResult(
        tool="get_cash_flow", status="ok",
        summary={
            "from_date": args["from_date"], "to_date": args["to_date"],
            "opening_cash": result["opening_cash"], "closing_cash": result["closing_cash"],
            "net_change_in_cash": result["net_change_in_cash"],
            "operating_activities_total": result["operating_activities"]["total"],
            "investing_activities_total": result["investing_activities"]["total"],
            "financing_activities_total": result["financing_activities"]["total"],
        },
        data={"cash_accounts": [{"name": row["name"]} for row in result["cash_accounts"]]},
        sources=[report_source(
            report="cash_flow", key=f"{args['from_date']}:{args['to_date']}",
            label=f"Cash Flow Statement {args['from_date']} to {args['to_date']}",
            route=_route("reports-cash-flow", from_date=args["from_date"], to_date=args["to_date"]),
        )],
    )


register(Tool(
    name="get_cash_flow",
    description="Authoritative Cash Flow Statement for a period, including opening and closing cash (the cash position).",
    required_permissions=(Permission.VIEW_REPORTS,),
    input_serializer=PeriodArgs,
    handler=_cash_flow,
))


# --- Trial Balance ---------------------------------------------------------

def _trial_balance(ctx: ToolContext, args: dict) -> ToolResult:
    from accounting.selectors import get_trial_balance

    as_of = args.get("as_of_date") or ctx.today
    result = get_trial_balance(organization=ctx.organization, as_of_date=as_of)
    rows = [
        {
            "account_code": row["account"].code, "account_name": row["account"].name,
            "closing_debit": row["closing_debit"], "closing_credit": row["closing_credit"],
        }
        for row in result["rows"]
        if row["closing_debit"] or row["closing_credit"]
    ]
    return ToolResult(
        tool="get_trial_balance", status="ok",
        summary={
            "as_of_date": as_of, "total_closing_debit": result["total_closing_debit"],
            "total_closing_credit": result["total_closing_credit"], "is_balanced": result["is_balanced"],
        },
        data={"accounts": rows},
        sources=[report_source(
            report="trial_balance", key=str(as_of), label=f"Trial Balance as of {as_of}",
            route=_route("reports-trial-balance", as_of_date=as_of),
        )],
    )


register(Tool(
    name="get_trial_balance",
    description="Authoritative Trial Balance (closing debit/credit per account) as of a date.",
    required_permissions=(Permission.VIEW_REPORTS,),
    input_serializer=AsOfArgs,
    handler=_trial_balance,
))


# --- AR / AP ageing --------------------------------------------------------

def _ageing_result(tool, report, route_name, label, result, number_key, party_key, limit):
    rows = []
    for bucket in AGEING_BUCKETS:
        rows.extend({**row, "bucket": bucket} for row in result["buckets"][bucket])
    top = _top(rows, "amount_due", limit)
    return ToolResult(
        tool=tool, status="ok",
        summary={"as_of": result["as_of"], "grand_total": result["grand_total"], **{f"bucket_{k}": v for k, v in result["totals"].items()}, "document_count": len(rows)},
        data={"largest_outstanding": [
            {
                number_key: row[number_key], party_key: row[party_key], "due_date": row["due_date"],
                "amount_due": row["amount_due"], "days_overdue": row["days_overdue"], "bucket": row["bucket"],
            }
            for row in top
        ]},
        sources=[report_source(report=report, key=str(result["as_of"]), label=f"{label} as of {result['as_of']}",
                               route=_route(route_name, as_of_date=result["as_of"]))],
    )


class AgeingArgs(AsOfArgs, LimitMixin):
    pass


def _ar_ageing(ctx, args):
    result = get_ar_ageing(organization=ctx.organization, as_of=args.get("as_of_date") or ctx.today)
    return _ageing_result("get_ar_ageing", "ar_ageing", "reports-ar-ageing", "Accounts Receivable Ageing",
                          result, "invoice_number", "customer_name", _limit(args, 10))


def _ap_ageing(ctx, args):
    result = get_ap_ageing(organization=ctx.organization, as_of=args.get("as_of_date") or ctx.today)
    return _ageing_result("get_ap_ageing", "ap_ageing", "reports-ap-ageing", "Accounts Payable Ageing",
                          result, "bill_number", "vendor_name", _limit(args, 10))


register(Tool(
    name="get_ar_ageing",
    description="Accounts Receivable ageing: total owed BY customers, split into current/1-30/31-60/61-90/90+ day buckets.",
    required_permissions=(Permission.VIEW_INVOICES,),
    input_serializer=AgeingArgs,
    handler=_ar_ageing,
))
register(Tool(
    name="get_ap_ageing",
    description="Accounts Payable ageing: total the organization owes vendors, split into ageing buckets.",
    required_permissions=(Permission.VIEW_BILLS,),
    input_serializer=AgeingArgs,
    handler=_ap_ageing,
))


class OverdueArgs(AsOfArgs, LimitMixin):
    min_days_overdue = serializers.IntegerField(
        required=False, min_value=0, max_value=3650, help_text="Only invoices at least this many days past due."
    )


def _overdue_invoices(ctx, args):
    as_of = args.get("as_of_date") or ctx.today
    minimum = max(args.get("min_days_overdue") or 0, 1)
    result = get_ar_ageing(organization=ctx.organization, as_of=as_of)
    rows = [row for bucket in AGEING_BUCKETS for row in result["buckets"][bucket] if row["days_overdue"] >= minimum]
    rows.sort(key=lambda row: (-row["days_overdue"], row["invoice_number"]))
    limit = _limit(args, 25)
    return ToolResult(
        tool="get_overdue_invoices", status="ok",
        # A count of matching invoices, not a money total: no selector sums
        # an arbitrary "N+ days" subset, so none is computed here.
        summary={"as_of": as_of, "min_days_overdue": minimum, "matching_invoice_count": len(rows),
                 **{f"ageing_bucket_{k}": v for k, v in result["totals"].items()}},
        data={"invoices": [
            {"invoice_number": row["invoice_number"], "customer_name": row["customer_name"], "due_date": row["due_date"],
             "days_overdue": row["days_overdue"], "amount_due": row["amount_due"], "invoice_id": str(row["invoice_id"])}
            for row in rows[:limit]
        ]},
        truncated=len(rows) > limit,
        sources=[report_source(report="ar_ageing", key=str(as_of), label=f"Accounts Receivable Ageing as of {as_of}",
                               route=_route("reports-ar-ageing", as_of_date=as_of))],
    )


register(Tool(
    name="get_overdue_invoices",
    description="Customer invoices past their due date, optionally only those overdue by at least N days.",
    required_permissions=(Permission.VIEW_INVOICES,),
    input_serializer=OverdueArgs,
    handler=_overdue_invoices,
))


# --- Balances by party -----------------------------------------------------

class PartyBalanceArgs(StrictSerializer, LimitMixin):
    order = serializers.ChoiceField(
        choices=["balance_desc", "name"], required=False, help_text="balance_desc = largest balance first."
    )


def _party_balances(tool, rows, id_key, name_key, report, route_name, label, args):
    if args.get("order", "balance_desc") == "balance_desc":
        rows = sorted(rows, key=lambda row: (-row["balance"], row[name_key]))
    limit = _limit(args, 10)
    return ToolResult(
        tool=tool, status="ok",
        summary={"party_count_with_balance": len(rows),
                 "top": [{name_key: row[name_key], "balance": row["balance"]} for row in rows[:min(limit, 5)]]},
        data={"balances": [{name_key: row[name_key], "balance": row["balance"], id_key: str(row[id_key])} for row in rows[:limit]]},
        truncated=len(rows) > limit,
        sources=[report_source(report=report, key="current", label=label, route=_route(route_name))],
    )


def _customer_balances(ctx, args):
    return _party_balances("get_customer_balances", get_customer_balances(organization=ctx.organization),
                           "customer_id", "customer_name", "customer_balances", "reports-customer-balances",
                           "Customer Balances", args)


def _vendor_balances(ctx, args):
    return _party_balances("get_vendor_balances", get_vendor_balances(organization=ctx.organization),
                           "vendor_id", "vendor_name", "vendor_balances", "reports-vendor-balances",
                           "Vendor Balances", args)


register(Tool(
    name="get_customer_balances",
    description="Outstanding balance owed by each customer (e.g. which customers owe the most).",
    required_permissions=(Permission.VIEW_CUSTOMERS, Permission.VIEW_INVOICES),
    input_serializer=PartyBalanceArgs,
    handler=_customer_balances,
))
register(Tool(
    name="get_vendor_balances",
    description="Outstanding balance the organization owes each vendor.",
    required_permissions=(Permission.VIEW_VENDORS, Permission.VIEW_BILLS),
    input_serializer=PartyBalanceArgs,
    handler=_vendor_balances,
))


class LimitArgs(StrictSerializer, LimitMixin):
    pass


def _outstanding_bills(ctx, args):
    rows = get_outstanding_bills_report(organization=ctx.organization)
    rows.sort(key=lambda row: (row["due_date"], row["bill_number"]))
    limit = _limit(args, 25)
    return ToolResult(
        tool="get_outstanding_bills", status="ok",
        summary={"outstanding_bill_count": len(rows)},
        data={"bills": [
            {"bill_number": row["bill_number"], "vendor_name": row["vendor_name"], "due_date": row["due_date"],
             "amount_due": row["amount_due"], "status": row["status"], "bill_id": str(row["bill_id"])}
            for row in rows[:limit]
        ]},
        truncated=len(rows) > limit,
        sources=[report_source(report="outstanding_bills", key="current", label="Outstanding Bills",
                               route=_route("reports-outstanding-bills"))],
    )


register(Tool(
    name="get_outstanding_bills",
    description="Vendor bills not yet fully paid, earliest due first.",
    required_permissions=(Permission.VIEW_BILLS,),
    input_serializer=LimitArgs,
    handler=_outstanding_bills,
))


# --- Sales / purchases / expenses by dimension -----------------------------

class PeriodLimitArgs(PeriodArgs, LimitMixin):
    pass


def _grouped(tool, rows, amount_key, keep, report, route_name, label, args):
    limit = _limit(args, 10)
    return ToolResult(
        tool=tool, status="ok",
        summary={"from_date": args["from_date"], "to_date": args["to_date"], "group_count": len(rows),
                 "top": [{**{k: row[k] for k in keep[:1]}, amount_key: row[amount_key]} for row in rows[:min(limit, 5)]]},
        data={"rows": [{k: row[k] for k in (*keep, amount_key)} for row in rows[:limit]]},
        truncated=len(rows) > limit,
        sources=[report_source(report=report, key=f"{args['from_date']}:{args['to_date']}",
                               label=f"{label} {args['from_date']} to {args['to_date']}",
                               route=_route(route_name, from_date=args["from_date"], to_date=args["to_date"]))],
    )


def _sales_by_customer(ctx, args):
    rows = get_sales_by_customer(organization=ctx.organization, from_date=args["from_date"], to_date=args["to_date"])
    return _grouped("get_sales_by_customer", rows, "total_sales", ("customer_name", "invoice_count", "tax_total"),
                    "sales_by_customer", "reports-sales-by-customer", "Sales by Customer", args)


def _sales_by_item(ctx, args):
    rows = get_sales_by_item(organization=ctx.organization, from_date=args["from_date"], to_date=args["to_date"])
    return _grouped("get_sales_by_item", rows, "total_sales", ("item_name", "item_sku", "quantity"),
                    "sales_by_item", "reports-sales-by-item", "Sales by Item", args)


def _purchases_by_vendor(ctx, args):
    rows = get_purchases_by_vendor(organization=ctx.organization, from_date=args["from_date"], to_date=args["to_date"])
    return _grouped("get_purchases_by_vendor", rows, "total_purchases", ("vendor_name", "bill_count", "tax_total"),
                    "purchases_by_vendor", "reports-purchases-by-vendor", "Purchases by Vendor", args)


def _expenses_by_category(ctx, args):
    rows = get_expenses_by_category(organization=ctx.organization, from_date=args["from_date"], to_date=args["to_date"])
    return _grouped("get_expenses_by_category", rows, "total_amount", ("account_name", "account_code", "expense_count"),
                    "expenses_by_category", "reports-expenses-by-category", "Expenses by Category", args)


for _name, _desc, _perm, _handler in (
    ("get_sales_by_customer", "Invoiced sales per customer for a period, largest first.", Permission.VIEW_INVOICES, _sales_by_customer),
    ("get_sales_by_item", "Invoiced sales per item for a period, largest first.", Permission.VIEW_INVOICES, _sales_by_item),
    ("get_purchases_by_vendor", "Billed purchases per vendor for a period, largest first.", Permission.VIEW_BILLS, _purchases_by_vendor),
    ("get_expenses_by_category", "Recorded expenses per expense account for a period, largest first. "
     "Call once per period to compare periods.", Permission.VIEW_EXPENSES, _expenses_by_category),
):
    register(Tool(name=_name, description=_desc, required_permissions=(_perm,), input_serializer=PeriodLimitArgs, handler=_handler))


# --- Inventory -------------------------------------------------------------

class WarehouseArgs(StrictSerializer, LimitMixin):
    warehouse_id = serializers.UUIDField(required=False, help_text="Restrict to one warehouse.")


def _warehouse(args):
    if not args.get("warehouse_id"):
        return None
    from inventory.models.warehouse import Warehouse

    warehouse = Warehouse.objects.filter(pk=args["warehouse_id"]).first()  # tenant-scoped manager
    if warehouse is None:
        raise ToolError("not_found", "Warehouse not found.")
    return warehouse


def _inventory_valuation(ctx, args):
    warehouse = _warehouse(args)
    result = get_inventory_valuation(organization=ctx.organization, warehouse=warehouse)
    limit = _limit(args, 10)
    return ToolResult(
        tool="get_inventory_valuation", status="ok",
        summary={"total_value": result["total_value"], "stock_line_count": len(result["rows"]),
                 "warehouse": warehouse.name if warehouse else "all"},
        data={"largest_holdings": [
            {"item_name": r["item_name"], "item_sku": r["item_sku"], "warehouse_name": r["warehouse_name"],
             "quantity_on_hand": r["quantity_on_hand"], "average_cost": r["average_cost"], "inventory_value": r["inventory_value"]}
            for r in _top(result["rows"], "inventory_value", limit)
        ]},
        truncated=len(result["rows"]) > limit,
        sources=[report_source(report="inventory_valuation", key=str(args.get("warehouse_id") or "all"),
                               label="Inventory Valuation", route=_route("reports-inventory-valuation"))],
    )


def _inventory_summary(ctx, args):
    warehouse = _warehouse(args)
    rows = get_stock_summary(organization=ctx.organization, warehouse=warehouse)
    limit = _limit(args, 25)
    return ToolResult(
        tool="get_inventory_summary", status="ok",
        summary={"stock_line_count": len(rows), "warehouse": warehouse.name if warehouse else "all"},
        data={"stock": [
            {"item_name": r["item_name"], "item_sku": r["item_sku"], "warehouse_name": r["warehouse_name"],
             "quantity_on_hand": r["quantity_on_hand"], "inventory_value": r["inventory_value"]}
            for r in rows[:limit]
        ]},
        truncated=len(rows) > limit,
        sources=[report_source(report="inventory_summary", key=str(args.get("warehouse_id") or "all"),
                               label="Stock Summary", route=_route("reports-inventory-summary"))],
    )


def _low_stock(ctx, args):
    warehouse = _warehouse(args)
    rows = get_low_stock_report(organization=ctx.organization, warehouse=warehouse)
    limit = _limit(args, 25)
    return ToolResult(
        tool="get_low_stock", status="ok",
        summary={"low_stock_item_count": len(rows)},
        data={"items": [{k: row[k] for k in ("item_name", "item_sku", "on_hand", "reorder_level")} for row in rows[:limit]]},
        truncated=len(rows) > limit,
        sources=[report_source(report="low_stock", key=str(args.get("warehouse_id") or "all"),
                               label="Low Stock Report", route=_route("reports-inventory-low-stock"))],
    )


register(Tool(name="get_inventory_valuation", description="Total inventory value (weighted-average cost) and largest stock holdings.",
              required_permissions=(Permission.VIEW_INVENTORY,), input_serializer=WarehouseArgs, handler=_inventory_valuation))
register(Tool(name="get_inventory_summary", description="Quantity on hand per item and warehouse.",
              required_permissions=(Permission.VIEW_INVENTORY,), input_serializer=WarehouseArgs, handler=_inventory_summary))
register(Tool(name="get_low_stock", description="Items at or below their reorder level.",
              required_permissions=(Permission.VIEW_INVENTORY,), input_serializer=WarehouseArgs, handler=_low_stock))


# --- GST -------------------------------------------------------------------

def _gst_summary(ctx, args):
    result = get_gst_summary(organization=ctx.organization, date_from=args["from_date"], date_to=args["to_date"])
    return ToolResult(
        tool="get_gst_summary", status="ok",
        summary={
            "from_date": args["from_date"], "to_date": args["to_date"],
            "output_tax": result["output_tax"], "input_tax": result["input_tax"],
            # Stated explicitly so the assistant never "helpfully" subtracts:
            # EasyBook does not compute a net GST payable figure (ITC
            # utilisation order, blocked credits, cash vs credit ledger),
            # so none may be reported.
            "net_gst_payable": "not computed by EasyBook — see GSTR-3B figures",
        },
        data={"gstr3b": result["gstr3b"]},
        sources=[report_source(report="gst_summary", key=f"{args['from_date']}:{args['to_date']}",
                               label=f"GST Summary {args['from_date']} to {args['to_date']}",
                               route=_route("reports-tax-gst-summary", from_date=args["from_date"], to_date=args["to_date"]))],
    )


register(Tool(
    name="get_gst_summary",
    description="GST output tax, input tax (ITC) and GSTR-3B summary figures for a period, from the compliance registers.",
    required_permissions=(Permission.VIEW_RETURNS,),
    input_serializer=PeriodArgs,
    handler=_gst_summary,
))


# --- Projects --------------------------------------------------------------

class ProjectArgs(StrictSerializer, LimitMixin):
    status = serializers.ChoiceField(choices=["draft", "active", "on_hold", "completed", "cancelled"], required=False)


def _project_profitability(ctx, args):
    rows = get_project_profitability_report(organization=ctx.organization, status=args.get("status"))
    limit = _limit(args, 10)
    keep = ("project_code", "name", "status", "revenue", "total_cost", "margin", "margin_percent")
    return ToolResult(
        tool="get_project_profitability", status="ok",
        summary={"project_count": len(rows)},
        data={"projects": [{k: row[k] for k in keep} for row in _top(rows, "revenue", limit)]},
        truncated=len(rows) > limit,
        sources=[report_source(report="project_profitability", key=args.get("status") or "all",
                               label="Project Profitability", route=_route("reports-project-profitability"))],
    )


register(Tool(
    name="get_project_profitability",
    description="Revenue, cost and margin per project.",
    required_permissions=(Permission.VIEW_ALL_TIMESHEETS,),
    input_serializer=ProjectArgs,
    handler=_project_profitability,
))


# --- General ledger --------------------------------------------------------

class LedgerArgs(PeriodLimitArgs):
    account_code = serializers.CharField(max_length=32, help_text="Chart of accounts code, e.g. 6000.")


def _general_ledger(ctx, args):
    from accounting.models.account import Account
    from reports.selectors.gl_journal import get_general_ledger_report

    account = Account.objects.filter(code=args["account_code"]).first()  # tenant-scoped manager
    if account is None:
        raise ToolError("not_found", "Account not found.")
    ledger = get_general_ledger_report(account=account, from_date=args["from_date"], to_date=args["to_date"])
    limit = _limit(args, 25)
    entries = ledger["entries"]
    return ToolResult(
        tool="get_general_ledger", status="ok",
        summary={"account": f"{account.code} {account.name}", "from_date": args["from_date"], "to_date": args["to_date"],
                 "opening_balance": ledger["opening_balance"], "closing_balance": ledger["closing_balance"],
                 "entry_count": len(entries)},
        data={"entries": [
            {"posting_date": e["journal_entry"].posting_date, "debit": e["debit"], "credit": e["credit"],
             "running_balance": e["running_balance"], "description": (e["line"].description or "")[:120]}
            for e in entries[-limit:]
        ]},
        truncated=len(entries) > limit,
        sources=[report_source(report="general_ledger", key=f"{account.code}:{args['from_date']}:{args['to_date']}",
                               label=f"General Ledger {account.code} {account.name}",
                               route=_route("reports-general-ledger", account=account.id,
                                            from_date=args["from_date"], to_date=args["to_date"]))],
    )


register(Tool(
    name="get_general_ledger",
    description="Posted ledger lines and running balance for one account over a period (most recent lines).",
    required_permissions=(Permission.VIEW_TRANSACTIONS,),
    input_serializer=LedgerArgs,
    handler=_general_ledger,
))
