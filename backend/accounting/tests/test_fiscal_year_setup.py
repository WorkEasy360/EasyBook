"""Fiscal-year onboarding: a brand-new organization must be able to create
its fiscal year and then post its first journal.

Before this path existed, FiscalYear had no serializer, view or service:
every freshly created organization failed every posting with
fiscal_year_not_found.
"""
import datetime
import threading

from django.core.cache import cache
from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import FiscalYear
from audit.models import AuditLog
from authz.roles import Role
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_membership, make_org_with_owner, make_user

FY_URL = "/api/v1/accounting/fiscal-years/"
STATUS_URL = "/api/v1/accounting/fiscal-years/setup-status/"


def _covering_today(start_month=4):
    today = timezone.localdate()
    start_year = today.year if today.month >= start_month else today.year - 1
    start = datetime.date(start_year, start_month, 1)
    end_year = start_year + 1 if start_month > 1 else start_year
    end_month = start_month - 1 if start_month > 1 else 12
    next_month_first = (
        datetime.date(end_year + 1, 1, 1) if end_month == 12 else datetime.date(end_year, end_month + 1, 1)
    )
    return start, next_month_first - datetime.timedelta(days=1)


class FreshOrganizationOnboardingTests(TransactionTestCase):
    def test_signup_org_fiscal_year_then_first_journal_posts(self):
        cache.clear()  # the auth throttle's counters live in the (shared, in-process) cache
        currency = make_currency("INR")
        client = APIClient()
        r = client.post(
            "/api/v1/auth/register/",
            {"email": "fy-fresh@example.com", "password": "Str0ng-pass-phrase!", "full_name": "Fresh"},
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.data)
        r = client.post("/api/v1/auth/login/", {"email": "fy-fresh@example.com", "password": "Str0ng-pass-phrase!"}, format="json")
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")
        r = client.post("/api/v1/organizations/", {"name": "Fresh Org", "default_currency": currency.pk}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        headers = {"HTTP_X_ORGANIZATION_ID": r.data["id"]}

        r = client.get(STATUS_URL, **headers)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["has_current_fiscal_year"])
        self.assertTrue(r.data["can_manage"])
        self.assertEqual(r.data["fiscal_year_start_month"], 4)

        start, end = _covering_today(4)
        r = client.post(FY_URL, {"start_date": start, "end_date": end}, format="json", **headers)
        self.assertEqual(r.status_code, 201, r.data)

        r = client.get(STATUS_URL, **headers)
        self.assertTrue(r.data["has_current_fiscal_year"])
        self.assertEqual(r.data["current"]["start_date"], start.isoformat())

        cash = client.post("/api/v1/accounting/accounts/", {"code": "1000", "name": "Cash", "account_type": "asset"}, format="json", **headers)
        equity = client.post("/api/v1/accounting/accounts/", {"code": "3000", "name": "Capital", "account_type": "equity"}, format="json", **headers)
        self.assertEqual((cash.status_code, equity.status_code), (201, 201), (cash.data, equity.data))
        journal = client.post(
            "/api/v1/accounting/journals/",
            {
                "posting_date": timezone.localdate().isoformat(),
                "currency": currency.pk,
                "lines": [
                    {"account_id": cash.data["id"], "debit": "5000.00"},
                    {"account_id": equity.data["id"], "credit": "5000.00"},
                ],
            },
            format="json",
            **headers,
        )
        self.assertEqual(journal.status_code, 201, journal.data)
        posted = client.post(f"/api/v1/accounting/journals/{journal.data['id']}/post/", **headers)
        self.assertEqual(posted.status_code, 200, posted.data)
        self.assertEqual(posted.data["status"], "posted")
        self.assertTrue(posted.data["journal_number"])


class FiscalYearCreationRulesTests(TransactionTestCase):
    def setUp(self):
        self.org, self.owner, _ = make_org_with_owner("FY Org", "fy-owner@example.com")
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.headers = {"HTTP_X_ORGANIZATION_ID": str(self.org.id)}

    def _post(self, start, end, client=None, headers=None):
        return (client or self.client).post(
            FY_URL, {"start_date": start, "end_date": end}, format="json", **(headers or self.headers)
        )

    def _count(self, org=None):
        org = org or self.org
        with tenant_context(organization_id=org.id):
            return FiscalYear.objects.filter(organization=org).count()

    def test_duplicate_submission_is_idempotent(self):
        first = self._post("2026-04-01", "2027-03-31")
        second = self._post("2026-04-01", "2027-03-31")
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(first.data["id"], second.data["id"])
        self.assertEqual(self._count(), 1)

    def test_overlapping_year_is_rejected(self):
        self.assertEqual(self._post("2026-04-01", "2027-03-31").status_code, 201)
        for start, end in [("2026-10-01", "2027-09-30"), ("2025-04-01", "2026-04-01"), ("2026-06-01", "2026-12-31")]:
            r = self._post(start, end)
            self.assertEqual(r.status_code, 409, (start, end, r.data))
            self.assertEqual(r.data["error"]["code"], "fiscal_year_overlap")
        self.assertEqual(self._count(), 1)

    def test_adjacent_following_year_is_allowed(self):
        self.assertEqual(self._post("2026-04-01", "2027-03-31").status_code, 201)
        self.assertEqual(self._post("2027-04-01", "2028-03-31").status_code, 201)
        self.assertEqual(self._count(), 2)

    def test_invalid_ranges_are_rejected(self):
        for start, end, code in [
            ("2027-03-31", "2026-04-01", "fiscal_year_invalid_range"),
            ("2026-04-01", "2026-04-01", "fiscal_year_invalid_range"),
            ("2026-04-01", "2028-03-31", "fiscal_year_too_long"),
        ]:
            r = self._post(start, end)
            self.assertEqual(r.status_code, 400, (start, end, r.data))
            self.assertEqual(r.data["error"]["code"], code)
        self.assertEqual(self._count(), 0)

    def test_creation_is_audited(self):
        r = self._post("2026-04-01", "2027-03-31")
        with tenant_context(organization_id=self.org.id):
            log = AuditLog.objects.get(object_type="accounts.FiscalYear", object_id=r.data["id"])
        self.assertEqual(log.action, AuditLog.Action.CREATE)
        self.assertEqual(log.actor_id, self.owner.id)

    def test_viewer_cannot_create_but_can_read_setup_status(self):
        viewer = make_user("fy-viewer@example.com")
        make_membership(self.org, viewer, role=Role.VIEWER)
        client = APIClient()
        client.force_authenticate(user=viewer)
        self.assertEqual(self._post("2026-04-01", "2027-03-31", client=client).status_code, 403)
        status = client.get(STATUS_URL, **self.headers)
        self.assertEqual(status.status_code, 200)
        self.assertFalse(status.data["can_manage"])
        self.assertEqual(self._count(), 0)

    def test_tenant_isolation(self):
        other_org, other_owner, _ = make_org_with_owner("Other FY Org", "fy-other@example.com")
        self.assertEqual(self._post("2026-04-01", "2027-03-31").status_code, 201)

        # The other org's owner cannot read or write this org's fiscal years…
        other = APIClient()
        other.force_authenticate(user=other_owner)
        self.assertEqual(other.get(FY_URL, **self.headers).status_code, 403)
        self.assertEqual(self._post("2026-04-01", "2027-03-31", client=other).status_code, 403)
        # …and in its own org sees none of them, and may create the same range.
        own = {"HTTP_X_ORGANIZATION_ID": str(other_org.id)}
        self.assertEqual(other.get(FY_URL, **own).data["results"], [])
        self.assertEqual(self._post("2026-04-01", "2027-03-31", client=other, headers=own).status_code, 201)
        self.assertEqual(self._count(), 1)
        self.assertEqual(self._count(other_org), 1)

    def test_status_is_false_when_only_a_past_year_exists(self):
        self.assertEqual(self._post("2020-04-01", "2021-03-31").status_code, 201)
        self.assertFalse(self.client.get(STATUS_URL, **self.headers).data["has_current_fiscal_year"])


class ConcurrentFiscalYearSetupTests(TransactionTestCase):
    def setUp(self):
        self.org, self.owner, _ = make_org_with_owner("FY Race Org", "fy-race@example.com")

    def _race(self, ranges):
        from accounting.services.fiscal import create_fiscal_year
        from core.exceptions import ApplicationError

        barrier = threading.Barrier(len(ranges))
        outcomes, lock = [], threading.Lock()

        def run(start, end):
            try:
                barrier.wait(5)
                with tenant_context(organization_id=self.org.id):
                    _, created = create_fiscal_year(
                        organization=self.org, start_date=start, end_date=end, actor=self.owner
                    )
                result = "created" if created else "existing"
            except ApplicationError as exc:
                result = exc.detail.code
            except Exception as exc:
                result = exc
            finally:
                connection.close()
            with lock:
                outcomes.append(result)

        threads = [threading.Thread(target=run, args=r) for r in ranges]
        for t in threads:
            t.start()
        for t in threads:
            t.join(15)
        with tenant_context(organization_id=self.org.id):
            return outcomes, FiscalYear.objects.filter(organization=self.org).count()

    def test_concurrent_identical_setup_creates_exactly_one(self):
        rng = (datetime.date(2026, 4, 1), datetime.date(2027, 3, 31))
        outcomes, count = self._race([rng] * 6)
        self.assertEqual(count, 1)
        self.assertEqual(sorted(outcomes), ["created"] + ["existing"] * 5)

    def test_concurrent_overlapping_setup_creates_exactly_one(self):
        outcomes, count = self._race(
            [
                (datetime.date(2026, 4, 1), datetime.date(2027, 3, 31)),
                (datetime.date(2026, 1, 1), datetime.date(2026, 12, 31)),
                (datetime.date(2026, 7, 1), datetime.date(2027, 6, 30)),
            ]
        )
        self.assertEqual(count, 1)
        self.assertEqual(sorted(map(str, outcomes)), ["created", "fiscal_year_overlap", "fiscal_year_overlap"])
