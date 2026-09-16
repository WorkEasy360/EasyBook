from django.apps import AppConfig


class AiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ai"
    verbose_name = "AI / Ask Books"

    def ready(self):
        from ai.config import validate_ai_configuration
        from ai.rag import signals  # noqa: F401 — connects archive/quarantine chunk purge receivers

        validate_ai_configuration()
