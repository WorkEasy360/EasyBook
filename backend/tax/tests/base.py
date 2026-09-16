"""Shared fixtures for the tax suite.

The three-class split is the one `purchases/tests/base.py` documents and is not
cosmetic: `TestCase` SUBCLASSES `TransactionTestCase`, so writing
`class T(TaxTestsBase, TransactionTestCase)` resolves by MRO to `TestCase` and
silently runs inside a wrapping transaction - which would defeat exactly the
tests that need committed rows visible to a second connection.
"""

from decimal import Decimal

from django.test import TestCase, TransactionTestCase

from accounting.models.account import AccountType
from accounting.services.accounts import create_account
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_org_with_owner
from tax.enums import TaxComponent, TaxDirection
from tax.models import StateCode
from tax.services.profile import set_tax_account_mapping, set_tax_profile
from tax.testing import ensure_state_codes

# Two real states, deliberately not adjacent in the master, plus a UT that
# levies UTGST rather than SGST - the three cases determination must separate.
MAHARASHTRA = "27"
KARNATAKA = "29"
CHANDIGARH = "04"


class TaxFixtureMixin:
    """Plain mixin, not a TestCase - see the module docstring."""

    def setUp(self):
        super().setUp()
        self.org_a, self.user_a, _ = make_org_with_owner("Org A", "owner-a@example.com")
        self.org_b, self.user_b, _ = make_org_with_owner("Org B", "owner-b@example.com")
        self.currency = make_currency("INR")

        # A TransactionTestCase anywhere in the run flushes tax_statecode and
        # data migrations are not re-run - see tax/testing.py.
        ensure_state_codes()

        self.maharashtra = StateCode.objects.get(code=MAHARASHTRA)
        self.karnataka = StateCode.objects.get(code=KARNATAKA)
        self.chandigarh = StateCode.objects.get(code=CHANDIGARH)

        with tenant_context(organization_id=self.org_a.id):
            self.output_cgst = create_account(
                organization=self.org_a, code="2110", name="Output CGST",
                account_type=AccountType.LIABILITY,
            )
            self.output_sgst = create_account(
                organization=self.org_a, code="2111", name="Output SGST/UTGST",
                account_type=AccountType.LIABILITY,
            )
            self.output_igst = create_account(
                organization=self.org_a, code="2112", name="Output IGST",
                account_type=AccountType.LIABILITY,
            )
            self.output_cess = create_account(
                organization=self.org_a, code="2113", name="Output Cess",
                account_type=AccountType.LIABILITY,
            )
            self.input_cgst = create_account(
                organization=self.org_a, code="1310", name="Input CGST",
                account_type=AccountType.ASSET,
            )
            self.input_sgst = create_account(
                organization=self.org_a, code="1311", name="Input SGST/UTGST",
                account_type=AccountType.ASSET,
            )
            self.input_igst = create_account(
                organization=self.org_a, code="1312", name="Input IGST",
                account_type=AccountType.ASSET,
            )
            self.tds_account = create_account(
                organization=self.org_a, code="2150", name="TDS Payable",
                account_type=AccountType.LIABILITY,
            )

            self.profile = set_tax_profile(
                organization=self.org_a,
                state_code=MAHARASHTRA,
                gstin="27AAPFU0939F1ZV",
                actor=self.user_a,
            )

    def map_output_accounts(self):
        """Wires the OUTPUT direction. Called explicitly rather than in setUp so
        that tests can also exercise the UNMAPPED path, which is how every
        organization predating this phase still posts."""
        with tenant_context(organization_id=self.org_a.id):
            for component, account in (
                (TaxComponent.CGST, self.output_cgst),
                (TaxComponent.SGST_UTGST, self.output_sgst),
                (TaxComponent.IGST, self.output_igst),
                (TaxComponent.CESS, self.output_cess),
            ):
                set_tax_account_mapping(
                    organization=self.org_a, component=component,
                    direction=TaxDirection.OUTPUT, account=account,
                )

    def map_input_accounts(self):
        with tenant_context(organization_id=self.org_a.id):
            for component, account in (
                (TaxComponent.CGST, self.input_cgst),
                (TaxComponent.SGST_UTGST, self.input_sgst),
                (TaxComponent.IGST, self.input_igst),
            ):
                set_tax_account_mapping(
                    organization=self.org_a, component=component,
                    direction=TaxDirection.INPUT, account=account,
                )


class TaxTestsBase(TaxFixtureMixin, TestCase):
    """Default base: fast, wrapped in a transaction rolled back per test."""


class TaxTransactionTestsBase(TaxFixtureMixin, TransactionTestCase):
    """For tests needing real transaction boundaries or a second connection.

    The state master truncated by this class's flush is restored by
    `ensure_state_codes()` in the mixin's setUp, which is cheaper and more
    predictable than `serialized_rollback`.
    """


ZERO = Decimal("0")
