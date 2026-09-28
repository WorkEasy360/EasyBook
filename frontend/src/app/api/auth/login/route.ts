import type { NextResponse } from "next/server";
import { clientIpFromHeaders } from "@/lib/security/client-ip";
import { errorEnvelope, refuseForeignOrigin, signInWithPassword } from "@/lib/auth/sign-in";

/** Exchanges credentials for a cookie session (src/lib/auth/sign-in.ts). */
export async function POST(request: Request): Promise<NextResponse> {
  const refused = refuseForeignOrigin(request);
  if (refused) return refused;

  let credentials: { email?: unknown; password?: unknown };
  try {
    credentials = (await request.json()) as typeof credentials;
  } catch {
    return errorEnvelope("parse_error", "Invalid request.", 400);
  }

  const email = typeof credentials.email === "string" ? credentials.email.trim() : "";
  const password = typeof credentials.password === "string" ? credentials.password : "";

  if (!email || !password) {
    return errorEnvelope("invalid", "Enter your email address and password.", 400);
  }

  return signInWithPassword(email, password, clientIpFromHeaders(request.headers));
}

export const dynamic = "force-dynamic";
