# SSO

PURPOSE
Workeasy360 sign-in. EasyBook is a Workeasy360 product, so people sign in with their Workeasy360 account (OpenID Connect: authorization code + PKCE S256, `private_key_jwt` client authentication). The protocol is the shared **workeasy360-sso** connector (Django app `workeasy360_sso`, vendored wheel in `backend/vendor/`, version pinned in `requirements.txt` and by hash in `vendor/SHA256SUMS`); this module keeps EasyBook's routes, error codes, provisioning and enforcement rules around it, and maps the result onto EasyBook's own user, organization and membership. Full guide: `docs/workeasy360-sso.md`.

OWNS
- `connector_settings.py` — `settings.WORKEASY360_SSO`: the connector's configuration, derived from the `WORKEASY360_*` settings each time it loads (every key given, so nothing falls through to its environment lookup). `config.py` resets it when a `WORKEASY360_*` setting changes (tests).
- `flow.py` — start and complete through `workeasy360_sso.api`. The BFF is the connector's "browser": the connector's binding value travels in the signed `flow` blob the BFF keeps (state, nonce and verifier stay server-side, single use, 10 minutes); the callback hands it back as the cookie a browser would have sent, on a request EasyBook builds (`_relay`) with a session that is never stored (`UnstoredSession`). Then the usual SimpleJWT pair, naming the connector's session record.
- `errors.py` — `SSOError` and `from_refusal`: every connector refusal mapped back onto the codes the BFF has always been given (frontend `backendErrorCode`).
- `provisioning.py` — the connector's `UserAdapter`/`OrganizationAdapter` (one transaction): user by (issuer, sub), else link by VERIFIED email, else create (no usable password); organization by `Organization.workeasy360_organization_id`, else create (first signer becomes OWNER, others get `WORKEASY360_DEFAULT_ROLE`); membership. Keeps the connector's `FederatedIdentity` mirror in step (`sync_federated_identity`).
- `models.ExternalIdentity` — (issuer, subject) -> user, AUTHORITATIVE. Global like `User`: no organization, no RLS. The connector's `FederatedIdentity` is a mirror (back-filled by migration `0002`, add-only).
- `sessions.py` — `SESSION_CLAIM` (`workeasy360_session`, the connector's `ProductSession` id) on every token of a Workeasy360 sign-in; `Workeasy360JWTAuthentication` (DRF default) and `TokenRefreshSerializer` (SimpleJWT setting) refuse a token whose session record has ended. A token without the claim is plain SimpleJWT.
- `backchannel.py` — EasyBook's part of OpenID Connect Back-Channel Logout (a receiver of `workeasy360_sso.signals.backchannel_logout`): for a `sub`-only token, revoke every refresh token of the person; audit each ended session. The receiver itself (`/api/v1/auth/workeasy360/backchannel-logout/`, verification, `jti` replay, ending records) is the connector's, mounted in `urls.py` with `workeasy360_sso.urls.namespaced()`.
- `entitlement.py` — a thin wrapper over `workeasy360_sso.entitlements`: what is asked (per (organization, `sub`) for a signed-in request when the user has an `ExternalIdentity`, per organization for background work and identity-less users) and what the answer does (403 `workeasy360_access_ended` / `workeasy360_read_only` / `workeasy360_access_unverified` / `workeasy360_sign_in_required`, `details.reason` for `membership_inactive` / `no_seat`). Registered as a `core.access` gate in `apps.SsoConfig.ready`, so `core` never imports `sso`. The cadence, outage, refusal, known-denial and cache rules are the connector's (the same rules as before). Views that only read but answer POST set `entitlement_read = True`. Background work asks through `core.access.background_allowed` (`background_gate`, logs `workeasy360.background_skipped`) - every Celery task is classified in `docs/workeasy360-sso.md`, and a new task that produces a customer effect must call the gate - after checking that it has due work (an idle organization is never asked). The subject lookup's cache calls fail open: a Redis outage must never be a 500.
- `views.py` — `auth/methods/` (public), `auth/workeasy360/start/`, `auth/workeasy360/callback/`, `auth/workeasy360/end-session/`, all called by the BFF. The callback answers a refusal (it does not raise), so the spent pending sign-in is not rolled back with the request.
- `tasks.py` — `purge_workeasy360_records_task` (beat, class C): the connector's housekeeping, EasyBook's way (NOT `w360sso_cleanup`, which would delete every session record: no EasyBook session is a Django session).
- `checks.py` — `sso.E001` (product code), `sso.E002` (the redirect URI is the BFF's callback path; stands in for the silenced `workeasy360_sso.E005`).
- `manage.py link_workeasy360_organization` — staff attach an existing organization before its first Workeasy360 sign-in.
- `manage.py move_workeasy360_issuer --from <previous> [--apply]` — moves `ExternalIdentity` and the connector's mirror (with its session records) to the configured issuer in one transaction; dry run by default; folds duplicates, refuses conflicts, idempotent.

DOES NOT OWN
- The protocol (`workeasy360_sso`): never re-implement discovery, token verification, client assertions or the entitlement HTTP here, and never import its private names outside tests.
- `Organization.workeasy360_organization_id` lives on `accounts.Organization`; `accounts` refuses password login, registration and organization creation when `settings.WORKEASY360_SSO_ONLY` is on (it reads the setting; it never imports `sso`).
- The browser side: `frontend/src/app/api/auth/workeasy360/start`, `frontend/src/app/api/auth/callback/workeasy360` (the registered redirect URI — exact string, do not move it), `frontend/src/lib/auth/workeasy360.ts`.

INVARIANTS
- Identity is (issuer, `sub`), never the email address. Without `email_verified: true` nothing is linked or created (`email_unverified`). Linking retires the account's password and revokes its refresh tokens: EasyBook never verified addresses, so the password may be a squatter's.
- The token's `product_code` must equal `WORKEASY360_PRODUCT_CODE`, and when the launcher named an organization the token must be for it (`wrong_product`, `wrong_organization`).
- One Workeasy360 subject per user per issuer (DB constraint). A second subject for the same account is `account_conflict`.
- `organization_id` is the tenant identity; `organization_name` is a starting label only and is never re-synced.
- An inactive EasyBook membership stays inactive: Workeasy360 granting product access does not override an EasyBook admin's removal.
- `ExternalIdentity` decides; the mirror follows (a disagreeing mirror row is corrected at sign-in, never the other way round). A change of `WORKEASY360_ISSUER` needs `move_workeasy360_issuer` in the same release.
- An ended session record ends its EasyBook session at once (every token naming it is refused). Ended records are kept longer than a refresh token lives (`tasks.keep_ended_sessions`).
- Error codes returned to the BFF are a fixed vocabulary (`SSOError.code`); `detail` goes to logs only.

TESTS
- `tests/fakes.FakeWorkeasy360` — real keys and signatures, no network; reached through `WORKEASY360_HTTP_TRANSPORT` (an httpx `MockTransport`). `tests/test_sign_in.behind_fake_provider` also hands the test the connector's entitlement clock (`Clock`).
- `tests/test_protocol.py` — each ID-token check broken in turn (through the API), PKCE, state, the client assertion, `azp`/`aud`.
- `tests/test_sign_in.py` — start/callback end to end, the flow's binding and single use, linking rules, session records and the mirror, SSO-only refusals, checks, throttles.
- `tests/test_backchannel.py` — `sid` vs `sub`, rotated tokens, older sessions, replay, forged/expired/untyped tokens, the in-flight sign-in of an ended session, failure and retry, the route and the manifest.
- `tests/test_issuer_move.py`, `tests/test_connector_data.py` (back-fill, housekeeping), `tests/test_link_command.py`.
- `tests/test_entitlement.py` — cadence, denial, renewal, read-only, outage (last answer stands, backoff), single asker, unlinked organizations; per-person keys, reasons, outage and refusal per person.
- `tests/test_background.py` — the background gate and each gated task (skip, log, schedule kept, resume once after renewal). `tests/linked.py` puts any test case behind the fake provider.

SECURITY
- The client private key is configuration (`WORKEASY360_PRIVATE_KEY` / `_FILE`), never committed. Workeasy360 stores only the public half.
- Start and callback carry their throttle scopes (`sso_start`, `auth`).
- Production refuses to boot with `WORKEASY360_SSO_ONLY` on and sign-in unconfigured (`config/settings/preflight.py`), with `WORKEASY360_ALLOW_INSECURE_HTTP` on, and (the connector) with an unusable configuration in SSO-only mode.
- The back-channel receiver needs no CSRF token or session: the provider's signature is its authentication.
