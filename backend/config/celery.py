import os

from celery import Celery
from celery.signals import task_postrun, task_prerun

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
