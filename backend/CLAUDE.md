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
Django 5.2 LTS, DRF 3.17, psycopg 3, djangorestframework-simplejwt, django-environ, django-cors-headers, celery, redis, boto3 (S3-compatible document storage — Phase 9, see `documents/CLAUDE.md`; lazy-imported so dev/test never require it at runtime). Pinned in `requirements.txt`.

INVARIANTS
- See root `CLAUDE.md` global rules — Decimal for money, tenant isolation fail-closed, audit every mutation.
- Every request runs inside one DB transaction (`ATOMIC_REQUESTS = True`) so `SET LOCAL` tenant GUCs (core/CLAUDE.md) stay scoped correctly.

QUALITY GATE
`make check` runs what CI runs, in the order that fails fastest: `lint` (ruff)
-> `security` (bandit) -> `audit` (pip-audit) -> `migrations` (drift check)
-> `test`. Config lives in `pyproject.toml`; dev tooling is pinned in
`requirements-dev.txt`, separate from `requirements.txt` so production images
never install a linter. CI is `.github/workflows/backend-ci.yml`, with actions
pinned to commit SHAs rather than tags.

Deliberately NOT adopted yet, each with a reason in `requirements-dev.txt`:
pytest (the suite depends on Django's TransactionTestCase semantics), mypy
(needs django-stubs and an annotation pass — its own slice), semgrep
(overlaps bandit/ruff for this codebase's shape). `ruff format` is configured
but NOT enforced: adopting it reformats ~120 files, which belongs in its own
commit rather than mixed into feature work.

DJANGO 6.0 READINESS
`models.CheckConstraint` takes `condition=`, not `check=`. The `check` keyword
was deprecated in Django 5.1 and is REMOVED in 6.0; all 36 occurrences were
renamed in the Phase 6 slice. The rename is source-only — Django's
`deconstruct()` already emitted `condition`, so no migration was generated and
none is needed. New constraints must use `condition=`; `python -W
error::DeprecationWarning -c "import django; django.setup()"` catches a
regression.

Bandit excludes `tests/` while ruff does not. Skipping B105/B106/B107 globally
to quiet fixture passwords would blind bandit to a real hardcoded credential
in shipping code, which is the one thing it is here for.

TESTS
`manage.py test --settings=config.settings.test`. Requires the Postgres container running (RLS tests execute real SQL against Postgres, not SQLite — do not switch the test DB engine).

MODULE MAP
`core` (tenancy, shared money/enum/recurrence helpers) -> `accounts`/`authz`/`audit` -> `accounting` -> `tax` (GST engine: state master, determination, component split — see `tax/CLAUDE.md`) -> `items`/`inventory` -> `sales`, `purchases` -> `projects`, `banking`, `compliance` (registers, GSTR-1/3B, e-Invoice/e-Way Bill — see `compliance/CLAUDE.md`) -> `reports` (read-only reporting layer over every module above — see `reports/CLAUDE.md`) -> `documents` (tenant-scoped document management + OCR foundation — see `documents/CLAUDE.md`; sits at the same top layer as `reports`, importing lazily across modules only to validate a `DocumentLink`'s target).
Peer modules at the same level (sales and purchases; projects, banking and compliance) must not import each other: anything two of them need is promoted to a lower layer instead (see `core/CLAUDE.md`, and `tax/services/party.py` for the same pattern one layer up — shared between `sales.Customer` and `purchases.Vendor` tax fields). `projects`, `banking` and `compliance` sit above `sales`/`purchases` and may import them; the reverse is forbidden, which is why `purchases.Expense.project` is a string FK reference. `tax` sits below `items` (`Item.tax_rate` FKs into it) and below `sales`/`purchases` and must NEVER import either — it is called, it does not call out.

READ FIRST
- `config/settings/base.py`
- `core/tenancy.py`, `core/models.py`, `core/rls.py`
- The specific app's `CLAUDE.md` you're working in

DO NOT READ BY DEFAULT
- `.venv/`, `staticfiles/`, `**/migrations/*` (except when a migration is the actual subject of the change)
