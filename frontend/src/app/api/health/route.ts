import { NextResponse } from "next/server";

/**
 * Liveness for the frontend container: "this Node process can answer".
 * Deliberately touches nothing else — not Django, not cookies — so an API
 * outage never gets healthy frontend tasks replaced (the same rule the
 * backend's /api/v1/health/live/ follows). Outside src/proxy.ts's matcher.
 */
export function GET(): NextResponse {
  return NextResponse.json({ status: "ok" }, { headers: { "Cache-Control": "no-store" } });
}

export const dynamic = "force-dynamic";
