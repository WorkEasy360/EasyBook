"""Gunicorn worker class for the API container (backend/Dockerfile CMD).

`uvicorn_worker.UvicornWorker` never passes uvicorn a concurrency limit, so a
single API process accepted as many simultaneous requests as clients sent.
Under ASGI each in-flight request's sync code runs in its own thread with its
own database connection, so that also meant unbounded PostgreSQL connections.
Past the limit uvicorn answers 503 at once instead of opening another one.
uvicorn counts open connections (idle keep-alive included) as well as running
tasks against it (uvicorn/protocols/http/h11_impl.py).

Connection budget at the defaults (infrastructure/terraform/variables.tf):
api desired_count 2 x gunicorn --workers 4 x ASGI_LIMIT_CONCURRENCY 10 = 80
connections, 160 while a rolling deploy runs old and new tasks side by side
(deployment_maximum_percent 200), plus one per Celery worker process
(2x4 + 2x4 + 1x2 + 1x2 = 20, doubled during a deploy) and beat. Size
ASGI_LIMIT_CONCURRENCY and task counts against the RDS instance's
max_connections, and the rds_connections alarm with them.
"""

from uvicorn_worker import UvicornWorker

from config.asgi_limits import asgi_limit_concurrency


class BoundedUvicornWorker(UvicornWorker):
    CONFIG_KWARGS = {**UvicornWorker.CONFIG_KWARGS, "limit_concurrency": asgi_limit_concurrency()}
