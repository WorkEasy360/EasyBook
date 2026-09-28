"""CELERY_BEAT_SCHEDULE (config/settings/base.py) is a dict of plain strings —
nothing stops a typo'd task path or a renamed task function from silently
turning into a periodic job that Celery Beat schedules but can never run.
Resolve each entry directly against the real task object rather than relying
on Celery's lazy, Django-signal-driven autodiscovery timing.
"""

import importlib

from django.conf import settings
from django.test import SimpleTestCase


class CeleryBeatScheduleTests(SimpleTestCase):
    def test_every_scheduled_task_path_resolves_to_a_real_task(self):
        for beat_name, entry in settings.CELERY_BEAT_SCHEDULE.items():
            task_path = entry["task"]
            module_path, _, task_name = task_path.rpartition(".")
            module = importlib.import_module(module_path)
            task = getattr(module, task_name, None)
            self.assertIsNotNone(task, f"{beat_name}: {task_path!r} does not exist")
            self.assertEqual(task.name, task_path, f"{beat_name}: registered task name mismatch")

    def test_every_schedule_interval_is_positive(self):
        for beat_name, entry in settings.CELERY_BEAT_SCHEDULE.items():
            self.assertGreater(entry["schedule"].total_seconds(), 0, beat_name)
