# CORE

PURPOSE
Cross-cutting foundation every other app depends on: tenant isolation mechanism, base model classes, REST conventions (pagination/error envelope), request-scoped middleware, health checks, idempotency foundation. Owns no business domain.

OWNS
- `tenancy.py` — the ONLY place that reads/writes the current-organization/current-user context. Contextvar for app-level scoping + PostgreSQL session GUCs (`app.current_organization_id`, `app.current_user_id`) for RLS.
- `models.py` — `TimeStampedModel`, `TenantScopedModel` (abstract bases; every org-owned model inherits `TenantScopedModel`).
- `managers.py` — `TenantManager`: fails closed (`.none()`) when no tenant context is set.
- `rls.py` — helpers for writing RLS-enabling migrations (`enable_rls_org_scoped`, `enable_rls_self_or_org_scoped`).
- `views.py` — `OrganizationScopedMixin` (resolves + enforces `X-Organization-Id`), `AuthenticatedAPIView`, `HealthCheckView` (readiness: DB+cache), `LivenessCheckView` (no dependencies, `throttle_classes = []` — see SECURITY).
- `exceptions.py`, `pagination.py`, `logging.py`, `middleware.py`, `idempotency.py`.
- `throttling.py` — `FailOpen{Scoped,User,Anon}RateThrottle`, the `DEFAULT_THROTTLE_CLASSES` (`config/settings/base.py`). Wrap DRF's own throttle classes to catch `redis.exceptions.RedisError` and allow the request through rather than 500 it — Django's built-in Redis cache backend does not swallow connection errors on its own.
- `money.py` — `calculate_line`/`calculate_document_totals`, the ONE rounding policy every priced document in every module uses. `enums.py` — `PaymentMethod`, `RecurringFrequency`. `recurrence.py` — `advance_occurrence`. All three arrived here from `sales` when `purchases` needed them: a peer-to-peer purchases->sales import would couple two modules at the same layer, and a second copy would drift. Only promote something to `core` once a SECOND module genuinely needs it.

DOES NOT OWN
- Organization/User/Membership models (accounts).
- Role/permission catalog (authz).
- What gets audited and when (audit) — core only provides `TenantScopedModel`; audit owns `AuditLog`.

DEPENDENCIES
- `accounts.Organization` (FK target in `TenantScopedModel`) — this is the one deliberate circular-ish coupling; keep it to that single FK.

INVARIANTS
- `TenantManager` (the `objects` manager on every `TenantScopedModel`) must NEVER return cross-tenant rows, and must return an EMPTY queryset (not all rows) when no tenant is set.
- Every table backing a `TenantScopedModel` subclass MUST have a matching RLS migration (`enable_rls_org_scoped`/`enable_rls_self_or_org_scoped`) — the manager filter is defense in depth, not the primary control. A new `TenantScopedModel` without an RLS migration is a bug.
- `SET LOCAL` (used for the RLS GUCs) only survives inside an open transaction. `tenant_context()` opens its own `transaction.atomic()` for exactly this reason — do not "simplify" it to skip that in the name of avoiding nested transactions.
- `core.rls` policies use `FORCE ROW LEVEL SECURITY`, but that still does NOT protect against a Postgres superuser — the app's runtime DB role must never be a superuser (see `backend/CLAUDE.md`).

TESTS
Any change to `tenancy.py`, `managers.py`, or `rls.py` requires rerunning `core/tests/test_tenant_isolation.py` — it is the acceptance gate proving Org A cannot read/write Org B's data at both the app and database layers, and that the system fails closed with no tenant context.

SECURITY
- `OrganizationScopedMixin.initial()` must always resolve membership and set both the contextvar and the Postgres GUC BEFORE any view body runs.
- Never add a code path that sets the organization GUC from anything other than a verified, active `Membership` row.
- Rate limiting fails OPEN on a cache-backend outage (`throttling.py`), unlike tenant isolation/auth which must fail closed — a protective, non-correctness-critical layer degrading during a Redis outage must never take the whole API down with it (phase 12 security review, 2026-09-16; see `infrastructure/runbooks/redis-celery-outage.md`).

READ FIRST
- `tenancy.py`, `models.py`, `managers.py`, `rls.py`, `views.py`

DO NOT READ BY DEFAULT
- `migrations/` unless the migration itself is in question

TOKEN DISCIPLINE
- This module is small; read the file you need directly rather than globbing the whole app.
