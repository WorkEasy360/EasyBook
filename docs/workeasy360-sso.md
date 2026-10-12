# Sign in with Workeasy360

**Written for: whoever deploys or operates EasyBook.**

EasyBook is a Workeasy360 product. People sign in with the same email and password they use for Workeasy360; EasyBook never sees that password. With `WORKEASY360_SSO_ONLY` on (the default outside local development), it is the only way in: EasyBook's own password sign-in, self-registration and "create an organization" are switched off, because people, plans and organizations are managed in Workeasy360.

## How a sign-in works

1. The user clicks **Continue with Workeasy360** on `/login`, or opens EasyBook from the Workeasy360 app launcher (`https://book.workeasy360.com/?organization=<uuid>`).
2. The BFF route `/api/auth/workeasy360/start` asks Django for an authorization URL. Django generates state, nonce and a PKCE verifier and returns them sealed in a signed `flow` blob. The BFF keeps the blob in an httpOnly cookie, `eb_sso`, scoped to the callback path and valid for 10 minutes, then redirects the browser to Workeasy360.
3. The user signs in at Workeasy360, including any second factor. If they came from the launcher, Workeasy360 already knows the organization; otherwise it shows its organization chooser.
4. Workeasy360 checks the membership, that the organization has EasyBook (trial or paid), and the seat. It then redirects to `/api/auth/callback/workeasy360?code=…&state=…`.
5. The BFF relays the code, the state and the kept flow to Django. Django:
   - compares the state in constant time;
   - exchanges the code, authenticating with a client assertion signed by EasyBook's private key;
   - verifies the ID token offline;
   - provisions the user, organization and membership;
   - returns the usual access/refresh pair. The BFF stores it exactly as a password sign-in does.

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
   - Set the redirect URI to exactly `https://book.workeasy360.com/api/auth/callback/workeasy360`. Matching is by exact string, so a trailing slash breaks it.
   - Set the scopes to `openid email profile organization`.
   - Set client authentication to `private_key_jwt`.
   - Add a public-key credential from `easybook-w360.pub`. Workeasy360 returns its credential id.
   - Activate the integration.
3. Set the environment:

| Variable | Value |
|---|---|
| `WORKEASY360_ISSUER` | The Workeasy360 issuer, exactly as its discovery document names it (no trailing slash) |
| `WORKEASY360_CLIENT_ID` | The integration's client id |
| `WORKEASY360_REDIRECT_URI` | The registered redirect URI |
| `WORKEASY360_KEY_ID` | The credential id from step 2 |
| `WORKEASY360_PRIVATE_KEY_FILE` (or `WORKEASY360_PRIVATE_KEY`) | The private key. Inline PEM may use literal `\n` |
| `WORKEASY360_SSO_ONLY` | `1` (the default). Production refuses to start if it is on while any value above is missing |
| `WORKEASY360_DEFAULT_ROLE` | Optional. Role for people joining an existing organization (default `staff`) |
| `WORKEASY360_DEFAULT_CURRENCY` | Optional. Currency of a newly provisioned organization (default `INR`) |

To rotate the key, add a second credential in Workeasy360, deploy the new key and `WORKEASY360_KEY_ID`, confirm sign-in works, then revoke the old credential.

## Local development

`dev.py` leaves `WORKEASY360_SSO_ONLY` off, so password sign-in keeps working with no Workeasy360 running. To rehearse the real flow, register a development integration whose redirect URI is `http://localhost:3000/api/auth/callback/workeasy360` (Workeasy360 accepts `http://localhost` outside production), fill in the `WORKEASY360_*` values in `backend/.env`, and set `WORKEASY360_SSO_ONLY=1`.

## After sign-in: the organization's access is re-checked (`sso/entitlement.py`)

Workeasy360 checks the trial or plan at sign-in. EasyBook's own session then lives on (its refresh token rotates), so EasyBook also asks Workeasy360's entitlement API, as the integration guide requires, whether each organization linked to Workeasy360 may still use EasyBook:

- One answer per question, shared through the cache, for Workeasy360's `revalidate_after` (900 s). A denial is asked again after 60 s, so a renewal takes effect quickly. The call authenticates with EasyBook's own `private_key_jwt` credential; there is no product parameter.
- **Per person for a signed-in request.** When the user has an `ExternalIdentity` for the configured issuer, the question is (Workeasy360 organization, the `sub` they signed in with): the form carries `subject`, and Workeasy360 can also answer `deny` with `membership_inactive` (account disabled or locked by an administrator, membership suspended or removed) or `no_seat` (a per-seat product whose seat was unassigned). The cache key (the subject is hashed into it), the per-process copy, the single flight, the back-off and the 60 s denial re-check are all per (organization, person), so one person's denial never touches a colleague. A user with no Workeasy360 identity (a password account in local development) is asked per organization, as before. The subject lookup is cached for 5 minutes, so a request costs no extra query; only a found subject is cached ("no identity" is looked up each time, so it can never hide an identity created a moment later), and the entry is also dropped after commit when a sign-in links a new identity.
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

Not jobs: EasyBook sends no e-mail or reminders and has no external-sync task (bank statement import is a user's request, behind the request gate). `manage.py ai_reindex_documents` only enqueues the gated task; `link_workeasy360_organization` and `db_preflight` are operator commands (class C). Everything that reaches a customer through an automation (notification, outbound webhook) runs inside `run_execution_task`, so one gate covers all trigger paths.

Consequences worth knowing: after a long denial, a renewal produces one burst of catch-up recurring drafts (reviewable, never posted) and the stale undispatched events fire their rules once. That is deliberate: skipping must not lose work, and every generator is idempotent per occurrence.

## Redirects from the SSO routes are relative

`/api/auth/workeasy360/start`, `/api/auth/callback/workeasy360` and `/api/auth/workeasy360/logout` redirect to EasyBook's own pages with a relative `Location` (`src/lib/redirect.ts`). Every target, `next` after sign-in included, goes through one check (`sameSitePath`): no whitespace, control character or backslash anywhere - a browser strips a tab, so `/<tab>/evil.example` would become `//evil.example` - and it must resolve on the same site. In a route handler Next.js builds `request.url` from the address the server listens on, not the one the browser used. Behind the production proxy (`HOSTNAME=0.0.0.0`) `new URL(path, request.url)` sent every sign-in to `http://localhost:3000`.

## What this does not do yet

- **Single sign-out.** Signing out of EasyBook ends the EasyBook session only. While the Workeasy360 session is still alive, the next **Continue with Workeasy360** signs straight back in.
- **Member management.** EasyBook still has no invite or role-change screens. People arrive through Workeasy360, and roles beyond the default need a backend change or the Django admin.

## Universal sign-in: what the person controls (2026-10-12)

Workeasy360 now lets each person choose how it signs them in to products (Workeasy360 ADR-044):
automatically, after asking ("Ask me each time", the default), or by preferring the product's own sign-in.
Nothing about that choice lives in EasyBook; what it means here:

- **The sign-in page** asks "Already a Workeasy360 user?" with **Access EasyBook**. When password
  sign-in is also on, EasyBook's own form is behind **Use another sign-in method** (`/login?method=password`,
  a plain link).
- **Workeasy360's access chooser.** When the person's preference is to be asked, Workeasy360 shows its own
  "How would you like to access EasyBook?" page before returning a code. EasyBook sees nothing new: the
  usual callback, or `error=access_denied` when the person chose Cancel (shown as `cancelled`). EasyBook is
  registered `sso_only` while `WORKEASY360_SSO_ONLY` is on, so Workeasy360 never offers "use EasyBook's own
  sign-in" for it.
- **Error codes added on the Workeasy360 side:** `unauthorized_client` maps to `unavailable`;
  `interaction_required` and `login_required` map to `expired`.
- **Sign-out.** "Sign out of EasyBook" ends EasyBook's session (the refresh token is revoked). With
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
