# Secret rotation

## Django SECRET_KEY (`terraform/secrets.tf`'s `django_secret_key`)

Rotating this invalidates every active session/signed cookie AND every
issued JWT access/refresh token: `config/settings/base.py`'s `SIMPLE_JWT`
dict does not set `SIGNING_KEY`, so djangorestframework-simplejwt falls back
to its own default of `settings.SECRET_KEY` (confirmed against the installed
package's `settings.py`, not assumed). Plan for every logged-in user needing
to re-authenticate — this is not a rotation to do casually or on a whim.

1. Generate a new value, update the Secrets Manager secret (this is exactly
   the `ignore_changes = [secret_string]` lifecycle block in `secrets.tf` —
   Terraform will not fight a manual rotation here).
2. Force a new ECS deployment for every service in `var.ecs_services` (all
   six read this secret at container start — `ecs.tf`'s `secrets` block) so
   every running task picks up the new value. A rolling deploy means a brief
   window where some tasks have the old key and some have the new one;
   sessions signed by whichever task handled the request stay valid only
   against that same key, so expect scattered 401s during the rollover, not after.

## App DB role password (`terraform/secrets.tf`'s `app_db_credentials`)

1. Generate a new password, `ALTER ROLE easybook_app WITH PASSWORD '<new>';`
   against the real RDS instance (needs the same VPC network access as
   `database-restore.md`'s procedure).
2. Update the Secrets Manager secret's `password` field to match.
3. Force a new ECS deployment for every service (all six connect to
   Postgres). Old connections using the previous password keep working until
   they're recycled/closed — `CONN_MAX_AGE` (default 60s,
   `config/settings/base.py`) bounds how long that can persist.

## RDS master password

AWS-managed (`manage_master_user_password = true`, `terraform/rds.tf`) — AWS
rotates this on its own schedule via Secrets Manager's native rotation, or
trigger it manually from the RDS console. The application never uses this
credential (global rule 4) so rotating it has zero application impact.

## AI / email / webhook-signing secrets (`terraform/secrets.tf`'s empty containers)

Update the Secrets Manager value directly (out-of-band, same as their
initial population — see that file's header comment), then force a new ECS
deployment for whichever services actually reference that secret in their
task definition's `secrets` block once wired up (none are wired into
`ecs.tf` yet — see its own comment on why).

## After any rotation

Confirm via `/api/v1/health/` (or a real authenticated request, for the DB
password) rather than assuming success from the deploy completing —
`config/settings/preflight.py` will crash-loop the `api` service loudly if
the new value is malformed, but a wrong-but-well-formed value (e.g. a typo'd
DB password) fails at connection time, not at startup preflight time.
