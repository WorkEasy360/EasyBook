import threading

from django.db import connection
from django.test import TransactionTestCase

from accounts.services import allocate_sequence_number
from core.tests.factories import make_organization


class NumberSequenceTests(TransactionTestCase):
    def setUp(self):
        self.organization = make_organization("Acme")

    def test_sequential_allocation_increments(self):
        first = allocate_sequence_number(organization_id=self.organization.id, key="invoice", prefix="INV-")
        second = allocate_sequence_number(organization_id=self.organization.id, key="invoice", prefix="INV-")
        self.assertEqual(first, "INV-0001")
        self.assertEqual(second, "INV-0002")

    def test_concurrent_allocation_never_duplicates(self):
        # Pre-create the sequence row so every thread exercises the
        # select_for_update lock/increment path, not the unrelated
        # get_or_create-vs-concurrent-insert race for a brand new row.
        allocate_sequence_number(organization_id=self.organization.id, key="invoice", prefix="INV-")

        results = []
        errors = []
        lock = threading.Lock()

        def allocate():
            try:
                number = allocate_sequence_number(organization_id=self.organization.id, key="invoice", prefix="INV-")
            except Exception as exc:  # surface thread failures instead of losing them silently
                with lock:
                    errors.append(exc)
                return
            finally:
                connection.close()
            with lock:
                results.append(number)

        threads = [threading.Thread(target=allocate) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 10)
        self.assertEqual(len(results), len(set(results)), "sequence numbers must be unique under concurrency")
