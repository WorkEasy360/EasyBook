"""Concurrency limit for the API's ASGI worker processes.

Separate from config/asgi_worker.py because that module imports gunicorn,
which cannot import on Windows (fcntl) — this part stays testable anywhere.
"""

import os

DEFAULT_ASGI_LIMIT_CONCURRENCY = 10


def asgi_limit_concurrency() -> int:
    raw = os.environ.get("ASGI_LIMIT_CONCURRENCY", str(DEFAULT_ASGI_LIMIT_CONCURRENCY))
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value < 1:
        raise RuntimeError(f"ASGI_LIMIT_CONCURRENCY must be a positive integer, got {raw!r}.")
    return value
