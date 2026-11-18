# Sign in with Workeasy360

**Written for: whoever deploys or operates EasyBook.**

EasyBook is a Workeasy360 product. People sign in with the same email and password they use for Workeasy360; EasyBook never sees that password. With `WORKEASY360_SSO_ONLY` on (the default outside local development), it is the only way in: EasyBook's own password sign-in, self-registration and "create an organization" are switched off, because people, plans and organizations are managed in Workeasy360.

The OpenID Connect protocol itself is the shared **workeasy360-sso** connector (Django app `workeasy360_sso`, version 0.3.1). It lives in a **private** repository and is installed from there, never committed here: see "Installing the connector (a private repository)". EasyBook keeps its own routes, error codes, provisioning and enforcement rules around it. See "The workeasy360-sso connector and back-channel logout" below.

## How a sign-in works

1. The user clicks **Continue with Workeasy360** on `/login`, or opens EasyBook from the Workeasy360 app launcher (`https://book.workeasy360.com/?organization=<uuid>`).
2. The BFF route `/api/auth/workeasy360/start` asks Django for an authorization URL. Django (the connector) generates state, nonce and a PKCE verifier and keeps them server-side as a single-use pending sign-in, valid for 10 minutes and bound to a random value; it returns that value sealed in a signed `flow` blob. The BFF keeps the blob in an httpOnly cookie, `__Host-eb_sso` (see "Cookie names" below), valid for 10 minutes, then redirects the browser to Workeasy360.
3. The user signs in at Workeasy360, including any second factor. If they came from the launcher, Workeasy360 already knows the organization; otherwise it shows its organization chooser.
4. Workeasy360 checks the membership, that the organization has EasyBook (trial or paid), and the seat. It then redirects to `/api/auth/callback/workeasy360?code=…&state=…`.
5. The BFF relays the code, the state and the kept flow to Django. Django:
   - spends the pending sign-in that state names, first, whatever happens next. It must be bound to the value in this flow: another browser's flow, a replayed callback or one older than 10 minutes is refused (`state_mismatch` / `flow_expired`);
   - exchanges the code, authenticating with a client assertion signed by EasyBook's private key;
   - verifies the ID token offline;
   - provisions the user, organization and membership (one transaction: a refusal leaves no new account behind);
   - records the sign-in for back-channel logout and returns the usual access/refresh pair, which names that record. The BFF stores it exactly as a password sign-in does.

**Provisioning rules** (`backend/sso/provisioning.py`):

| Situation | Result |
|---|---|
| Known Workeasy360 account (issuer + `sub`) | Same EasyBook user |
| Address **not** verified at Workeasy360 | Refused (`email_unverified`): it neither links an existing account nor creates one |
| New Workeasy360 account; an EasyBook user has the same verified address | Linked to that user. EasyBook never verified addresses, so a password on that account might have been set by someone who pre-registered it. The password is therefore removed and every refresh token it issued is revoked. From then on the account is reached through Workeasy360 only |
| New verified address | New user with no usable password |
| Workeasy360 organization not seen before | New EasyBook organization named from `organization_name`, currency `WORKEASY360_DEFAULT_CURRENCY`. The person signing in becomes **owner** and goes through fiscal-year onboarding |
| Organization already known | Joined with `WORKEASY360_DEFAULT_ROLE` (default `staff`). Owners and admins manage roles in EasyBook |
| EasyBook membership was deactivated | Refused (`membership_inactive`) |
| Token for another product (`product_code` ≠ `WORKEASY360_PRODUCT_CODE`, default `easybook`) | Refused (`wrong_product`) |
| Opened from the launcher for organization A, but the token names organization B | Refused (`wrong_organization`) |

An existing EasyBook organization whose books already live here should be attached before its first Workeasy360 sign-in. Otherwise that sign-in creates a new, empty organization:

```
manage.py link_workeasy360_organization <easybook-organization-id> <workeasy360-organization-id>
```

## Registering EasyBook with Workeasy360 (once per environment)

1. Generate EasyBook's key pair. Keep the private key secret and outside the repository:
   ```
   openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out easybook-w360.key
   openssl pkey -in easybook-w360.key -pubout -out easybook-w360.pub
   ```
2. In the Workeasy360 Super Admin → Products → EasyBook integration:
   - Set the redirect URI to exactly `https://book.workeasy360.com/api/auth/callback/workeasy360`. Matching is by exact string, so a trailing slash breaks it. (`manage.py check` refuses any other path: `sso.E002`.)
   - Set the back-channel logout URI to `https://book.workeasy360.com/api/v1/auth/workeasy360/backchannel-logout/`, once this release is live (see "Rolling out the connector release"). `python manage.py w360sso_manifest --login-url https://book.workeasy360.com/login` prints both URIs from the running configuration.
   - Set the scopes to `openid email profile organization`.
   - Set client authentication to `private_key_jwt`.
   - Add a public-key credential from `easybook-w360.pub`. Workeasy360 returns its credential id.
   - Activate the integration.
3. Set the environment:

| Variable | Value |
|---|---|
| `WORKEASY360_ISSUER` | The Workeasy360 issuer, exactly as its discovery document names it (no trailing slash). Production: `https://accounts.workeasy360.com` (WorkEasy360 Accounts). Changing it needs `manage.py move_workeasy360_issuer` in the same release (see "Moving to WorkEasy360 Accounts") |
| `WORKEASY360_CLIENT_ID` | The integration's client id |
| `WORKEASY360_REDIRECT_URI` | The registered redirect URI |
| `WORKEASY360_KEY_ID` | The credential id from step 2 |
| `WORKEASY360_PRIVATE_KEY_FILE` (or `WORKEASY360_PRIVATE_KEY`) | The private key. Inline PEM may use literal `\n` |
| `WORKEASY360_SSO_ONLY` | `1` (the default). Production refuses to start if it is on while any value above is missing |
| `WORKEASY360_PRODUCT_CODE` | Optional, default `easybook`: the product code a token must name. **Must not be empty**: production refuses to start, and so does `manage.py check` in every environment (`sso.E001`), because the product binding check would otherwise have nothing to compare with. The sign-in itself also fails closed (`wrong_product`) when it is empty |
| `THROTTLE_SSO_START` | Optional, default `60/min` per client address: the rate limit of the sign-in **start** endpoint (its own scope, `sso_start`). The callback and password sign-in keep `THROTTLE_AUTH` (`20/min`) |
| `WORKEASY360_DEFAULT_ROLE` | Optional. Role for people joining an existing organization (default `staff`) |
| `WORKEASY360_DEFAULT_CURRENCY` | Optional. Currency of a newly provisioned organization (default `INR`) |
| `WORKEASY360_BACKCHANNEL_LOGOUT` | Optional, default on: serves the back-channel logout receiver. Off: it answers `404` |
| `WORKEASY360_ALLOW_INSECURE_HTTP` | Local development only (on in `dev.py`): an `http://` issuer and redirect URI on a loopback host. Production refuses to start with it |

To rotate the key, add a second credential in Workeasy360, deploy the new key and `WORKEASY360_KEY_ID`, confirm sign-in works, then revoke the old credential.

## Local development

The backend needs the connector installed from its private repository first (`make install`; see the next section).

`dev.py` leaves `WORKEASY360_SSO_ONLY` off, so password sign-in keeps working with no Workeasy360 running. To rehearse the real flow, register a development integration whose redirect URI is `http://localhost:3000/api/auth/callback/workeasy360` (Workeasy360 accepts `http://localhost` outside production), fill in the `WORKEASY360_*` values in `backend/.env`, and set `WORKEASY360_SSO_ONLY=1`. `dev.py` turns `WORKEASY360_ALLOW_INSECURE_HTTP` on, which the connector needs for an `http://localhost` issuer or redirect URI.

## Installing the connector (a private repository)

`workeasy360-sso` lives in **WorkEasy360/workeasy360-sso, a private repository**. EasyBook's repository is public, so nothing of the connector may be committed here: no wheel, no copy of its code, and no key or token to fetch it with. `backend/requirements.txt` names it by commit:

```
workeasy360-sso @ git+ssh://git@github.com/WorkEasy360/workeasy360-sso.git@a98552f8f37e1e4f1fab1de99cc5b915cb9ed5c1
```

That commit is the connector's tag `v0.3.1`. The pin is the full commit SHA, never the tag: a tag can be moved, a commit cannot, and CI refuses any other form (`backend-ci.yml`, "The connector is pinned to a full commit SHA"). Installing it needs read access to that repository, over SSH.

**On your machine** (you need read access to WorkEasy360/workeasy360-sso; the owner grants it):

1. Have an SSH key on your GitHub account, and check it: `ssh -T git@github.com` answers "Hi <you>! You've successfully authenticated". On Windows, pip runs Git for Windows, which uses its own OpenSSH and `%USERPROFILE%\.ssh`.
2. In `backend/`, with the virtualenv: `make install` (pip >= 25.3, for `--build-constraint`: `.venv/Scripts/python.exe -m pip install --upgrade pip` first). It runs exactly what CI and the Dockerfile run:
   ```
   .venv/Scripts/python.exe -m pip install --no-deps --build-constraint requirements-build.txt -r requirements.txt
   .venv/Scripts/python.exe -m pip check
   ```
   pip clones the pinned commit, builds the connector's wheel with the hash-pinned build backend in `requirements-build.txt`, and installs it with every other pin.
3. If you sign in to GitHub over HTTPS (GitHub CLI, `gh auth setup-git`) rather than with an SSH key, point git at HTTPS for this one repository instead: `git config --global url."https://github.com/WorkEasy360/workeasy360-sso".insteadOf "ssh://git@github.com/WorkEasy360/workeasy360-sso"`.

Without access everything else installs and the connector does not, so Django does not start (`workeasy360_sso` is in `INSTALLED_APPS`). That is deliberate: there is no public copy to fall back to.

**The image.** From `backend/`: `docker build --ssh default -t easybook-backend .`, with an SSH agent that holds a key with access (`--ssh default` forwards the agent named by `SSH_AUTH_SOCK`; `--ssh default=<path to an unencrypted private key>` passes a key file instead). BuildKit mounts the agent's socket into the one `RUN` that installs the connector, and nowhere else: no key, token or `.git` reaches any layer, and git and the SSH client exist only in the builder stage. Without `--ssh` the build stops at that step. The built image does contain the connector's code (`/opt/venv/.../workeasy360_sso`), as it must to run: keep the image in a private registry (ECR), never a public one.

**CI** (`.github/workflows/backend-ci.yml`). The `test`, `container` and `publish` jobs need the Actions secret **`WORKEASY360_SSO_DEPLOY_KEY`**: the private half of a **read-only deploy key** of WorkEasy360/workeasy360-sso. The local action `.github/actions/workeasy360-sso-ssh` loads it into an ssh-agent from the environment (it is never written to disk), trusts only github.com's published host keys (`backend/github_known_hosts`, `StrictHostKeyChecking=yes`), checks that the key can read the repository, and stops with an error naming the secret when it is missing or unusable. The key is unloaded as soon as the install (or the image build) is done, before any third-party action runs. The test job keeps no pip cache: pip caches the wheel it builds from the pinned commit, and the Actions cache of a public repository can be restored by a pull request from a fork. Pull requests from forks get no secrets at all, so their backend tests stop at that step (the `static` job still runs).

To create the key and the secret (the owner, once; never commit either half):

```
ssh-keygen -t ed25519 -N "" -C "EasyBook CI, read-only" -f easybook-ci-workeasy360-sso
gh repo deploy-key add easybook-ci-workeasy360-sso.pub -R WorkEasy360/workeasy360-sso --title "EasyBook CI (read-only)"
gh secret set WORKEASY360_SSO_DEPLOY_KEY -R WorkEasy360/EasyBook < easybook-ci-workeasy360-sso
rm easybook-ci-workeasy360-sso easybook-ci-workeasy360-sso.pub
```

(The same in the web interface: the connector repository's Settings, Deploy keys, without "Allow write access"; then EasyBook's Settings, Secrets and variables, Actions.) If Dependabot is ever enabled, its pull requests read Dependabot secrets: add the same secret there.

**Integrity, with no wheel committed.**

- The connector's code is fixed by the commit SHA: git checks every object it fetches against it. It is the same code as the 0.3.1 wheel this repository used to carry (compared file by file: identical, apart from Windows line endings in five files of that wheel).
- Its build: pip builds the wheel in an isolated environment, with `hatchling` and its dependencies pinned by version and SHA-256 in `requirements-build.txt` (`--build-constraint`), not with whatever PyPI has that day.
- Everything else: `requirements.txt` pins every other package to an exact version. Those pins carry no hashes (they never did; the vendored wheel was the only file checked by hash). Installing with `--no-deps` and then `pip check` means exactly the pinned tree is installed, and that it is complete.
- Audit: `pip-audit -r requirements.txt --no-deps --disable-pip` (CI's `static` job, `make audit`) audits every pin without installing anything, so it needs no access to the connector. It skips the connector's git line, as before (it is on no index and in no advisory database); its dependencies are pinned, and audited, in `requirements.txt`.
- Hash-pinning all of `requirements.txt` later (`--require-hashes`) would need the connector's line in a file of its own, installed by a second pip command: pip's hash-checking mode covers a whole install and refuses a git requirement, which has no file to hash.

**Moving to another connector release.** Tag it in the connector repository. Find the commit: `gh api repos/WorkEasy360/workeasy360-sso/git/ref/tags/vX.Y.Z` gives an object; when its `type` is `tag` (an annotated tag), `gh api repos/WorkEasy360/workeasy360-sso/git/tags/<that sha>` gives the commit as `object.sha`. Put that full SHA in `requirements.txt`, update the version named in this guide, then `make install` and the full suite.

## After sign-in: the organization's access is re-checked (`sso/entitlement.py`)

Workeasy360 checks the trial or plan at sign-in. EasyBook's own session then lives on (its refresh token rotates), so EasyBook also asks Workeasy360's entitlement API, as the integration guide requires, whether each organization linked to Workeasy360 may still use EasyBook:

- One answer per question, shared through the cache, for Workeasy360's `revalidate_after` (900 s). A denial is asked again after 60 s, so a renewal takes effect quickly. The call authenticates with EasyBook's own `private_key_jwt` credential; there is no product parameter. The question, its cache and these rules are the connector's (`workeasy360_sso.entitlements`); `sso/entitlement.py` decides what is asked and what the answer does here.
- **Per person for a signed-in request.** When the user has an `ExternalIdentity` for the configured issuer, the question is (Workeasy360 organization, the `sub` they signed in with): the form carries `subject`, and Workeasy360 can also answer `deny` with `membership_inactive` (account disabled or locked by an administrator, membership suspended or removed) or `no_seat` (a per-seat product whose seat was unassigned). The cache key (the connector's, per organization and subject), the per-process copy, the single flight, the back-off and the 60 s denial re-check are all per (organization, person), so one person's denial never touches a colleague. A user with no Workeasy360 identity (a password account in local development) is asked per organization, as before. The subject lookup is cached for 5 minutes, so a request costs no extra query; only a found subject is cached ("no identity" is looked up each time, so it can never hide an identity created a moment later), and the entry is also dropped after commit when a sign-in links a new identity.
- **Per organization for background work** (no person): see "Background jobs" below.
- Every organization-scoped request (`core.access` gate, registered by `SsoConfig.ready` so `core` never imports `sso`) is refused with `403 workeasy360_access_ended` once the answer is `deny` - a trial that ran out, a lapsed or cancelled subscription - and every write with `workeasy360_read_only` while `write_allowed` is false. `auth/me/` and the organization list still answer, so the frontend sends every page to **/access-ended** ("EasyBook access has ended": Try again, Sign out). When the reason is the person's (`membership_inactive`, `no_seat`) the error's `details` carries `{"reason": ...}` and the page says "Your access has been removed" or "You no longer have an EasyBook seat" instead of blaming the organization (`/access-ended?reason=membership_inactive|no_seat`; an unknown reason falls back to the organization wording). A person who belongs to other organizations also gets a **Switch organization** list there (the organization list is not organization-scoped, so it still answers), which uses the same `/api/auth/organization` route as the app's switcher and lands on the dashboard with a full page load.
- A Workeasy360 outage (5xx, 429, timeout, an unreadable key) is never a denial: the last answer stands, with none access continues, and after a failure the next attempt waits 60 s. One request asks while the others use the last answer; a first check is never waved through because another request is asking.
- A refusal is not an outage: when Workeasy360 refuses EasyBook's own client (400/401/403 - a revoked credential, a suspended integration) every organization-scoped request is refused with `workeasy360_access_unverified` ("EasyBook can't confirm your access", `/access-ended?reason=unverified`) and it is logged as an error. A refusal is about the **client**, so it closes the whole client for 60 s, kept in the shared cache and in each process (so it survives a Redis outage): every question - any person, any organization, background work - fails closed without being asked, even if it held a fresh `allow`. After 60 s a question is asked again. Workeasy360 answers 429 once a client has failed to authenticate about ten times a minute; a 429 that arrives within 5 minutes of a refusal is that limiter and counts as a refusal (otherwise most people would never learn of a revocation); a 429 with no recent refusal is an outage as before.
- **Sessions from before SSO-only.** With `WORKEASY360_SSO_ONLY` on, a user with no Workeasy360 identity (a session, i.e. a refresh token, opened before SSO-only was switched on) in an organization linked to Workeasy360 is refused with 403 `workeasy360_sign_in_required`: with no `sub` there is nothing to check per person, so such a session would survive the person's removal in Workeasy360 for as long as its refresh token keeps rotating. The frontend sends them to **/access-ended?reason=sign_in_required** ("Sign in with Workeasy360", button **Continue with Workeasy360**); signing in links the account by verified e-mail as always. `auth/me/`, the organization list and sign-out stay reachable. EasyBook-only organizations are unaffected, and with SSO-only off (local development) the organization is asked, as before.
- A known denial never lapses into access: it is cached without expiry, and each process keeps its own copy of the last answers, so a Redis outage or eviction cannot hand access back.
- **A Redis outage is never a 500.** Every cache operation on this path fails open: a read is a miss, a write or delete is a no-op, the single-flight lock says "unknown" (so the request asks). The per-process copy stands in for the last answer and the back-off; with nothing known the provider is asked, at the cost of one extra question and one subject query per request until Redis is back. Background jobs the same: the gate never raises.
- Reads made with POST stay allowed while read-only: views say so with `entitlement_read = True` (document download, Ask Books).
- An EasyBook-only organization (not linked to Workeasy360) is never checked.

The worst case after access ends is one window (15 minutes), not the life of the session - for every cause: a trial that ran out, a lapsed subscription, a suspended organization, a removed membership, a disabled account, an unassigned seat.

## Background jobs (`core.access.background_allowed`)

A job that produces a customer business effect asks, **before** the effect, whether the organization may still have it. The check is the organization-level answer (no subject), through `sso/entitlement.py`, registered from `SsoConfig.ready` like the request gate so `core` never imports `sso`:

- `allow` **and** `write_allowed` -> run. Anything else (a denial, a refusal of EasyBook's own client, or a read-only subscription) -> **skip**: log `workeasy360.background_skipped` (organization id, task, reason), do not raise, do not retry in a loop, and leave the schedule or queue state exactly as it was so the work resumes after a renewal.
- A denial is trusted for **900 s** for background use (not 60 s), so an expired organization's jobs cost one question per window, not one a minute. A person's request still re-asks after 60 s. A *refusal* of EasyBook's own client is not a denial of the organization: it is re-asked every 60 s for background work too.
- **Only an organization with due work is asked.** Each sweep looks for due work first (an undispatched event, a due schedule, a matching scan, a stalled execution or document, a template due and not past its end date, a document marked as skipped) and asks Workeasy360 only for organizations that have some. An expired organization with nothing due costs no question at all, and during a Workeasy360 outage the sweeps do not pay a timeout per idle organization. (An expired organization that *does* have due work, say a recurring template, is asked once per 15 minutes while that work stays due.)
- An outage -> the last answer stands; with none, the job runs. Only organizations linked to Workeasy360 (`workeasy360_organization_id` set) are ever checked.

| Job (Celery task) | Schedule | Class | Denied: what happens, and how it resumes |
|---|---|---|---|
| `automation.tasks.run_execution_task` (notifications, outbound webhooks, report drafts) | on demand: events, schedules, scans, manual run, retry, recovery | **A** - the customer effect | Returns `{"skipped": ...}`; the execution stays `pending`/`running`, untouched. The recovery sweeper re-enqueues it within 5 minutes of a renewal. Steps already `succeeded` never repeat. |
| `dispatch_automation_events_task` | 60 s | **A** (producer) | The organization is skipped; its events stay undispatched and are dispatched in order after a renewal (up to 200 per run). Writes are refused while denied, so few accumulate. |
| `run_due_schedules_task` | 300 s | **A** (producer) | Skipped before the occurrence is claimed, so `next_run_at` does not advance. After a renewal it fires **once** for the oldest missed occurrence and advances past today (the SKIP_MISSED policy): no backlog, no double. |
| `run_due_scans_task` (`invoice.overdue`, `stock.low`) | 300 s | **A** (producer) | Skipped before anything is claimed. After a renewal, entities that still match fire once; the cooldown applies as always. |
| `recover_stalled_executions_task` | 300 s | **A** (guard) | Skips the organization, so a denied execution is neither re-enqueued (it would only be skipped again) nor spends its recovery budget (10) and fails. |
| `sales.tasks.generate_recurring_invoices_task` | 3600 s | **A** | Skipped before generation; `next_run_at` stays. After a renewal the catch-up loop generates **each missed occurrence once** as a DRAFT (`RecurringInvoiceRun` is unique per occurrence), never posted: three months of denial gives three drafts, not zero and not six. |
| `purchases.tasks.generate_recurring_bills_task` | 3600 s | **A** | Same as invoices (`RecurringBillRun`). |
| `purchases.tasks.generate_recurring_expenses_task` | 3600 s | **A** | Same as invoices (`RecurringExpenseRun`). |
| `documents.tasks.run_ocr_task` (sends the file to the OCR provider) | on demand | **A** | Skipped; the document stays `queued`. The sweeper re-enqueues it after a renewal. |
| `documents.tasks.recover_stalled_ocr_task` | 300 s | **A** (guard) | Skips the organization: nothing is re-enqueued and no per-document recovery budget (5) is spent. |
| `ai.rag.tasks.index_document_task` (embeds with the AI provider) | on document change, or `manage.py ai_reindex_documents` | **A** | Returns `"skipped"`, not retried, and marks the document's index `pending` with reason `workeasy360_access` (only when it has no index yet or is already pending; an indexed document keeps serving its complete chunks). |
| `ai.rag.tasks.resume_skipped_indexing_task` | 900 s | **A** (producer) | Enqueues indexing for documents marked `workeasy360_access`, for organizations that are allowed again. Nothing is asked for an organization with nothing marked. This is what resumes skipped indexing without an operator. |
| `ai.rag.tasks.purge_expired_ai_data` | 86400 s | **C** retention | Never checked: retention must run for every organization. |
| `sso.tasks.purge_workeasy360_records_task` | 21600 s | **C** housekeeping | Never checked: the connector's spent sign-in flows, logout-token ids and long-ended session records; no organization. |

Not jobs: EasyBook sends no e-mail or reminders and has no external-sync task (bank statement import is a user's request, behind the request gate). `manage.py ai_reindex_documents` only enqueues the gated task; `link_workeasy360_organization` and `db_preflight` are operator commands (class C). Everything that reaches a customer through an automation (notification, outbound webhook) runs inside `run_execution_task`, so one gate covers all trigger paths.

Consequences worth knowing: after a long denial, a renewal produces one burst of catch-up recurring drafts (reviewable, never posted) and the stale undispatched events fire their rules once. That is deliberate: skipping must not lose work, and every generator is idempotent per occurrence.

## Opening EasyBook directly (SSO-only)

A person already signed in to Workeasy360 who types `https://book.workeasy360.com/` (or any EasyBook page) arrives signed in: no EasyBook sign-in page, no password. This is the Workeasy360 integration guide §6b (`docs/phase-3/09-product-integration-guide.md`, `docs/universal-sso/one-login.md`). It applies only while EasyBook is **SSO-only**; otherwise nothing changes (a visitor without a session goes to `/login`, which offers both ways in).

**What happens.** `src/proxy.ts` sees a page navigation from a browser with no session cookies. Instead of `/login?next=<page>` it redirects to EasyBook's own `/api/auth/workeasy360/start?next=<page>` (no organization hint; `/` carries no `next`). That is the sign-in described above. Workeasy360 then completes it silently when it has a session and the person's preference is Automatic (the default), asks first when the preference is "Ask me each time", and shows its central sign-in when it has no session. The person lands on the page they asked for.

**How SSO-only is known.** The proxy asks Django, which owns the setting: `GET /api/v1/auth/methods/` answers `password: false` and `workeasy360: true` exactly when `WORKEASY360_SSO_ONLY` is on and the integration is configured (`src/lib/auth/sso-only.ts`). The answer is kept in the Next.js server process for 60 s and shared by concurrent requests, so the backend is asked about once a minute, and only when a visitor without a session arrives. There is no second setting to keep in step. If Django cannot be asked, the last answer stands; with none, EasyBook is treated as **not** SSO-only, so the visitor gets the sign-in page as before. Not knowing never starts a sign-in.

**It never starts by itself** in these cases; the visitor is shown the sign-in page (**Access EasyBook** / **Continue with Workeasy360**) instead:

(Cookie names below are the plain ones; with Secure cookies they carry the `__Host-` prefix, see "Cookie names".)

| Case | How it is decided |
|---|---|
| On the sign-in pages (`/login`, `/register`, `/forgot-password`), whatever the query, including `?sso_error=` and `?reason=expired` | They are public paths, answered before any of this runs. **The sign-in page never auto-starts.** |
| After the person signed out of EasyBook in this browser | The cookie `eb_signed_out` (value `1`, httpOnly, SameSite=Lax, **a session cookie: no Max-Age**, so every tab and the proxy see it for one browser session). Both **Sign out of EasyBook** and **Sign out of EasyBook and Workeasy360** leave it: `POST /api/auth/logout` sets it (the combined sign-out calls that first), and `/api/auth/workeasy360/logout` sets it again, but only for a request from EasyBook's own pages (`Sec-Fetch-Site: same-origin`, or no such header), so a link on another site or a sibling subdomain cannot set it. It stays until the person **signs in to EasyBook again**: the callback and the password sign-in both clear it. A sign-out therefore never ends in a silent sign-in. The launcher (`/?organization=`) and the buttons on the sign-in page still start a sign-in, because the person asked for one. |
| This browser already started automatically less than 60 s ago and still has no session (the **loop guard**) | The cookie `eb_sso_auto` (httpOnly, SameSite=Lax, Max-Age 60) holds the time of the start; the proxy sets it on the redirect that auto-starts. A dropped cookie, or a failure Workeasy360 did not report as `?sso_error=`, ends at the sign-in page instead of looping. A cookie it cannot read counts as recent. A sign-in does **not** clear it: it expires by itself after its 60 s, so a browser that drops the session it was just given is still caught. |
| A session exists | The proxy only auto-starts when there is no session at all (the access and refresh cookies are not both present). A person whose access ended keeps their session and the **/access-ended** explanation (see "After sign-in" above); a session the API rejects ends at `/login?reason=expired`, which never starts. A session is never replaced silently. |
| Not a page navigation | Only `GET` document navigations start a sign-in (`Sec-Fetch-Mode: navigate` + `Sec-Fetch-Dest: document`, or, where a browser sends no `Sec-Fetch-*` header, `Accept: text/html`). `/api/...` routes and the BFF are not served by the proxy at all (its matcher excludes `api/`), and fetches, Next.js client-side navigations and prefetches (`RSC`, `Next-Router-Prefetch`), frames and browser prefetches (`Sec-Purpose`) are never redirected to Workeasy360; they get the answer they always did. |

**`next` stays a same-site path.** The page asked for goes through `sameSitePath` (`src/lib/redirect.ts`) before it is put into the start URL, and the start route checks it again. A path that does not pass (`//evil.example`, a backslash, a tab) is dropped and sign-in lands on the dashboard.

**The redirect is relative.** The proxy builds it like its other redirects: Next.js turns a same-origin redirect from the proxy into a relative `Location`, so it never names the address the server listens on (`localhost:3000` behind the production proxy). The route handlers use `redirectToPath` for the same reason (next section).

**For the operator.** Nothing to configure. The two cookies carry no secret and no identity. If a deployment ever needs this switched off while keeping SSO-only, the lever is `src/proxy.ts` (`autoStartRedirect`); there is deliberately no setting for it, because a second setting could disagree with `WORKEASY360_SSO_ONLY`. Tests: `src/proxy.test.ts` ("someone opens EasyBook directly"), `src/lib/auth/direct-visit.test.ts`, `src/lib/auth/sso-only.test.ts`.

## Cookie names: `__Host-` (a compromised sibling subdomain)

Every Workeasy360 product is a sibling subdomain of `workeasy360.com`. A cookie another product (or anything else under that domain) sets with `Domain=.workeasy360.com` is sent to EasyBook too, and under a plain name it is indistinguishable from EasyBook's own, with the last duplicate winning in Next.js's parser. A compromised sibling could plant its own `eb_sso` flow and send a victim to the callback with the attacker's code and state, signing the victim in to the attacker's books past the state check (a login CSRF), or plant `eb_at` / `eb_rt` to the same effect.

So with `SESSION_COOKIE_SECURE=1` (which production requires) **every authentication cookie carries the `__Host-` prefix**. A browser accepts such a cookie only when it is set `Secure`, with `Path=/` and **no `Domain`**, which is to say only by this exact host: a sibling cannot set one. A cookie under the plain name is never read.

| Cookie | Name when Secure | Plain name (development, no Secure) |
|---|---|---|
| Access token | `__Host-eb_at` | `eb_at` |
| Refresh token | `__Host-eb_rt` | `eb_rt` |
| Organization choice | `__Host-eb_org` | `eb_org` |
| Sign-in flow (the binding to the server-side state, nonce and PKCE verifier; signed; 10 min) | `__Host-eb_sso` | `eb_sso` |
| Signed-out marker | `__Host-eb_signed_out` | `eb_signed_out` |
| Loop guard | `__Host-eb_sso_auto` | `eb_sso_auto` |

`eb_sso` is no longer scoped to the callback path: `__Host-` requires `Path=/`. It stays httpOnly, single-use and signed by Django. All names come from one function (`cookieName()`, `src/lib/auth/cookie-names.ts`); nothing hard-codes them. Without Secure (local development over plain http) the prefix is not possible and the plain names stay.

**On deploy, everyone is signed out of EasyBook once.** The session an earlier version kept under the plain names is not read any more. People come back silently through Workeasy360 (their next visit starts a sign-in, and Workeasy360 answers from its own session), or sign in again. The old cookies are expired the next time a session is written or ended. Nothing needs doing by the operator; announce it if you have long-lived sessions you care about.

## Starting a sign-in has its own rate limit

Because a direct visit starts a sign-in by itself, starts are routine (an office behind one address, an uptime check). `POST /auth/workeasy360/start/` therefore has its own throttle scope, `sso_start` (`THROTTLE_SSO_START`, default `60/min` per client address), instead of sharing the `auth` bucket (`THROTTLE_AUTH`, `20/min`) with the callback and password sign-in. The callback keeps the `auth` limit, and starts no longer use it up. The start view is limited by its scope alone; the blanket anonymous limit would count the same request a second time.

## ID token checks beyond the seven steps

`azp` and a list-valued `aud` (OpenID Connect Core 3.1.3.7): an `azp` that is present must equal this client's id, and an `aud` that names anybody besides this client is accepted only with an `azp` that names this client. The product binding (`product_code`) is never skipped: an empty `WORKEASY360_PRODUCT_CODE` stops the process (see the table above), and the connector refuses every sign-in meanwhile (`workeasy360_unavailable`). These checks are the connector's (`workeasy360_sso.tokens`); EasyBook proves them through its own API. Tests: `backend/sso/tests/test_protocol.py` (`AuthorizedPartyTests`, `ProofOfPossessionTests`: a code bound to another PKCE challenge is refused by the fake provider's token endpoint, which verifies S256 like the real one; a known `kid` with a forged RS256 signature, and a genuine token with a swapped payload, are refused).

## Redirects from the SSO routes are relative

`/api/auth/workeasy360/start`, `/api/auth/callback/workeasy360` and `/api/auth/workeasy360/logout` redirect to EasyBook's own pages with a relative `Location` (`src/lib/redirect.ts`). Every target, `next` after sign-in included, goes through one check (`sameSitePath`): no whitespace, control character or backslash anywhere - a browser strips a tab, so `/<tab>/evil.example` would become `//evil.example` - and it must resolve on the same site. In a route handler Next.js builds `request.url` from the address the server listens on, not the one the browser used. Behind the production proxy (`HOSTNAME=0.0.0.0`) `new URL(path, request.url)` sent every sign-in to `http://localhost:3000`.

## The workeasy360-sso connector and back-channel logout (2026-11-17)

The OpenID Connect protocol is the shared **workeasy360-sso** connector, not EasyBook code: version 0.3.1, installed from the private repository WorkEasy360/workeasy360-sso at the commit `requirements.txt` pins ("Installing the connector (a private repository)" above), `INSTALLED_APPS` entry `workeasy360_sso`. It owns discovery and the key set, the authorization request (state, nonce, PKCE S256, `organization`), the single-use pending sign-in, `private_key_jwt`, ID-token and logout-token verification, the entitlement API with its cache and rules, and the back-channel logout receiver. It reads its configuration from the `WORKEASY360_*` settings above (`sso/connector_settings.py`); no environment variable changed its name.

EasyBook keeps, unchanged:

- its routes (`auth/methods/`, `auth/workeasy360/start/`, `auth/workeasy360/callback/`, `auth/workeasy360/end-session/`, called by the BFF). The BFF, the registered redirect URI and the post-logout URI are untouched;
- the error codes the BFF maps: `sso/errors.py` translates every connector refusal back (`state_mismatch`, `flow_expired`, `workeasy360_unavailable`, `email_unverified`, `account_conflict`, `membership_inactive`, `account_disabled`, ...);
- who is provisioned and linked, and how (`sso/provisioning.py`, now the connector's user and organization adapters, in one transaction), linking by verified address exactly as before;
- entitlement enforcement: the 403 codes, read-only, `workeasy360_sign_in_required`, background gating and their log lines (`sso/entitlement.py`, now a thin wrapper over `workeasy360_sso.entitlements`).

**What Workeasy360 sees.** The same client id, credential, scopes, redirect URI and post-logout URI, and the same authorization, token and entitlement requests. The one addition is a field the entitlement API defines: every entitlement question now also says `write=false` (EasyBook never asks the "fresh, for a write" variant). Once the back-channel logout URI is registered, ID tokens carry `sid` (the platform's choice, ADR-069).

**How the BFF fits.** The connector binds a pending sign-in to a browser with a cookie. EasyBook's browser never talks to Django, so the BFF is the connector's browser: the binding value travels in the signed `flow` blob the BFF already keeps (it no longer carries state, nonce or verifier, which stay server-side), and the callback hands it back to the connector. A sign-in records no Django session: EasyBook's session is still the access/refresh pair. The connector records the sign-in against a session that is never stored (`sso/flow.py`, `UnstoredSession`). The callback answers a refusal without rolling the request back, so a refused pending sign-in stays spent.

**Data.**

- `sso.ExternalIdentity` stays the authoritative (issuer, `sub`) → user link, written exactly as before.
- The connector's `FederatedIdentity` (`workeasy360_sso_federated_identity`) mirrors it. Migration `sso.0002_federated_identity_backfill` copied every existing link into it, **adding rows only**: nothing existing is changed, moved or deleted, and a mirror row already there is left alone. Every sign-in keeps the mirror in step.
- `workeasy360_sso_product_session`: one row per Workeasy360 sign-in, with its `sid`. The token pair the sign-in issues carries the row's id (claim `workeasy360_session`), and every refresh keeps it.
- In-flight sign-ins, spent logout-token ids and ended Workeasy360 sign-ins. `sso.tasks.purge_workeasy360_records_task` (beat, every 6 h) purges what is no longer needed. It is not the connector's `w360sso_cleanup`, which would delete every session record here (no EasyBook session is a Django session): an open record goes only once its person holds no live refresh token, and an ended one is kept longer than a refresh token lives.

### Back-channel logout

Workeasy360 POSTs a signed logout token to `/api/v1/auth/workeasy360/backchannel-logout/`: under `/api/v1/`, the only prefix the production edge routes to Django (`infrastructure/terraform/alb.tf`), and on the redirect URI's origin, as Workeasy360 requires. The connector verifies it (signature from the key set, `iss`, `aud`, `iat`/`exp`, the back-channel event, no `nonce`, `typ: logout+jwt`) and accepts each `jti` once. A forged, expired or replayed token is answered `400` and ends nothing.

| Token names | Ends |
|---|---|
| `sid` (one Workeasy360 sign-in ended: a sign-out on that device, an account switch, a revoked session) | the EasyBook sessions that sign-in opened, nothing else. The same person's other devices stay signed in |
| only `sub` ("sign out everywhere", a password change or reset) | every EasyBook session of that person: every session record, and every refresh token they hold |

"Ends" means at once. From then on every access token of that session is refused (`401`) and its refresh token is refused too (`sso/sessions.py`), including tokens rotated since the sign-in. The BFF then signs the browser out as for any expired session.

- Sessions opened before this release (or by a password) carry no session record. Only "sign out everywhere" reaches them: their refresh tokens are revoked, so they end at their next refresh, within one access-token lifetime (`JWT_ACCESS_MINUTES`, 15 minutes).
- A sign-in that was still at Workeasy360 when its Workeasy360 sign-in ended is refused (`flow_expired`, "start again").
- If ending the sessions fails, nothing is recorded (the `jti` included), Workeasy360 is answered `503` and delivers again.
- Each ended session is audited (`sign_in`, `delete`, method `workeasy360_backchannel`) in the organization it was signed in to.

Tests: `backend/sso/tests/test_backchannel.py`.

### Rolling out the connector release, and rolling it back

The platform side (back-channel logout, ADR-069) and EasyBook can be deployed in either order. Until both are live and the URI is registered, nothing is sent to the receiver.

**Before deploying**

1. With the production environment, run `python manage.py check --deploy` and `python manage.py w360sso_check`. The connector refuses some configuration the old client accepted. In SSO-only production it refuses to start on any `workeasy360_sso.E001` error: an RSA key under 2048 bits, a key file it cannot read, a query string in `WORKEASY360_POST_LOGOUT_REDIRECT_URI`, a trailing `/` on `WORKEASY360_ISSUER`. Fix those first.
2. Take a database snapshot, as for every release (`infrastructure/runbooks/deploy.md`).

**Deploy**

1. `migrate`: `workeasy360_sso.0001`-`0003` create the connector's tables, and `sso.0002` back-fills the identity mirror. It only adds rows; nothing existing changes.
2. Deploy `api`, the workers and `beat` (the housekeeping task is new). Everyone stays signed in: existing token pairs keep working and refresh as before, and nobody's link changes. A sign-in that is mid-flight at the moment of the deploy ends once in `sso_error=expired` (its flow is the old release's). Cached entitlement answers start afresh (the connector keeps its own cache keys): each person's first request after the deploy asks Workeasy360 again.
3. Check: one Workeasy360 sign-in works; `python manage.py w360sso_manifest --login-url https://book.workeasy360.com/login` prints the URIs to register.

**Turn on back-channel logout** (only after EasyBook and the platform's ADR-069 release are both live)

1. In Super Admin, Products → EasyBook → Authentication, set the back-channel logout URL to `https://book.workeasy360.com/api/v1/auth/workeasy360/backchannel-logout/` (the redirect URI's origin).
2. Sign in to EasyBook, then sign out of Workeasy360 on that device. EasyBook's next request is refused and the browser lands on the sign-in page; Super Admin shows the delivery as delivered.

**Roll back**

1. First clear the back-channel logout URL in Super Admin: the previous image answers `404` to its POSTs, which the platform records as failed deliveries.
2. Redeploy the previous image. There is no migration to reverse. The connector's tables stay, and the previous code ignores them. `ExternalIdentity` was authoritative and written exactly as before, so every link is intact. Token pairs issued by this release are ordinary SimpleJWT tokens to the previous code (it ignores the extra claim), so nobody is signed out. A sign-in in flight ends once in `expired`.
3. To redeploy this release later, migrate again (a no-op); the mirror catches up at each sign-in.
4. Never drop the connector's tables while either image may still run.

## Moving to WorkEasy360 Accounts (`accounts.workeasy360.com`)

WorkEasy360's central sign-in is moving from `https://auth.workeasy360.com` to `https://accounts.workeasy360.com` (platform ADR-067). It is the same provider, with the same keys, the same people (`sub`), and the same client id, credential, redirect URI and post-logout URI; only the issuer name changes. The platform answers on both names while products move, and each name issues tokens only under its own `iss`. EasyBook keeps verifying exactly one issuer, the configured one.

**Which value.** `https://accounts.workeasy360.com`, once the platform is at stage b of its move. Before that, discovery under that name is refused, and every sign-in would end in `unavailable`.

**Data, in the same release.** EasyBook links people on (issuer, `sub`) twice: `ExternalIdentity` and the connector's mirror. Move both before the new configuration takes traffic:

```
python manage.py move_workeasy360_issuer --from https://auth.workeasy360.com           # dry run: both tables
python manage.py move_workeasy360_issuer --from https://auth.workeasy360.com --apply   # one transaction
```

- It moves the links to the configured issuer only, with their session records and ended Workeasy360 sign-ins, and logs `sso.issuer_moved`.
- It folds a duplicate link (the same person on the same EasyBook user under both names).
- If one person has two EasyBook users, or one EasyBook user holds two people, in either table, it moves nothing and lists them for a human decision.
- Running it again moves nothing.
- Without it a returning person is not recognised: they get a second, empty account, or `account_conflict`.

**Rollout.** Read the production `WORKEASY360_ISSUER`. Set it to `https://accounts.workeasy360.com`. Deploy, running `migrate` and then `move_workeasy360_issuer --from <the old value> --apply` before the release takes traffic. Check with `w360sso_check`, then one sign-in and one sign-out.

**Rollback** (while the platform still serves the previous name): put the previous value back, then run `move_workeasy360_issuer --from https://accounts.workeasy360.com --apply`.

Back-channel logout keeps working through the move: the platform signs each logout token with the issuer EasyBook last redeemed a code under (ADR-069 addendum). Tests: `backend/sso/tests/test_issuer_move.py`.

## What this does not do yet

- **Single sign-out, one way only.** Signing out of EasyBook ends the EasyBook session only. While the Workeasy360 session is still alive, the next **Continue with Workeasy360** signs straight back in (typing EasyBook's address does not: the sign-out marker shows the sign-in page first). The other way round is covered: signing out of Workeasy360 ends EasyBook's sessions of that sign-in (back-channel logout, below).
- **Member management.** EasyBook still has no invite or role-change screens. People arrive through Workeasy360, and roles beyond the default need a backend change or the Django admin.

## Universal sign-in: what the person controls (2026-10-12)

Workeasy360 now lets each person choose how it signs them in to products (Workeasy360 ADR-044):
automatically, after asking ("Ask me each time", the default), or by preferring the product's own sign-in.
Nothing about that choice lives in EasyBook; what it means here:

- **The sign-in page** asks "Already a Workeasy360 user?" with **Access EasyBook**. When password
  sign-in is also on, EasyBook's own form is behind **Use another sign-in method** (`/login?method=password`,
  a plain link). **The sign-in page never starts a Workeasy360 sign-in by itself**, whatever its query
  (`?sso_error=`, `?reason=expired`): it is where a refusal, an expired session or a sign-out ends. A visit to
  any other page with no session is what starts one (see "Opening EasyBook directly").
- **Workeasy360's access chooser.** When the person's preference is to be asked, Workeasy360 shows its own
  "How would you like to access EasyBook?" page before returning a code. EasyBook sees nothing new: the
  usual callback, or `error=access_denied` when the person chose Cancel (shown as `cancelled`). EasyBook is
  registered `sso_only` while `WORKEASY360_SSO_ONLY` is on, so Workeasy360 never offers "use EasyBook's own
  sign-in" for it.
- **Error codes added on the Workeasy360 side:** `unauthorized_client` maps to `unavailable`;
  `interaction_required` and `login_required` map to `expired`.
- **Sign-out.** Both sign-outs go on only after EasyBook's own session has really ended (`POST /api/auth/logout` answered OK); if it did not, the menu (or the access-ended page) stays where it is and says "Could not sign you out. Please try again.", and nothing continues to Workeasy360. Both leave the `eb_signed_out` marker (see "Opening EasyBook directly"), so
  typing EasyBook's address afterwards shows the sign-in page instead of signing straight back in until the
  person signs in again. "Sign out of EasyBook" ends EasyBook's session (the refresh token is revoked). With
  `WORKEASY360_POST_LOGOUT_REDIRECT_URI` set (and registered in Workeasy360 as a post-logout URI of the
  EasyBook integration), the account menu also offers **Sign out of EasyBook and Workeasy360**: EasyBook's
  session ends first, then the BFF route `/api/auth/workeasy360/logout` sends the browser to Workeasy360's
  `end_session_endpoint` (named by Django from discovery), which asks the person to confirm and returns
  them to that URI. Other products' sessions are not ended.
- **Account switching.** When a different person signs in through Workeasy360 in a browser that already
  holds an EasyBook session, the callback revokes the previous session's refresh token before writing the
  new one - the earlier person is signed out properly, not merely overwritten in the cookie.

| Variable | Value |
|---|---|
| `WORKEASY360_POST_LOGOUT_REDIRECT_URI` | Optional, e.g. `https://book.workeasy360.com/login`. https in production; exact match with Workeasy360's registration. Empty: only EasyBook's own sign-out |
