"""Structured logs keep their fields, carry the request/task correlation id,
never leak one request's context into the next, and never carry secrets.

Before: the JSON formatter emitted only timestamp/level/logger/message (every
`extra={...}` payload was dropped), no filter attached request_id, audit rows
never recorded one, and X-Request-ID was taken verbatim and unbounded.
"""
import io
import json
import logging
import uuid

from django.test import TestCase
from rest_framework.test import APIClient

from accounting.models.account import AccountType
from audit.models import AuditLog
from core import request_context
from core.logging import JSONFormatter, RequestContextFilter
from core.tenancy import tenant_context
from core.tests.factories import make_org_with_owner


class _CapturedLogger:
    """Attaches the production formatter + filter to a logger for one test."""

    def __init__(self, name):
        self.logger = logging.getLogger(name)
        self.stream = io.StringIO()
        self.handler = logging.StreamHandler(self.stream)
        self.handler.addFilter(RequestContextFilter())
        self.handler.setFormatter(JSONFormatter())

    def __enter__(self):
        self.previous_level = self.logger.level
        self.logger.setLevel(logging.INFO)
        self.logger.addHandler(self.handler)
        return self

    def __exit__(self, *exc):
        self.logger.removeHandler(self.handler)
        self.logger.setLevel(self.previous_level)

    def records(self):
        return [json.loads(line) for line in self.stream.getvalue().splitlines() if line.strip()]


class JSONFormatterTests(TestCase):
    def test_extra_fields_are_preserved(self):
        with _CapturedLogger("easybook.test.extra") as captured:
            captured.logger.info("automation_execution_ran", extra={"execution_id": "e-1", "attempts": 3, "status": "ok"})
        record = captured.records()[0]
        self.assertEqual(record["message"], "automation_execution_ran")
        self.assertEqual(record["execution_id"], "e-1")
        self.assertEqual(record["attempts"], 3)
        self.assertEqual(record["status"], "ok")

    def test_secrets_are_redacted_by_key(self):
        with _CapturedLogger("easybook.test.redact") as captured:
            captured.logger.info(
                "event",
                extra={
                    "password": "hunter2",
                    "refresh": "eyJ-refresh",
                    "access_token": "eyJ-access",
                    "authorization": "Bearer x",
                    "api_key": "sk-123",
                    "client_secret": "s",
                    "cookie": "eb_rt=1",
                    "harmless": "kept",
                },
            )
        record = captured.records()[0]
        for key in ("password", "refresh", "access_token", "authorization", "api_key", "client_secret", "cookie"):
            self.assertEqual(record[key], "[redacted]", key)
        self.assertEqual(record["harmless"], "kept")
        self.assertNotIn("hunter2", json.dumps(record))

    def test_non_serializable_extras_do_not_break_logging(self):
        with _CapturedLogger("easybook.test.serial") as captured:
            captured.logger.info("event", extra={"when": object()})
        self.assertIn("when", captured.records()[0])


class RequestIdTests(TestCase):
    def setUp(self):
        self.org, self.owner, _ = make_org_with_owner("Correlation Org", "correlation@example.com")
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.headers = {"HTTP_X_ORGANIZATION_ID": str(self.org.id)}

    def _get(self, request_id=None):
        extra = {"HTTP_X_REQUEST_ID": request_id} if request_id is not None else {}
        return self.client.get("/api/v1/accounting/accounts/", **self.headers, **extra)

    def _is_generated(self, value):
        return str(uuid.UUID(value)) == value

    def test_valid_supplied_id_is_kept_and_echoed(self):
        response = self._get("req-7f3a.b_c:1")
        self.assertEqual(response["X-Request-ID"], "req-7f3a.b_c:1")

    def test_oversized_id_is_replaced(self):
        response = self._get("x" * 65)
        self.assertTrue(self._is_generated(response["X-Request-ID"]))
        response = self._get("x" * 10_000)
        self.assertTrue(self._is_generated(response["X-Request-ID"]))

    def test_malformed_id_is_replaced(self):
        for bad in ["has space", "<script>", "a/b", "ünïcode", "semi;colon", "quote\"d", ""]:
            response = self._get(bad)
            self.assertTrue(self._is_generated(response["X-Request-ID"]), bad)

    def test_missing_id_is_generated(self):
        self.assertTrue(self._is_generated(self._get()["X-Request-ID"]))

    def test_request_id_and_tenant_appear_in_json_logs(self):
        with _CapturedLogger("easybook.request") as captured:
            self._get("corr-123")
        finished = [r for r in captured.records() if r["message"] == "request_finished"]
        self.assertEqual(len(finished), 1)
        record = finished[0]
        self.assertEqual(record["request_id"], "corr-123")
        self.assertEqual(record["organization_id"], str(self.org.id))
        self.assertEqual(record["user_id"], str(self.owner.id))
        self.assertEqual(record["status"], 200)
        self.assertEqual(record["method"], "GET")
        self.assertIn("duration_ms", record)

    def test_context_does_not_leak_into_the_next_request_or_after_it(self):
        self._get("first-request")
        self.assertIsNone(request_context.get_request_id())

        anonymous = APIClient()
        with _CapturedLogger("easybook.request") as captured:
            anonymous.get("/api/v1/health/", HTTP_X_REQUEST_ID="second-request")
        record = next(r for r in captured.records() if r["message"] == "request_finished")
        self.assertEqual(record["request_id"], "second-request")
        self.assertNotIn("organization_id", record)
        self.assertNotIn("user_id", record)

    def test_audit_record_is_correlated_to_the_request(self):
        response = self.client.post(
            "/api/v1/accounting/accounts/",
            {"code": "1999", "name": "Correlated", "account_type": AccountType.ASSET},
            format="json",
            HTTP_X_REQUEST_ID="audit-corr-1",
            **self.headers,
        )
        self.assertEqual(response.status_code, 201, response.data)
        with tenant_context(organization_id=self.org.id):
            log = AuditLog.objects.get(object_type="accounting.Account", object_id=response.data["id"])
        self.assertEqual(log.request_id, "audit-corr-1")


class TaskContextTests(TestCase):
    def test_celery_task_establishes_and_clears_its_own_context(self):
        from config.celery import bind_task_context, clear_task_context

        class _Task:
            name = "automation.tasks.run_execution_task"

        bind_task_context(task_id="task-abc", task=_Task())
        try:
            with _CapturedLogger("easybook.test.task") as captured:
                captured.logger.info("inside_task")
            record = captured.records()[0]
            self.assertEqual(record["task_id"], "task-abc")
            self.assertEqual(record["task_name"], "automation.tasks.run_execution_task")
            self.assertEqual(record["request_id"], "task-abc")
        finally:
            clear_task_context()
        self.assertIsNone(request_context.get_task_id())
        self.assertIsNone(request_context.get_request_id())
