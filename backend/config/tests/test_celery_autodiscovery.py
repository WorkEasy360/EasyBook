"""Celery's Django autodiscovery (config/celery.py's `app.autodiscover_tasks()`)
only imports `<app>/tasks.py` for each top-level INSTALLED_APPS entry — a task
module nested deeper (ai/rag/tasks.py) is silently never found by a real
`celery -A config worker`, even though CELERY_BEAT_SCHEDULE references it by
name and it runs fine in-process under CELERY_TASK_ALWAYS_EAGER=True (tests)
or when some other module happens to import it directly first.

This can only be tested honestly via a clean subprocess that mimics what a
freshly started worker does (django.setup() + autodiscover_tasks(force=True))
before anything else has had a chance to import the task module — a
same-process test would pass regardless of whether autodiscovery itself
works, since Celery registers a @shared_task the instant its module is
imported by anything, autodiscovery or not.
"""

import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent

# Every task referenced by name in CELERY_BEAT_SCHEDULE (config/settings/base.py)
# must be reachable by autodiscover_tasks() alone, or Beat will schedule work
# a real worker rejects as unregistered.
_SCRIPT = """
import django
django.setup()
from django.conf import settings
from config.celery import app
app.autodiscover_tasks(force=True)
registered = set(app.tasks.keys())
required = {entry["task"] for entry in settings.CELERY_BEAT_SCHEDULE.values()}
missing = required - registered
print("MISSING:" + ",".join(sorted(missing)) if missing else "OK")
"""


class CeleryAutodiscoveryTests(SimpleTestCase):
    def test_every_beat_scheduled_task_is_autodiscoverable_by_a_fresh_worker(self):
        result = subprocess.run(  # noqa: S603 — fixed interpreter path and hardcoded script constant, no untrusted input
            [sys.executable, "-c", _SCRIPT],
            cwd=BACKEND_DIR,
            env={**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings.test"},
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertIn("OK", result.stdout, f"stdout={result.stdout!r} stderr={result.stderr}")
