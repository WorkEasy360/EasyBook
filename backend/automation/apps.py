from django.apps import AppConfig


class AutomationConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'automation'

    def ready(self):
        from automation import receivers  # noqa: F401 -- connects domain event receivers
