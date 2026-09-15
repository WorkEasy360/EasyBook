# BACKEND

PURPOSE
Django + DRF modular monolith. One project (`config`), one app per bounded domain module at this directory level (not nested under `apps/`).

OWNS
- `config/` — settings package (base/dev/production/test), URL root, Celery app.
- Cross-app conventions: tenant isolation, error envelope, pagination, auth.

RUNNING LOCALLY
- `infrastructure/docker-compose.yml` provides Postgres 16 (host port 5442) and Redis (host port 6390) — remapped from the defaults because other local projects already hold 5432/6379 on this machine.
- `.venv/` is the project's virtualenv (Python 3.10). Activate or call `.venv/Scripts/python.exe` directly on Windows.
- `manage.py` defaults to `config.settings.dev`; tests use `config.settings.test` (`manage.py test --settings=config.settings.test`).
- The Postgres role Django connects as (`easybook_app`, see `.env`) is deliberately NOT the superuser created by the official Postgres image — superusers always bypass Row Level Security. Never point `DB_USER` at the `easybook` superuser role.

DEPENDENCIES
Django 5.2 LTS, DRF 3.17, psycopg 3, djangorestframework-simplejwt, django-environ, django-cors-headers, celery, redis. Pinned in `requirements.txt`.

INVARIANTS
- See root `CLAUDE.md` global rules — Decimal for money, tenant isolation fail-closed, audit every mutation.
- Every request runs inside one DB transaction (`ATOMIC_REQUESTS = True`) so `SET LOCAL` tenant GUCs (core/CLAUDE.md) stay scoped correctly.

TESTS
`manage.py test --settings=config.settings.test`. Requires the Postgres container running (RLS tests execute real SQL against Postgres, not SQLite — do not switch the test DB engine).

READ FIRST
- `config/settings/base.py`
- `core/tenancy.py`, `core/models.py`, `core/rls.py`
- The specific app's `CLAUDE.md` you're working in

DO NOT READ BY DEFAULT
- `.venv/`, `staticfiles/`, `**/migrations/*` (except when a migration is the actual subject of the change)
