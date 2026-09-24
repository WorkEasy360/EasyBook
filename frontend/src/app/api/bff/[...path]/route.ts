import { NextResponse } from "next/server";
import { resolveBffPath } from "@/lib/api/bff-path";
import { callUpstream, decodeJson, type UpstreamResponse } from "@/lib/api/upstream";
import { DEFAULT_TIMEOUT_MS, REPORT_TIMEOUT_MS } from "@/lib/api/config";
import { refreshTokens } from "@/lib/auth/refresh";
import {
  ACCESS_COOKIE,
  ORG_COOKIE,
  ORG_MAX_AGE_SECONDS,
  REFRESH_COOKIE,
  cookieSecurity,
} from "@/lib/auth/session";
import { clientIpFromHeaders } from "@/lib/security/client-ip";
import {
  BodyTooLargeError,
  DEFAULT_MAX_BODY_BYTES,
  isForeignOriginWrite,
  readBodyWithLimit,
} from "@/lib/security/request-guards";

/**
 * Backend-for-frontend proxy: the ONLY way the browser reaches Django.
 *
 * Why a proxy rather than calling Django directly from the client:
 *  - The access/refresh tokens stay in httpOnly cookies. No JS on the page
 *    can read them, so an XSS cannot lift a session (spec §80).
 *  - Requests are same-origin, so there is no CORS preflight on every call
 *    and no need to relax the production CSP's connect-src (spec §81).
 *  - Token rotation is handled in one place. A 401 is refreshed and retried
 *    here, invisibly, instead of in every feature's data layer.
 *
 * What it deliberately does NOT do: interpret bodies, or add business logic.
 * It forwards, authenticates, and normalizes failures.
 */

/** Client headers that may cross to Django. Everything else is dropped. */
const FORWARDABLE_REQUEST_HEADERS = new Set([
  "content-type",
  "accept",
  // Critical writes carry this so a retried POST cannot double-post
  // (backend/core/idempotency.py, spec §73).
  "idempotency-key",
  // Log correlation across the web tier and the API (core/middleware.py).
  "x-request-id",
]);

/**
 * Response headers worth passing back. Content-Disposition matters because
 * the CSV report exports (`?export=csv`) are downloads, not JSON.
 */
const FORWARDABLE_RESPONSE_HEADERS = [
  "content-type",
  "content-disposition",
  "content-length",
  "x-request-id",
  "retry-after",
];

function failure(status: number, code: string, message: string): NextResponse {
  return NextResponse.json(
    { error: { code, message, details: null, request_id: crypto.randomUUID() } },
    { status, headers: { "Cache-Control": "no-store" } },
  );
}

function unauthorized(message: string): NextResponse {
  return NextResponse.json(
    {
      error: {
        code: "not_authenticated",
        message,
        details: null,
        request_id: crypto.randomUUID(),
      },
    },
    { status: 401 },
  );
}

/**
 * Rebuilds the Django path from the catch-all segments (src/lib/api/bff-path.ts).
 * An unsafe path is refused before any session cookie is read, so no token is
 * ever attached to it. The previous check rejected only literal "."/".." and
 * separators, and let ".%2e" through — which URL parsing then turned into
 * "..", reaching /admin/ with the user's bearer token.
 */
const resolvePath = resolveBffPath;

function buildUpstreamHeaders(request: Request, accessToken: string, organizationId: string | null): Headers {
  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (FORWARDABLE_REQUEST_HEADERS.has(key.toLowerCase())) headers.set(key, value);
  });

  // Set last and unconditionally: a client-supplied Authorization or
  // X-Organization-Id must never be able to override the session's own.
  headers.set("Authorization", `Bearer ${accessToken}`);
  if (organizationId) headers.set("X-Organization-Id", organizationId);
  return headers;
}

function toNextResponse(upstream: UpstreamResponse): NextResponse {
  const headers = new Headers();
  for (const name of FORWARDABLE_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }
  // Nothing from the API is cacheable by a shared cache: every response is
  // tenant- and user-specific.
  headers.set("Cache-Control", "no-store");
  // A null-body status must be constructed with a null body: the Response
  // constructor THROWS for 204/205/304 given any body, even an empty buffer,
  // which turned every successful DELETE into a 500 here while Django had
  // already committed it.
  const body = NULL_BODY_STATUSES.has(upstream.status) ? null : upstream.body;
  return new NextResponse(body, { status: upstream.status, headers });
}

/** Fetch spec "null body status" codes. */
const NULL_BODY_STATUSES = new Set([101, 103, 204, 205, 304]);

function maxBodyBytes(): number {
  const configured = Number(process.env.BFF_MAX_BODY_BYTES);
  return Number.isFinite(configured) && configured > 0 ? configured : DEFAULT_MAX_BODY_BYTES;
}

/** Reports and CSV exports are legitimately slower than a CRUD call. */
function timeoutFor(path: string): number {
  return path.startsWith("/reports/") ? REPORT_TIMEOUT_MS : DEFAULT_TIMEOUT_MS;
}

async function proxy(
  request: Request,
  context: { params: Promise<{ path: string[] }> },
): Promise<NextResponse> {
  const { path: segments } = await context.params;
  const path = resolvePath(segments);
  if (!path) {
    return NextResponse.json(
      { error: { code: "not_found", message: "Unknown endpoint.", details: null, request_id: crypto.randomUUID() } },
      { status: 404 },
    );
  }

  // A write that a browser reports as coming from another origin is refused
  // before the session is even read (src/lib/security/request-guards.ts).
  if (isForeignOriginWrite(request.method, request.headers)) {
    return failure(403, "cross_origin_request", "This request did not come from EasyBook.");
  }

  // Read cookies off the request rather than next/headers: this handler may
  // need to REPLACE them after a rotation, and the rotated pair has to ride
  // back on this same response.
  const accessToken = readCookie(request, ACCESS_COOKIE);
  const refreshToken = readCookie(request, REFRESH_COOKIE);
  const storedOrganizationId = readCookie(request, ORG_COOKIE);

  if (!accessToken || !refreshToken) {
    return unauthorized("Your session has expired. Please sign in again.");
  }

  // Django is reached server-to-server, so without this every browser user
  // counts as one client against its rate limits.
  const clientIp = clientIpFromHeaders(request.headers);

  // No organization cookie (the sign-in lookup failed, or it was cleared):
  // resolve the first organization the user belongs to — the same fallback
  // requireSession() uses for the page — and persist it on this response, so
  // the browser and the rendered page agree on the tenant.
  const organizationId = storedOrganizationId ?? (await firstOrganizationId(accessToken, clientIp));

  const search = new URL(request.url).searchParams.toString();
  const method = request.method.toUpperCase();
  // Buffered rather than streamed (Node fetch needs `duplex: "half"` to
  // stream a request body), so the buffer is capped: the largest thing any
  // endpoint accepts is a document upload (documents/services/validation.py,
  // 25 MiB for a PDF). Anything bigger is refused here instead of being held
  // in this process's memory first.
  let body: ArrayBuffer | null = null;
  if (method !== "GET" && method !== "HEAD") {
    try {
      body = await readBodyWithLimit(request, maxBodyBytes());
    } catch (error) {
      if (error instanceof BodyTooLargeError) {
        return failure(413, "request_too_large", "That upload is too large.");
      }
      throw error;
    }
  }
  const timeoutMs = timeoutFor(path);

  let upstream = await callUpstream({
    path,
    method,
    search,
    body,
    headers: buildUpstreamHeaders(request, accessToken, organizationId),
    timeoutMs,
    clientIp,
    cache: "no-store",
  });

  if (upstream.status !== 401) {
    const response = toNextResponse(upstream);
    if (!storedOrganizationId && organizationId) rememberOrganization(response, organizationId);
    return response;
  }

  // 401: the access token expired mid-session. Refresh once and replay.
  const rotated = await refreshTokens(refreshToken, clientIp);
  if (!rotated) {
    const response = unauthorized("Your session has expired. Please sign in again.");
    const base = cookieSecurity();
    response.cookies.set(ACCESS_COOKIE, "", { ...base, maxAge: 0 });
    response.cookies.set(REFRESH_COOKIE, "", { ...base, maxAge: 0 });
    return response;
  }

  upstream = await callUpstream({
    path,
    method,
    search,
    body,
    headers: buildUpstreamHeaders(request, rotated.access, organizationId),
    timeoutMs,
    clientIp,
    cache: "no-store",
  });

  const response = toNextResponse(upstream);
  const base = cookieSecurity();
  const maxAge = 7 * 24 * 60 * 60;
  response.cookies.set(ACCESS_COOKIE, rotated.access, { ...base, maxAge });
  response.cookies.set(REFRESH_COOKIE, rotated.refresh, { ...base, maxAge });
  if (!storedOrganizationId && organizationId) rememberOrganization(response, organizationId);
  return response;
}

async function firstOrganizationId(accessToken: string, clientIp: string | null): Promise<string | null> {
  const response = await callUpstream({
    path: "/organizations/",
    headers: { Authorization: `Bearer ${accessToken}`, Accept: "application/json" },
    clientIp,
    cache: "no-store",
  });
  if (response.status !== 200) return null;
  const organizations = decodeJson(response) as Array<{ id: string }> | null;
  return organizations?.[0]?.id ?? null;
}

function rememberOrganization(response: NextResponse, organizationId: string): void {
  response.cookies.set(ORG_COOKIE, organizationId, { ...cookieSecurity(), maxAge: ORG_MAX_AGE_SECONDS });
}

function readCookie(request: Request, name: string): string | null {
  const header = request.headers.get("cookie");
  if (!header) return null;
  for (const part of header.split(";")) {
    const index = part.indexOf("=");
    if (index === -1) continue;
    if (part.slice(0, index).trim() === name) {
      return decodeURIComponent(part.slice(index + 1).trim());
    }
  }
  return null;
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;

// Session cookies make every response user-specific; nothing here is static.
export const dynamic = "force-dynamic";
