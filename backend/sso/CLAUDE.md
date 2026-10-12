# SSO

PURPOSE
Workeasy360 sign-in. EasyBook is a Workeasy360 product, so people sign in with their Workeasy360 account (OpenID Connect: authorization code + PKCE S256, `private_key_jwt` client authentication). This module verifies the result and maps it onto EasyBook's own user, organization and membership. Full guide: `docs/workeasy360-sso.md`.

OWNS
- `oidc.py` — the protocol client. No Django import. Discovery (only the issuer is configured), cached JWK Set, client assertion (RFC 7523), code exchange, and the seven ID-token checks of the Workeasy360 product integration guide.
- `flow.py` — start (authorization URL plus a Django-signed `flow` blob holding state, nonce and verifier, 10 minutes) and complete (verify, provision, issue the usual SimpleJWT pair).
- `provisioning.py` — claims to rows: user by (issuer, sub), else link by VERIFIED email, else create (no usable password); organization by `Organization.workeasy360_organization_id`, else create (first signer becomes OWNER, others get `WORKEASY360_DEFAULT_ROLE`); membership.
- `models.ExternalIdentity` — (issuer, subject) -> user. Global like `User`: no organization, no RLS.
- `entitlement.py` — re-asks Workeasy360's entitlement API, on its `revalidate_after` cadence, whether each linked organization may still use EasyBook; refuses organization-scoped requests once access has ended (`workeasy360_access_ended`) and writes while read-only. Registered as a `core.access` gate in `apps.SsoConfig.ready`, so `core` never imports `sso`. An outage is never a denial; a refusal of EasyBook's own client is (`workeasy360_access_unverified`); a known denial never lapses (no cache expiry, plus a per-process copy). Views that only read but answer POST set `entitlement_read = True`. A signed-in request asks per (organization, `sub`) when the user has an `ExternalIdentity` (also `membership_inactive` / `no_seat`, both shown as `workeasy360_access_ended` with `details.reason`); background work asks per organization through `core.access.background_allowed` (`background_gate`, denial trusted 900 s, logs `workeasy360.background_skipped`) - every Celery task is classified in `docs/workeasy360-sso.md`, and a new task that produces a customer effect must call the gate - after checking that it has due work (an idle organization is never asked). Every shared-cache call in `entitlement.py` goes through the fail-open `_cache_*` wrappers: a Redis outage must never be a 500.
- `views.py` — `auth/methods/` (public), `auth/workeasy360/start/`, `auth/workeasy360/callback/`, all called by the BFF.
- `manage.py link_workeasy360_organization` — staff attach an existing organization before its first Workeasy360 sign-in.

DOES NOT OWN
- `Organization.workeasy360_organization_id` lives on `accounts.Organization`; `accounts` refuses password login, registration and organization creation when `settings.WORKEASY360_SSO_ONLY` is on (it reads the setting; it never imports `sso`).
- The browser side: `frontend/src/app/api/auth/workeasy360/start`, `frontend/src/app/api/auth/callback/workeasy360` (the registered redirect URI — exact string, do not move it), `frontend/src/lib/auth/workeasy360.ts`.

INVARIANTS
- Identity is (issuer, `sub`), never the email address. Without `email_verified: true` nothing is linked or created (`email_unverified`). Linking retires the account's password and revokes its refresh tokens: EasyBook never verified addresses, so the password may be a squatter's.
- The token's `product_code` must equal `WORKEASY360_PRODUCT_CODE`, and when the launcher named an organization the token must be for it (`wrong_product`, `wrong_organization`).
- One Workeasy360 subject per user per issuer (DB constraint). A second subject for the same account is `account_conflict`.
- `organization_id` is the tenant identity; `organization_name` is a starting label only and is never re-synced.
- An inactive EasyBook membership stays inactive: Workeasy360 granting product access does not override an EasyBook admin's removal.
- The algorithm comes from the key set, never the token header; an unknown `kid` refreshes the key set once, then fails.
- Error codes returned to the BFF are a fixed vocabulary (`SSOError.code`); `detail` goes to logs only.

TESTS
- `tests/test_oidc.py` — each ID-token check broken in turn against `tests/fakes.FakeWorkeasy360` (real keys and signatures, no network).
- `tests/test_sign_in.py` — start/callback end to end, linking rules, SSO-only refusals.
- `tests/test_link_command.py`.
- `tests/test_entitlement.py` — cadence, denial, renewal, read-only, outage (last answer stands, backoff), single asker, unlinked organizations; per-person keys, reasons, outage and refusal per person.
- `tests/test_background.py` — the background gate and each gated task (skip, log, schedule kept, resume once after renewal). `tests/linked.py` puts any test case behind the fake provider.

SECURITY
- The client private key is configuration (`WORKEASY360_PRIVATE_KEY` / `_FILE`), never committed. Workeasy360 stores only the public half.
- Start and callback carry `throttle_scope = "auth"`.
- Production refuses to boot with `WORKEASY360_SSO_ONLY` on and sign-in unconfigured (`config/settings/preflight.py`).
