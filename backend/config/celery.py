import logging
import os

from celery import Celery
from celery.signals import beat_init, task_postrun, task_prerun, worker_init

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("easybook")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


# Each task runs with its own correlation context: task_id/task_name on every
# log line, and the task id standing in as the request_id (so audit rows the
# task writes are correlated too). Tokens are kept per task id and reset after
# the task, so a worker thread never carries one task's context into the next.
_task_context_tokens = {}


@task_prerun.connect
def bind_task_context(task_id=None, task=None, **_kwargs):
    from core.request_context import bind_task

    _task_context_tokens[task_id] = bind_task(task_id, getattr(task, "name", None))


@task_postrun.connect
def clear_task_context(task_id=None, **_kwargs):
    from core.request_context import reset_task

    if task_id is None and _task_context_tokens:
        task_id = next(reversed(_task_context_tokens))
    tokens = _task_context_tokens.pop(task_id, None)
    if tokens is not None:
        reset_task(tokens)


@worker_init.connect
@beat_init.connect
def refuse_unrestricted_db_role(**_kwargs):
    """Same boot-time guard as config/asgi.py, for Celery workers and beat.

    Celery logs and SWALLOWS exceptions raised by signal handlers, so raising
    here would leave a worker running as a role that bypasses RLS (observed
    against the built image). The process therefore exits itself.
    """
    from django.conf import settings

    if not settings.DB_ROLE_PREFLIGHT:
        return
    from django.core.exceptions import ImproperlyConfigured

    from core.db_preflight import assert_runtime_db_role_is_restricted

    try:
        assert_runtime_db_role_is_restricted()
    except ImproperlyConfigured as exc:
        logger = logging.getLogger("easybook.preflight")
        logger.critical("db_role_preflight_failed", extra={"error": str(exc)})
        for handler in logging.getLogger().handlers + logger.handlers:
            handler.flush()
        os._exit(1)
