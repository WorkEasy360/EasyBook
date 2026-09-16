"""Concurrent filing races.

Real threads on real connections (`TransactionTestCase`), because `TestCase`'s
outer wrapping transaction serialises everything onto one connection and would
prove nothing.

The race that matters here is worse than most in this codebase. A duplicate
invoice row can be voided; a duplicate IRN is a second FILING with the
government against one invoice, and unwinding it means cancelling on the portal
within a limited window. Two simultaneous "report this invoice" requests must
produce exactly one.
"""

import threading
from decimal import Decimal

from django.db import connection

from compliance.models import EInvoiceDocument, EInvoiceStatus, EWayBill
from compliance.services.einvoice import record_irn
from compliance.services.ewaybill import record_ewaybill
from compliance.tests.base import ComplianceTransactionTestsBase
from core.tenancy import tenant_context

SAMPLE_IRN = "a" * 64


def _run_concurrently(target, count=5):
    """N threads, each on its OWN connection.

    `connection.close()` in the finally block is what makes the threads
    genuinely contend rather than quietly reuse one pooled connection - the
    same primitive `banking/tests/test_concurrency.py` and
    `purchases/tests/test_concurrency.py` use.
    """
    results, errors = [], []
    lock = threading.Lock()

    def runner():
        try:
            value = target()
            with lock:
                results.append(value)
        except Exception as exc:
            with lock:
                errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=runner) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results, errors


class ConcurrentIrnRecordingTests(ComplianceTransactionTestsBase):
    def tearDown(self):
        with tenant_context(organization_id=self.org_a.id):
            EInvoiceDocument.all_objects.all().update(status=EInvoiceStatus.CANCELLED)

    def test_simultaneous_recordings_of_one_irn_create_exactly_one_filing(self):
        invoice = self.make_invoice(self.customer_mh)

        def record():
            with tenant_context(organization_id=self.org_a.id):
                return record_irn(document=invoice, irn=SAMPLE_IRN).id

        results, errors = _run_concurrently(record)

        self.assertEqual(errors, [], f"unexpected errors: {errors}")
        self.assertEqual(len(set(results)), 1, "the same IRN produced more than one record")
        with tenant_context(organization_id=self.org_a.id):
            self.assertEqual(
                EInvoiceDocument.all_objects.filter(invoice=invoice).count(), 1
            )

    def test_simultaneous_recordings_of_different_irns_let_only_one_win(self):
        # The dangerous shape: two operators file the same invoice on the
        # portal and each records what they got back. One must be refused.
        invoice = self.make_invoice(self.customer_ka)
        irns = [chr(ord("a") + index) * 64 for index in range(5)]
        results, errors = [], []
        lock = threading.Lock()

        def runner(irn):
            try:
                with tenant_context(organization_id=self.org_a.id):
                    value = record_irn(document=invoice, irn=irn).irn
                with lock:
                    results.append(value)
            except Exception as exc:
                with lock:
                    errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=runner, args=(irn,)) for irn in irns]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(results), 1, "more than one IRN was accepted for one invoice")
        self.assertEqual(len(errors), 4)
        with tenant_context(organization_id=self.org_a.id):
            self.assertEqual(
                EInvoiceDocument.all_objects.filter(invoice=invoice).count(), 1
            )


class ConcurrentEWayBillTests(ComplianceTransactionTestsBase):
    def test_simultaneous_recordings_produce_one_ewaybill(self):
        invoice = self.make_invoice(self.customer_ka, unit_price=Decimal("60000.00"))

        def record():
            with tenant_context(organization_id=self.org_a.id):
                return record_ewaybill(
                    document=invoice, ewb_no="123456789012", distance_km=300
                ).id

        results, errors = _run_concurrently(record)

        self.assertEqual(errors, [], f"unexpected errors: {errors}")
        self.assertEqual(len(set(results)), 1)
        with tenant_context(organization_id=self.org_a.id):
            self.assertEqual(EWayBill.all_objects.filter(invoice=invoice).count(), 1)
