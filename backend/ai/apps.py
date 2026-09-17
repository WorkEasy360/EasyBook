from django.apps import AppConfig
from django.db.models.signals import pre_migrate


class AiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ai"
    verbose_name = "AI / Ask Books"

    def ready(self):
        from ai.checks import check_pgvector_extension_installed
        from ai.config import validate_ai_configuration
        from ai.rag import signals  # noqa: F401 — connects archive/quarantine chunk purge receivers

        validate_ai_configuration()
        pre_migrate.connect(check_pgvector_extension_installed, dispatch_uid="ai_check_pgvector_extension")
