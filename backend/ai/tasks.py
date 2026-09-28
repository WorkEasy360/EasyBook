"""Celery's Django autodiscovery (config/celery.py's `app.autodiscover_tasks()`)
only imports `<app>/tasks.py` for each top-level INSTALLED_APPS entry — it
never recurses into `ai/rag/`, where the actual AI Celery tasks are defined.
Without this module a real `celery -A config worker` never imports
`ai.rag.tasks` at all, so `@shared_task` never registers `index_document_task`
or `purge_expired_ai_data` with the app, and a message for either task is
silently rejected as "unregistered" — `ai.rag.tasks.purge_expired_ai_data` in
particular would then never actually purge anything despite being wired into
CELERY_BEAT_SCHEDULE (config/settings/base.py). Confirmed missing by
inspecting a real worker's task list after building the production Docker
image — not caught by the test suite, since CELERY_TASK_ALWAYS_EAGER=True in
config/settings/test.py runs tasks in-process without needing them registered,
and importing ai.rag.tasks directly (as tests do) registers it regardless of
whether autodiscovery would ever have found it on its own.

Re-exporting rather than moving the tasks: the functions keep their original
`ai.rag.tasks.*` registered names (Celery names a task after its definition
site, not where it's later imported), so every existing reference — including
CELERY_BEAT_SCHEDULE's "ai.rag.tasks.purge_expired_ai_data" — stays correct.
"""

from ai.rag.tasks import index_document_task, purge_expired_ai_data  # noqa: F401
