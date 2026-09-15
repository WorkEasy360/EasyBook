import datetime
from decimal import Decimal

from django.db import connection
from django.test import TransactionTestCase

from accounting.models.account import AccountType
from accounting.services.accounts import create_account
from accounts.models import FiscalYear
from core.tenancy import clear_tenant_context, tenant_context
from core.tests.factories import make_currency, make_org_with_owner
from inventory.services.warehouses import create_warehouse
from items.models.item import ItemType
from items.services.items import create_item
from items.services.units import create_unit
from sales.services.credit_notes import create_credit_note, issue_credit_note
from sales.services.customers import create_customer
from sales.services.deliveries import create_delivery_challan
from sales.services.invoices import create_invoice, post_invoice
from sales.services.payments import record_payment
from sales.services.quotes import create_quote
from sales.services.recurring_invoices import create_recurring_template
from sales.services.sales_orders import create_sales_order


class RawSQLRLSTests(TransactionTestCase):
    def setUp(self):
        self.org_a, self.user_a, _ = make_org_with_owner("Org A", "sales-rls-a@example.com")
        self.org_b, self.user_b, _ = make_org_with_owner("Org B", "sales-rls-b@example.com")
        self.currency = make_currency("INR")
        with tenant_context(organization_id=self.org_a.id):
            FiscalYear.objects.create(
                organization=self.org_a, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )
            ar_account_a = create_account(
                organization=self.org_a, code="1100", name="AR", account_type=AccountType.ASSET
            )
            sales_account_a = create_account(
                organization=self.org_a, code="4000", name="Sales", account_type=AccountType.INCOME
            )
            self.customer_a = create_customer(
                organization=self.org_a, customer_code="CUST-1", display_name="A", currency=self.currency
            )
            unit_a = create_unit(organization=self.org_a, code="EA", name="Each")
            item_a = create_item(organization=self.org_a, item_type=ItemType.PRODUCT, name="Widget", unit=unit_a)
            self.quote_a = create_quote(
                organization=self.org_a, customer=self.customer_a, issue_date="2026-04-01",
                lines=[{"item": item_a, "quantity": Decimal("1"), "unit_price": Decimal("10.00")}],
            )
            self.order_a = create_sales_order(
                organization=self.org_a, customer=self.customer_a, order_date="2026-04-01",
                lines=[{"item": item_a, "quantity": Decimal("1"), "unit_price": Decimal("10.00")}],
            )
            item_a_stock = create_item(
                organization=self.org_a, item_type=ItemType.PRODUCT, name="Stocked", unit=unit_a,
                track_inventory=True,
            )
            warehouse_a = create_warehouse(organization=self.org_a, code="MAIN", name="Main")
            self.challan_a = create_delivery_challan(
                organization=self.org_a, customer=self.customer_a, warehouse=warehouse_a,
                challan_date=datetime.date(2026, 4, 1),
                lines=[{"item": item_a_stock, "quantity": Decimal("1")}],
            )
            item_a_invoice = create_item(
                organization=self.org_a, item_type=ItemType.SERVICE, name="Consulting", unit=unit_a,
                sales_account=sales_account_a,
            )
            invoice_a = create_invoice(
                organization=self.org_a, customer=self.customer_a, invoice_date=datetime.date(2026, 4, 1),
                due_date=datetime.date(2026, 5, 1), receivable_account=ar_account_a,
                lines=[{"item": item_a_invoice, "quantity": Decimal("1"), "unit_price": Decimal("10.00")}],
            )
            self.invoice_a = post_invoice(invoice_id=invoice_a.id, organization=self.org_a)
            bank_account_a = create_account(
                organization=self.org_a, code="1000", name="Bank", account_type=AccountType.ASSET
            )
            self.payment_a = record_payment(
                organization=self.org_a, customer=self.customer_a, payment_date=datetime.date(2026, 4, 2),
                amount=Decimal("10.00"), destination_account=bank_account_a,
                allocations=[{"invoice": self.invoice_a, "amount": Decimal("10.00")}],
            )
            credit_account_a = create_account(
                organization=self.org_a, code="2200", name="Customer Advances", account_type=AccountType.LIABILITY
            )
            invoice_line_a = self.invoice_a.lines.first()
            credit_note_a = create_credit_note(
                organization=self.org_a, customer=self.customer_a, credit_note_date=datetime.date(2026, 4, 3),
                source_invoice=self.invoice_a, unapplied_credit_account=credit_account_a,
                lines=[{
                    "item": item_a_invoice, "quantity": Decimal("1"), "unit_price": Decimal("10.00"),
                    "source_invoice_line": invoice_line_a,
                }],
            )
            self.credit_note_a = issue_credit_note(credit_note_id=credit_note_a.id, organization=self.org_a)
            self.recurring_template_a = create_recurring_template(
                organization=self.org_a, customer=self.customer_a, frequency="monthly",
                start_date=datetime.date(2026, 4, 1), receivable_account=ar_account_a,
                lines=[{"item": item_a_invoice, "quantity": Decimal("1"), "unit_price": Decimal("10.00")}],
            )
        with tenant_context(organization_id=self.org_b.id):
            FiscalYear.objects.create(
                organization=self.org_b, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )
            ar_account_b = create_account(
                organization=self.org_b, code="1100", name="AR", account_type=AccountType.ASSET
            )
            sales_account_b = create_account(
                organization=self.org_b, code="4000", name="Sales", account_type=AccountType.INCOME
            )
            customer_b = create_customer(
                organization=self.org_b, customer_code="CUST-1", display_name="B", currency=self.currency
            )
            unit_b = create_unit(organization=self.org_b, code="EA", name="Each")
            item_b = create_item(organization=self.org_b, item_type=ItemType.PRODUCT, name="Gadget", unit=unit_b)
            create_quote(
                organization=self.org_b, customer=customer_b, issue_date="2026-04-01",
                lines=[{"item": item_b, "quantity": Decimal("1"), "unit_price": Decimal("10.00")}],
            )
            create_sales_order(
                organization=self.org_b, customer=customer_b, order_date="2026-04-01",
                lines=[{"item": item_b, "quantity": Decimal("1"), "unit_price": Decimal("10.00")}],
            )
            item_b_stock = create_item(
                organization=self.org_b, item_type=ItemType.PRODUCT, name="Stocked", unit=unit_b,
                track_inventory=True,
            )
            warehouse_b = create_warehouse(organization=self.org_b, code="MAIN", name="Main")
            create_delivery_challan(
                organization=self.org_b, customer=customer_b, warehouse=warehouse_b,
                challan_date=datetime.date(2026, 4, 1),
                lines=[{"item": item_b_stock, "quantity": Decimal("1")}],
            )
            item_b_invoice = create_item(
                organization=self.org_b, item_type=ItemType.SERVICE, name="Other", unit=unit_b,
                sales_account=sales_account_b,
            )
            invoice_b = create_invoice(
                organization=self.org_b, customer=customer_b, invoice_date=datetime.date(2026, 4, 1),
                due_date=datetime.date(2026, 5, 1), receivable_account=ar_account_b,
                lines=[{"item": item_b_invoice, "quantity": Decimal("1"), "unit_price": Decimal("10.00")}],
            )
            invoice_b = post_invoice(invoice_id=invoice_b.id, organization=self.org_b)
            bank_account_b = create_account(
                organization=self.org_b, code="1000", name="Bank", account_type=AccountType.ASSET
            )
            record_payment(
                organization=self.org_b, customer=customer_b, payment_date=datetime.date(2026, 4, 2),
                amount=Decimal("10.00"), destination_account=bank_account_b,
                allocations=[{"invoice": invoice_b, "amount": Decimal("10.00")}],
            )
            credit_account_b = create_account(
                organization=self.org_b, code="2200", name="Customer Advances", account_type=AccountType.LIABILITY
            )
            invoice_line_b = invoice_b.lines.first()
            credit_note_b = create_credit_note(
                organization=self.org_b, customer=customer_b, credit_note_date=datetime.date(2026, 4, 3),
                source_invoice=invoice_b, unapplied_credit_account=credit_account_b,
                lines=[{
                    "item": item_b_invoice, "quantity": Decimal("1"), "unit_price": Decimal("10.00"),
                    "source_invoice_line": invoice_line_b,
                }],
            )
            issue_credit_note(credit_note_id=credit_note_b.id, organization=self.org_b)
            create_recurring_template(
                organization=self.org_b, customer=customer_b, frequency="monthly",
                start_date=datetime.date(2026, 4, 1), receivable_account=ar_account_b,
                lines=[{"item": item_b_invoice, "quantity": Decimal("1"), "unit_price": Decimal("10.00")}],
            )

    def test_rls_blocks_direct_sql_without_tenant_context(self):
        clear_tenant_context()
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM sales_customer")
            self.assertEqual(cursor.fetchall(), [])
            cursor.execute("SELECT id FROM sales_quote")
            self.assertEqual(cursor.fetchall(), [])
            cursor.execute("SELECT id FROM sales_quoteline")
            self.assertEqual(cursor.fetchall(), [])
            cursor.execute("SELECT id FROM sales_salesorder")
            self.assertEqual(cursor.fetchall(), [])
            cursor.execute("SELECT id FROM sales_salesorderline")
            self.assertEqual(cursor.fetchall(), [])
            cursor.execute("SELECT id FROM sales_deliverychallan")
            self.assertEqual(cursor.fetchall(), [])
            cursor.execute("SELECT id FROM sales_deliverychallanline")
            self.assertEqual(cursor.fetchall(), [])
            cursor.execute("SELECT id FROM sales_invoice")
            self.assertEqual(cursor.fetchall(), [])
            cursor.execute("SELECT id FROM sales_invoiceline")
            self.assertEqual(cursor.fetchall(), [])
            cursor.execute("SELECT id FROM sales_customerpayment")
            self.assertEqual(cursor.fetchall(), [])
            cursor.execute("SELECT id FROM sales_paymentallocation")
            self.assertEqual(cursor.fetchall(), [])
            cursor.execute("SELECT id FROM sales_creditnote")
            self.assertEqual(cursor.fetchall(), [])
            cursor.execute("SELECT id FROM sales_creditnoteline")
            self.assertEqual(cursor.fetchall(), [])
            cursor.execute("SELECT id FROM sales_recurringinvoicetemplate")
            self.assertEqual(cursor.fetchall(), [])
            cursor.execute("SELECT id FROM sales_recurringinvoicetemplateline")
            self.assertEqual(cursor.fetchall(), [])

    def test_rls_direct_sql_scoped_to_org_a_cannot_see_org_b(self):
        with tenant_context(organization_id=self.org_a.id):
            with connection.cursor() as cursor:
                cursor.execute("SELECT id FROM sales_customer")
                self.assertEqual({row[0] for row in cursor.fetchall()}, {self.customer_a.id})
                cursor.execute("SELECT id FROM sales_quote")
                self.assertEqual({row[0] for row in cursor.fetchall()}, {self.quote_a.id})
                cursor.execute("SELECT id FROM sales_salesorder")
                self.assertEqual({row[0] for row in cursor.fetchall()}, {self.order_a.id})
                cursor.execute("SELECT id FROM sales_deliverychallan")
                self.assertEqual({row[0] for row in cursor.fetchall()}, {self.challan_a.id})
                cursor.execute("SELECT id FROM sales_invoice")
                self.assertEqual({row[0] for row in cursor.fetchall()}, {self.invoice_a.id})
                cursor.execute("SELECT id FROM sales_customerpayment")
                self.assertEqual({row[0] for row in cursor.fetchall()}, {self.payment_a.id})
                cursor.execute("SELECT id FROM sales_creditnote")
                self.assertEqual({row[0] for row in cursor.fetchall()}, {self.credit_note_a.id})
                cursor.execute("SELECT id FROM sales_recurringinvoicetemplate")
                self.assertEqual({row[0] for row in cursor.fetchall()}, {self.recurring_template_a.id})
