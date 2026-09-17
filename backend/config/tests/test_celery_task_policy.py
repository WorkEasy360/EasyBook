"""Celery execution policy: routing, acknowledgement, limits, redelivery.

Regressions this guards against (phase 12 P0 remediation):

- No task was routed anywhere. infrastructure/terraform runs dedicated
  worker-critical / worker-heavy / worker-ai services, but every task landed
  on the default `celery` queue, so those workers sat idle while OCR,
  embedding and financial-document generation all queued behind each other
  on worker-default.
- Tasks were acknowledged BEFORE running (Celery's default), so a worker
  container killed mid-task by a deploy, scale-in or OOM lost that work.
- No task had a time limit, so one hung call pinned a worker process forever.

Late acknowledgement has its own trap on Redis: an unacknowledged message is
redelivered once the broker visibility timeout passes, so the timeout must
outlast the longest a task can legitimately stay unacknowledged — its hard
time limit plus its longest retry countdown — or long work runs twice.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from config.celery import app

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
TERRAFORM_VARIABLES = REPO_ROOT / "infrastructure" / "terraform" / "variables.tf"

# Every module that defines tasks. Imported explicitly: which modules
# autodiscovery finds is config/tests/test_celery_autodiscovery.py's concern.
TASK_MODULES = ("ai.rag.tasks", "automation.tasks", "documents.tasks", "purchases.tasks", "sales.tasks")


def _registered_task_names() -> set[str]:
    import importlib

    for module in TASK_MODULES:
        importlib.import_module(module)
    return {name for name in app.tasks if not name.startswith("celery.")}


def _queues_consumed_by_terraform_workers() -> set[str]:
    queues = set()
    for match in re.finditer(r'"--queues=([^"]+)"', TERRAFORM_VARIABLES.read_text(encoding="utf-8")):
        queues.update(queue.strip() for queue in match.group(1).split(","))
    return queues


class TaskRoutingTests(SimpleTestCase):
    def test_every_task_has_an_explicit_route(self):
        unrouted = _registered_task_names() - set(settings.CELERY_TASK_ROUTES)
        self.assertEqual(unrouted, set(), "every task must be routed explicitly, never left on the default queue")

    def test_every_routed_queue_is_consumed_by_a_deployed_worker(self):
        consumed = _queues_consumed_by_terraform_workers()
        routed = {route["queue"] for route in settings.CELERY_TASK_ROUTES.values()}
        routed.add(settings.CELERY_TASK_DEFAULT_QUEUE)
        self.assertTrue(consumed, f"no --queues= found in {TERRAFORM_VARIABLES}")
        self.assertEqual(routed - consumed, set(), "a routed queue no worker consumes is a silent task black hole")

    def test_router_sends_work_to_its_dedicated_worker(self):
        expected = {
            "documents.tasks.run_ocr_task": "heavy",
            "ai.rag.tasks.index_document_task": "ai",
            "sales.tasks.generate_recurring_invoices_task": "critical",
            "automation.tasks.run_execution_task": "celery",
        }
        _registered_task_names()
        for task_name, queue in expected.items():
            routed = app.amqp.router.route({}, task_name)
            self.assertEqual(routed["queue"].name, queue, task_name)


class TaskAcknowledgementAndLimitTests(SimpleTestCase):
    def test_tasks_acknowledge_after_running_and_prefetch_one_at_a_time(self):
        self.assertTrue(app.conf.task_acks_late)
        self.assertEqual(app.conf.worker_prefetch_multiplier, 1)
        # With late acks a lost broker connection otherwise redelivers tasks
        # that are still running (Celery documents this default as changing
        # to True in 6.0).
        self.assertTrue(app.conf.worker_cancel_long_running_tasks_on_connection_loss)

    def test_every_task_has_a_bounded_soft_and_hard_time_limit(self):
        for name in sorted(_registered_task_names()):
            task = app.tasks[name]
            with self.subTest(task=name):
                self.assertIsNotNone(task.soft_time_limit)
                self.assertIsNotNone(task.time_limit)
                self.assertGreater(task.time_limit, task.soft_time_limit)

    def test_visibility_timeout_outlasts_every_task_plus_its_retry_countdown(self):
        visibility_timeout = app.conf.broker_transport_options["visibility_timeout"]
        for name in sorted(_registered_task_names()):
            task = app.tasks[name]
            longest_countdown = (task.retry_backoff_max or 0) if getattr(task, "autoretry_for", ()) else 0
            with self.subTest(task=name):
                self.assertGreater(visibility_timeout, task.time_limit + longest_countdown)
