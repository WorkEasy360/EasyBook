import { NextResponse } from "next/server";
import { isForeignOriginWrite } from "@/lib/security/request-guards";
import { callUpstream, decodeJson } from "@/lib/api/upstream";
import { readSession, writeOrganizationId } from "@/lib/auth/session";
import type { Organization } from "@/types/api/accounts";

/**
 * Switches the active organization.
 *
 * The requested id is checked against the membership list the API itself
 * returns before it is stored, so a tampered request cannot pin the cookie to
 * a foreign tenant. Django would reject it regardless (resolve_membership
 * returns 403 organization_forbidden), but storing an unusable id would break
 * every subsequent page instead of failing here, visibly, once.
 */

function envelope(code: string, message: string, status: number): NextResponse {
  return NextResponse.json(
    { error: { code, message, details: null, request_id: crypto.randomUUID() } },
    { status },
  );
}

export async function POST(request: Request): Promise<NextResponse> {
  // Login CSRF (signing a victim into the attacker's account) and forced
  // organization switches are both refused when a browser says the request is
  // cross-origin (src/lib/security/request-guards.ts).
  if (isForeignOriginWrite(request.method, request.headers)) {
    return NextResponse.json(
      {
        error: {
          code: "cross_origin_request",
          message: "This request did not come from EasyBook.",
          details: null,
          request_id: crypto.randomUUID(),
        },
      },
      { status: 403 },
    );
  }

  const session = await readSession();
  if (!session) {
    return envelope("not_authenticated", "Your session has expired. Please sign in again.", 401);
  }

  let body: { organizationId?: unknown };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    body = {};
  }

  const organizationId = typeof body.organizationId === "string" ? body.organizationId : "";
  if (!organizationId) {
    return envelope("invalid", "No organization was selected.", 400);
  }

  const upstream = await callUpstream({
    path: "/organizations/",
    headers: { Authorization: `Bearer ${session.access}`, Accept: "application/json" },
    cache: "no-store",
  });
  const organizations =
    upstream.status === 200 ? ((decodeJson(upstream) as Organization[] | null) ?? []) : [];

  const match = organizations.find((organization) => organization.id === organizationId);
  if (!match) {
    return envelope("organization_forbidden", "You do not have access to that organization.", 403);
  }

  await writeOrganizationId(match.id);
  return NextResponse.json({
    ok: true,
    organization: { id: match.id, name: match.name, role: match.role },
  });
}

export const dynamic = "force-dynamic";
