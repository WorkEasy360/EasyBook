"""Phase 6 concurrency acceptance gate.

Real threads on real connections (`TransactionTestCase`), because `TestCase`'s
outer wrapping transaction serialises everything onto one connection and would
prove nothing.

The races here are the banking shapes of the one that let a single goods
receipt be billed five times in Phase 4: a derived total read without a lock,
then written. Two of them:

  * two imports of overlapping statements, each counting the rows already
    held and each importing the same excess;
  * two confirmations against one payment, each seeing "nothing matched yet".
"""

import threading
from decimal import Decimal

from django.db import connection

from banking.models.match import BankTransactionMatch
from banking.models.statement import BankTransaction
from banking.selectors import get_counterpart_matched_amount
from banking.services.imports import import_csv_statement
from banking.services.matching import create_match
from banking.tests.base import SIGNED_MAPPING, BankingFixtureMixin, BankingTransactionTestsBase
from core.tenancy import tenant_context

OVERLAPPING_CSV = """Date,Narration,Ref,Amount
2026-04-10,NEFT ACME LTD,REF001,1000.00
2026-04-11,UPI SWIGGY ORDER,REF002,-450.50
"""


def _run_concurrently(target, count=5):
    """Runs `target` in `count` threads, collecting (results, errors).
    Each thread closes its connection so it genuinely contends on its own."""
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


class ConcurrentStatementImportTests(BankingTransactionTestsBase):
    def test_simultaneous_overlapping_imports_import_each_row_once(self):
        """Without the lock on the bank account, each import counts the rows
        already held, both see the same count, and both insert the excess —
        doubling the statement and leaving the account permanently
        unreconcilable."""

        def do_import(index):
            def run():
                with tenant_context(organization_id=self.org_a.id):
                    return import_csv_statement(
                        organization=self.org_a,
                        bank_account=self.bank,
                        content=OVERLAPPING_CSV + f"2026-04-12,PADDING {index},P{index},-1.00\n",
                        mapping=SIGNED_MAPPING,
                        file_name=f"stmt-{index}.csv",
                    )

            return run

        results, errors = [], []
        lock = threading.Lock()

        def runner(index):
            try:
                value = do_import(index)()
                with lock:
                    results.append(value)
            except Exception as exc:
                with lock:
                    errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=runner, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        with tenant_context(organization_id=self.org_a.id):
            # The two shared rows must exist exactly once between them; each
            # import's own padding row is genuinely distinct.
            shared = BankTransaction.objects.filter(bank_reference__in=["REF001", "REF002"])
            self.assertEqual(
                shared.count(), 2,
                f"the two overlapping rows were imported {shared.count()} times, not once each",
            )
            self.assertEqual(BankTransaction.objects.count(), 2 + len(results))

    def tearDown(self):
        with tenant_context(organization_id=self.org_a.id):
            BankTransactionMatch.objects.all().delete()
        super().tearDown()


class ConcurrentMatchConfirmationTests(BankingTransactionTestsBase):
    def test_one_payment_cannot_explain_several_deposits_concurrently(self):
        """The Phase 4 double-billing race, in banking form. Each thread asks
        "how much of this payment is already matched?", all see zero, and all
        confirm — one 1,000 receipt explaining five separate 1,000 deposits."""
        with tenant_context(organization_id=self.org_a.id):
            payment = self._customer_payment(amount=Decimal("1000.00"))
            transactions = [
                self._txn(amount="1000.00", description=f"DEPOSIT {i}") for i in range(5)
            ]

        def confirm(transaction):
            def run():
                with tenant_context(organization_id=self.org_a.id):
                    return create_match(
                        transaction_id=transaction.id,
                        organization=self.org_a,
                        counterpart_field="customer_payment",
                        counterpart=payment,
                        actor=self.user_a,
                    )

            return run

        results, errors = [], []
        lock = threading.Lock()

        def runner(transaction):
            try:
                value = confirm(transaction)()
                with lock:
                    results.append(value)
            except Exception as exc:
                with lock:
                    errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=runner, args=(t,)) for t in transactions]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        with tenant_context(organization_id=self.org_a.id):
            matched = get_counterpart_matched_amount(
                counterpart_field="customer_payment", counterpart=payment
            )
            self.assertEqual(
                matched, Decimal("1000.00"),
                f"the payment ended up explaining {matched}, not its own 1000.00",
            )
            self.assertEqual(len(results), 1)
            self.assertEqual(len(errors), 4)

    def test_concurrent_confirmations_cannot_over_explain_one_line(self):
        """The mirror race: several payments, one statement line."""
        with tenant_context(organization_id=self.org_a.id):
            payments = [self._customer_payment(amount=Decimal("1000.00")) for _ in range(4)]
            transaction = self._txn(amount="1000.00", description="BATCH CREDIT")

        results, errors = [], []
        lock = threading.Lock()

        def runner(payment):
            try:
                with tenant_context(organization_id=self.org_a.id):
                    value = create_match(
                        transaction_id=transaction.id,
                        organization=self.org_a,
                        counterpart_field="customer_payment",
                        counterpart=payment,
                        amount=Decimal("1000.00"),
                        actor=self.user_a,
                    )
                with lock:
                    results.append(value)
            except Exception as exc:
                with lock:
                    errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=runner, args=(p,)) for p in payments]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        with tenant_context(organization_id=self.org_a.id):
            confirmed = BankTransactionMatch.objects.filter(
                transaction=transaction, is_confirmed=True
            )
            total = sum((m.amount for m in confirmed), Decimal("0"))
            self.assertEqual(
                total, Decimal("1000.00"),
                f"matches explain {total} of a 1000.00 line",
            )
            self.assertEqual(len(results), 1)


class ConcurrentManualTransactionTests(BankingTransactionTestsBase):
    def test_simultaneous_identical_manual_entries_get_distinct_ordinals(self):
        """Two people adding the same genuine repeat at once must not both
        read the same occurrence count and collide on the unique
        (account, fingerprint, ordinal) constraint."""

        def run():
            with tenant_context(organization_id=self.org_a.id):
                return self._txn(amount="-500.00", description="ATM WITHDRAWAL")

        results, errors = _run_concurrently(run, count=5)

        with tenant_context(organization_id=self.org_a.id):
            ordinals = sorted(
                BankTransaction.objects.filter(bank_account=self.bank).values_list(
                    "duplicate_ordinal", flat=True
                )
            )
            self.assertEqual(
                ordinals, list(range(len(results))),
                "ordinals must be distinct and contiguous",
            )
            # Any thread that lost the race failed loudly rather than
            # silently writing a duplicate.
            self.assertEqual(len(results) + len(errors), 5)


__all__ = ["BankingFixtureMixin"]
