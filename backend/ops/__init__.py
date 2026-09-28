"""Operational entry points for one-off deploy tasks (ops.db_bootstrap).

Modules here do not import Django, so they run before an environment is
configured; the application reuses only their pure verification helpers."""
