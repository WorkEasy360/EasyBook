import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest import TestCase as PlainTestCase

from django.test import TestCase

from accounting.models.account import AccountType
from accounting.services.accounts import create_account
from accounts.models import FiscalYear
from automation.conditions.evaluator import evaluate_condition, evaluate_conditions
from automation.triggers.facts import build_facts
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_org_with_owner
from items.models.item import ItemType
from items.services.items import create_item
from items.services.units import create_unit
from sales.services.customers import create_customer
from sales.services.invoices import create_invoice, post_invoice


def _condition(field, operator, value=""):
    return SimpleNamespace(field=field, operator=operator, value=value)


class ConditionEvaluatorTests(PlainTestCase):
    def test_equals(self):
        self.assertTrue(
            evaluate_condition(
                trigger_type="invoice.posted", condition=_condition("status", "equals", "sent"),
                facts={"status": "sent"},
            )
        )
        self.assertFalse(
            evaluate_condition(
                trigger_type="invoice.posted", condition=_condition("status", "equals", "sent"),
                facts={"status": "draft"},
            )
        )

    def test_decimal_comparison(self):
        facts = {"amount_due": Decimal("15000.00")}
        self.assertTrue(
            evaluate_condition(
                trigger_type="invoice.overdue",
                condition=_condition("amount_due", "greater_than", "10000.00"),
                facts=facts,
            )
        )
        self.assertFalse(
            evaluate_condition(
                trigger_type="invoice.overdue",
                condition=_condition("amount_due", "greater_than", "20000.00"),
                facts=facts,
            )
        )

    def test_contains(self):
        self.assertTrue(
            evaluate_condition(
                trigger_type="invoice.posted", condition=_condition("customer_id", "contains", "abc"),
                facts={"customer_id": "xxabcxx"},
            )
        )

    def test_is_empty_and_is_not_empty(self):
        self.assertTrue(
            evaluate_condition(
                trigger_type="invoice.posted", condition=_condition("customer_id", "is_empty"), facts={}
            )
        )
        self.assertFalse(
            evaluate_condition(
                trigger_type="invoice.posted", condition=_condition("customer_id", "is_not_empty"), facts={}
            )
        )

    def test_missing_fact_fails_non_empty_operators(self):
        self.assertFalse(
            evaluate_condition(
                trigger_type="invoice.posted", condition=_condition("customer_id", "equals", "x"), facts={}
            )
        )

    def test_evaluate_conditions_is_and_only(self):
        facts = {"amount_due": Decimal("15000.00"), "status": "sent"}
        conditions = [
            _condition("amount_due", "greater_than", "10000.00"),
            _condition("status", "equals", "sent"),
        ]
        self.assertTrue(evaluate_conditions(trigger_type="invoice.posted", conditions=conditions, facts=facts))

        conditions_with_failure = [*conditions, _condition("status", "equals", "draft")]
        self.assertFalse(
            evaluate_conditions(trigger_type="invoice.posted", conditions=conditions_with_failure, facts=facts)
        )

class FactBuilderTests(TestCase):
    """build_facts must reload authoritative live data, never trust a
    payload (phase section 16)."""

    def setUp(self):
        self.org, self.owner, _ = make_org_with_owner("Org", "automation-facts@example.com")
        self.currency = make_currency("INR")
        with tenant_context(organization_id=self.org.id):
            FiscalYear.objects.create(
                organization=self.org, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )
            self.ar_account = create_account(
                organization=self.org, code="1100", name="AR", account_type=AccountType.ASSET
            )
            self.sales_account = create_account(
                organization=self.org, code="4000", name="Sales", account_type=AccountType.INCOME
            )
            self.unit = create_unit(organization=self.org, code="EA", name="Each")
            self.item = create_item(
                organization=self.org, item_type=ItemType.SERVICE, name="Consulting", unit=self.unit,
                sales_account=self.sales_account,
            )
            self.customer = create_customer(
                organization=self.org, customer_code="CUST-1", display_name="Acme", currency=self.currency
            )

    def test_invoice_posted_facts_reflect_live_amount_due(self):
        with tenant_context(organization_id=self.org.id):
            invoice = create_invoice(
                organization=self.org, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
                due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account,
                lines=[{"item": self.item, "quantity": Decimal("1"), "unit_price": Decimal("500.00")}],
            )
            invoice = post_invoice(invoice_id=invoice.id, organization=self.org, actor=self.owner)

            facts = build_facts(trigger_type="invoice.posted", organization=self.org, entity_id=invoice.id)
            self.assertEqual(facts["amount_due"], Decimal("500.00"))
            self.assertEqual(facts["status"], "sent")

    def test_unknown_entity_returns_empty_facts(self):
        with tenant_context(organization_id=self.org.id):
            facts = build_facts(trigger_type="invoice.posted", organization=self.org, entity_id="00000000-0000-0000-0000-000000000000")
            self.assertEqual(facts, {})

    def test_manual_trigger_has_no_facts(self):
        with tenant_context(organization_id=self.org.id):
            facts = build_facts(trigger_type="manual", organization=self.org, entity_id="anything")
            self.assertEqual(facts, {})
