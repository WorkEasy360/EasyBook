"""Architecture fitness: invariants that must hold for the whole codebase, not
one feature. Each check scans every app, so a new module that breaks a rule
fails here even if its own tests never thought to look.

Covered elsewhere and deliberately not duplicated here:
- every Celery task routed, consumed, with time limits: config/tests/test_celery_task_policy.py
- runtime DB role not superuser/BYPASSRLS: core/tests/test_db_preflight.py
- production configuration fails closed: config/tests/test_production_settings.py
- BFF never proxies /admin or traversal paths: frontend/src/lib/api/bff-path.test.ts
- frontend production image builds and runs: .github/workflows/frontend-ci.yml
"""

import ast
from pathlib import Path

from django.apps import apps
from django.db import connection, models
from django.test import TestCase

BACKEND = Path(__file__).resolve().parents[2]
SKIP_PARTS = {"tests", "migrations", ".venv", "__pycache__", "node_modules"}


def _app_dirs():
    return sorted(p for p in BACKEND.iterdir() if p.is_dir() and (p / "apps.py").exists())


def _source_files():
    for app in _app_dirs():
        for path in app.rglob("*.py"):
            if SKIP_PARTS.isdisjoint(path.relative_to(BACKEND).parts):
                yield path


def _parse(path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _rel(path):
    return path.relative_to(BACKEND).as_posix()


def _receiver_names(node):
    """Every bare name in an attribute/call chain: JournalLine.objects.filter(...).update -> {JournalLine}."""
    names = set()
    while True:
        if isinstance(node, ast.Attribute):
            node = node.value
        elif isinstance(node, ast.Call):
            node = node.func
        elif isinstance(node, ast.Name):
            names.add(node.id)
            return names
        else:
            return names


WRITE_METHODS = {"create", "bulk_create", "update", "delete", "get_or_create", "update_or_create", "bulk_update"}


def _model_writes_outside(model_names, owner_app):
    """(file:line, snippet) for every write to `model_names` outside `owner_app`."""
    offenders = []
    for path in _source_files():
        if path.relative_to(BACKEND).parts[0] == owner_app:
            continue
        for node in ast.walk(_parse(path)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id in model_names:
                offenders.append(f"{_rel(path)}:{node.lineno} instantiates {func.id}")
            elif isinstance(func, ast.Attribute) and func.attr in WRITE_METHODS:
                hit = _receiver_names(func.value) & model_names
                if hit:
                    offenders.append(f"{_rel(path)}:{node.lineno} {sorted(hit)[0]}...{func.attr}()")
    return offenders


class TenantTablesAreRlsProtectedTests(TestCase):
    def test_every_organization_owned_table_forces_rls_with_a_policy(self):
        tables = set()
        for model in apps.get_models():
            if model._meta.proxy or not model._meta.managed:
                continue
            field_names = {f.name for f in model._meta.concrete_fields}
            if "organization" in field_names:
                tables.add(model._meta.db_table)
        self.assertGreater(len(tables), 20, "sanity: the scan found the tenant tables")

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                       (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname)
                FROM pg_class c
                WHERE c.relkind = 'r' AND c.relname = ANY(%s)
                """,
                [sorted(tables)],
            )
            rows = {name: (enabled, forced, policies) for name, enabled, forced, policies in cursor.fetchall()}

        unprotected = sorted(
            f"{t} (enabled={rows.get(t, (None,))[0]}, forced={rows.get(t, (None, None))[1]}, policies={rows.get(t, (0, 0, 0))[2]})"
            for t in tables
            if t not in rows or not (rows[t][0] and rows[t][1] and rows[t][2] >= 1)
        )
        self.assertEqual(unprotected, [], "Tenant tables without ENABLE + FORCE RLS and a policy")


class ModuleBoundaryTests(TestCase):
    def test_no_ledger_writes_outside_accounting(self):
        # accounting.services.posting is the single authoritative path to the
        # ledger (accounting/CLAUDE.md). Reads elsewhere are fine.
        self.assertEqual(_model_writes_outside({"JournalEntry", "JournalLine"}, "accounting"), [])
        # Not vacuous: the same scan does see accounting's own writes.
        self.assertGreater(len(_model_writes_outside({"JournalEntry", "JournalLine"}, "<no app>")), 3)

    def test_no_stock_movement_writes_outside_inventory(self):
        self.assertEqual(_model_writes_outside({"StockMovement"}, "inventory"), [])
        self.assertGreater(len(_model_writes_outside({"StockMovement"}, "<no app>")), 0)


class MoneyIsNeverFloatTests(TestCase):
    # The only float() conversions allowed: the government e-Invoice / e-Way
    # Bill JSON payload builders, which serialize already-computed Decimal
    # values into the schema's JSON numbers at the external boundary.
    FLOAT_ALLOWED_FILES = {
        "compliance/services/einvoice_payload.py",
        "compliance/services/ewaybill_payload.py",
    }
    MONEY_APPS = {"accounting", "accounts", "banking", "compliance", "inventory", "items", "projects", "purchases", "reports", "sales", "tax"}

    def test_no_model_stores_a_float(self):
        floats = [
            f"{model._meta.label}.{field.name}"
            for model in apps.get_models()
            for field in model._meta.concrete_fields
            if isinstance(field, models.FloatField)
        ]
        self.assertEqual(floats, [])

    def test_no_float_arithmetic_in_money_modules(self):
        offenders = []
        for path in _source_files():
            rel = _rel(path)
            if path.relative_to(BACKEND).parts[0] not in self.MONEY_APPS or rel in self.FLOAT_ALLOWED_FILES:
                continue
            for node in ast.walk(_parse(path)):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "float":
                    offenders.append(f"{rel}:{node.lineno}")
                if isinstance(node, ast.Constant) and isinstance(node.value, float):
                    offenders.append(f"{rel}:{node.lineno} float literal {node.value!r}")
        self.assertEqual(offenders, [])


class TaskDispatchAfterCommitTests(TestCase):
    """A task enqueued inside the request transaction can run before it
    commits, see pre-commit state and skip its work. Every .delay() /
    .apply_async() in application code must be inside transaction.on_commit
    — except in Celery task modules (which dispatch from their own committed
    context) and management commands (no request transaction)."""

    def test_request_path_dispatch_waits_for_commit(self):
        offenders, inspected = [], 0
        for path in _source_files():
            parts = path.relative_to(BACKEND).parts
            if path.name == "tasks.py" or "management" in parts:
                continue
            tree = _parse(path)
            parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                    continue
                if node.func.attr not in {"delay", "apply_async"}:
                    continue
                inspected += 1
                ancestor, inside_on_commit = parents.get(node), False
                while ancestor is not None:
                    if (
                        isinstance(ancestor, ast.Call)
                        and isinstance(ancestor.func, ast.Attribute)
                        and ancestor.func.attr == "on_commit"
                    ):
                        inside_on_commit = True
                        break
                    ancestor = parents.get(ancestor)
                if not inside_on_commit:
                    offenders.append(f"{_rel(path)}:{node.lineno}")
        self.assertEqual(offenders, [])
        self.assertGreater(inspected, 1, "sanity: the scan found request-path dispatch sites")


class CriticalPostsAreIdempotentTests(TestCase):
    """Money-moving creates that have no DRAFT->POSTED lifecycle to make a
    retry harmless must honour Idempotency-Key. (Journal, invoice and bill
    posting are idempotent by state instead: re-posting a POSTED document is a
    no-op — accounting/tests/test_posting.py.)"""

    CRITICAL_CREATE_VIEWS = {
        "sales/api/views.py": "CustomerPaymentListCreateView",
        "purchases/api/views.py": "VendorPaymentListCreateView",
    }

    def test_payment_creates_honour_idempotency_keys(self):
        missing = []
        for rel, class_name in self.CRITICAL_CREATE_VIEWS.items():
            tree = _parse(BACKEND / rel)
            classes = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
            view = classes.get(class_name)
            if view is None or "Idempotency-Key" not in ast.get_source_segment((BACKEND / rel).read_text(encoding="utf-8"), view):
                missing.append(f"{rel}::{class_name}")
        self.assertEqual(missing, [])
