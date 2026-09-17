// Read-heavy smoke load test — see README.md for status/usage. Covers the
// endpoints phase 12 section 60 names: Dashboard-equivalent list/summary
// views, Invoice/Bill lists, Customer/Vendor detail, and the report suite
// (P&L, Balance Sheet, Trial Balance, AR/AP Ageing).
//
// Real endpoint paths only, verified against backend/*/api/urls.py — never
// invented (root CLAUDE.md rule 5).

import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const EMAIL = __ENV.EMAIL;
const PASSWORD = __ENV.PASSWORD;
const ORG_ID = __ENV.ORG_ID;

export const options = {
  scenarios: {
    smoke: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 10 },
        { duration: '2m', target: 10 },
        { duration: '30s', target: 0 },
      ],
    },
  },
  thresholds: {
    // Starting points, not load-tested SLOs (phase 12 section 59-60) —
    // tighten once a real run against realistic data gives a baseline.
    http_req_duration: ['p(95)<2000'],
    http_req_failed: ['rate<0.01'],
  },
};

function login() {
  // accounts/urls.py: path("auth/login/", ...) — a TokenObtainPairView
  // subclass with throttle_scope="auth" (accounts/views.py). Logging in once
  // per VU at test start, not per iteration, respects that throttle rather
  // than testing it.
  const res = http.post(
    `${BASE_URL}/api/v1/auth/login/`,
    JSON.stringify({ email: EMAIL, password: PASSWORD }),
    { headers: { 'Content-Type': 'application/json' } },
  );
  check(res, { 'login succeeded': (r) => r.status === 200 });
  return res.json('access');
}

export function setup() {
  if (!EMAIL || !PASSWORD || !ORG_ID) {
    throw new Error('Set EMAIL, PASSWORD, and ORG_ID env vars (see README.md) — never hardcode credentials in this file.');
  }
  return { token: login() };
}

const READ_ENDPOINTS = [
  '/api/v1/sales/customers/',
  '/api/v1/sales/invoices/',
  '/api/v1/purchases/bills/',
  '/api/v1/reports/profit-loss/',
  '/api/v1/reports/balance-sheet/',
  '/api/v1/reports/trial-balance/',
  '/api/v1/reports/receivables/ageing/',
  '/api/v1/reports/payables/ageing/',
];

export default function (data) {
  const headers = {
    Authorization: `Bearer ${data.token}`,
    'X-Organization-Id': ORG_ID,
  };

  for (const path of READ_ENDPOINTS) {
    const res = http.get(`${BASE_URL}${path}`, { headers });
    check(res, { [`${path} => 200`]: (r) => r.status === 200 });
  }

  sleep(1);
}
