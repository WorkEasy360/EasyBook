# Security incident

## Triage first — classify before acting (matches phase 12 section 90)

| Signal | Likely severity |
|---|---|
| Cross-tenant data returned by the API | BLOCKER — see "Tenant breach" below |
| A leaked credential (Secrets Manager value, `.env`, API key in a log) | HIGH-BLOCKER depending on scope — see "Credential compromise" |
| A dependency CVE (pip-audit/Trivy) with no known exploitation | Triage per CI (`.github/workflows/backend-ci.yml`'s `static`/`container` jobs already block merges on this) — not an active incident unless there's evidence of exploitation |
| Suspicious automation/webhook activity | Check `automation/CLAUDE.md`'s safety limits (`AUTOMATION_MAX_DEPTH`) first — may be a misconfigured rule, not an attack |

## Tenant breach (org A saw org B's data)

This is the single most severe class of incident this codebase's own test
suite exists to prevent (`backend/core/tests/test_tenant_isolation.py`,
every app's own `tests/test_rls.py`). If one is reported:

1. **Do not guess which layer failed.** Reproduce it, then run
   `backend/core/tests/test_tenant_isolation.py` and the specific app's RLS
   tests against the same data shape — a genuine RLS bypass should already
   fail an existing test; if it doesn't, the test suite itself has a gap that
   needs a new regression test as part of the fix, not just a patch.
2. Check whether it's an RLS bypass (raw SQL/`all_objects` misuse — see
   `core/CLAUDE.md` on why `TenantManager` failing closed is not sufficient
   on its own) versus an app-level authorization bug (missing
   `OrganizationScopedMixin`, a view resolving the wrong organization from a
   header/body value instead of the authenticated membership).
3. Once understood: fix, add the regression test that would have caught it,
   and only then consider whether affected customers must be notified —
   that's a business/legal decision this runbook does not make.

## Credential compromise

1. Rotate the specific credential per [secret-rotation.md](secret-rotation.md).
2. If it was the Django `SECRET_KEY` or the app DB password: assume every
   session/JWT/DB connection using the old value is untrustworthy going
   forward, not just "should be rotated eventually."
3. If it was an AWS IAM credential (not something this stack's app roles
   normally expose, since ECS tasks use role-based creds via the execution/
   task roles in `terraform/iam.tf`, never static access keys) — this means
   a human's own AWS credentials leaked, which is an AWS-account-level
   incident (rotate via IAM, review CloudTrail for what that credential did),
   broader than this application.

## Prompt injection / RAG tenant leakage (Ask Books)

`ai/tests/test_prompt_injection.py` and `ai/tests/test_rls.py` are the
existing regression suite for exactly this. Reproduce against those tests
first; if they pass but a real leak still occurred, the gap is between what
those tests model and the real request — capture the real request/response
(redacting PII per phase 12 section 43's "never send... raw OCR text" spirit
even in an internal incident writeup) as a new test case before fixing.

## Automation loop / runaway execution

`AUTOMATION_MAX_DEPTH` (`config/settings/base.py`, default 5) is the
existing circuit breaker for causation chains. If it fired, check the
`AutomationExecution` records for the `category: "safety_limit"` failure —
that's the system working as designed, not itself an incident. If executions
are running away WITHOUT hitting that limit, that's the actual bug to
investigate (a causation chain bypassing `causation_context`'s depth
tracking somewhere).

## After any incident

Write down what actually happened while it's fresh, even briefly — this
runbook (and the others in this directory) should be updated with anything
that was wrong, missing, or slower than expected during the response.
