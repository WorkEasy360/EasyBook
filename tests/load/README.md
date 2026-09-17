# Load testing (phase 12 section 59-62)

**Status: script written, never run.** There is no deployed staging
environment yet (no AWS account was available while doing this phase 12
pass), and load testing against tiny local fixtures is explicitly what
phase 12 section 59 warns against ("Do not evaluate performance only on
tiny fixtures"). This is a ready-to-use script for whenever staging exists
with a realistic dataset, not a performance result.

## Tool

[k6](https://k6.io) — a standalone Go binary, not a project dependency
(nothing to add to `backend/requirements.txt`). Install separately on
whatever machine runs the test (never against production — phase 12 section
62: "Never run destructive load tests against production").

## Running it

```
k6 run \
  -e BASE_URL=https://staging.example.com \
  -e EMAIL=loadtest@example.com \
  -e PASSWORD='...' \
  -e ORG_ID=<uuid> \
  api-smoke.js
```

`api-smoke.js` logs in once per virtual user (respecting `throttle_scope =
"auth"` — see its own comment) then hits the read-heavy endpoints phase 12
section 60 names explicitly. It does not create/post/pay anything — mutating
load tests need their own script, written once there's a realistic
seeded dataset to run them against safely (repeatedly creating real invoices
against staging is itself something to design deliberately, not bolt on here).

## Before trusting any result this produces

1. Seed staging with realistic volume first (customers, invoices, journal
   lines, stock movements — phase 12 section 59's own list), not the tiny
   fixtures the automated test suite uses.
2. Watch `terraform/cloudwatch.tf`'s alarms (RDS CPU/connections, ALB
   latency) during the run, not just k6's own summary — a report that looks
   fast from k6's perspective but is quietly exhausting RDS connections is
   the actual failure mode phase 12 section 15's connection-pooling review
   exists for.
