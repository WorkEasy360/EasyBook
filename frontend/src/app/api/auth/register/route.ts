import { NextResponse } from "next/server";
import { callUpstream } from "@/lib/api/upstream";
import { clientIpFromHeaders } from "@/lib/security/client-ip";
import { errorEnvelope, refuseForeignOrigin, relayUpstreamError, signInWithPassword } from "@/lib/auth/sign-in";

/**
 * Creates an account (POST /auth/register/), then signs the new user in so
 * they land straight on organization setup.
 *
 * The backend is the authority on every rule: password validators, email
 * uniqueness, field lengths. Its field errors are relayed unchanged so the
 * form can attach them to the right inputs.
 */
export async function POST(request: Request): Promise<NextResponse> {
  const refused = refuseForeignOrigin(request);
  if (refused) return refused;

  let input: { email?: unknown; password?: unknown; first_name?: unknown; last_name?: unknown };
  try {
    input = (await request.json()) as typeof input;
  } catch {
    return errorEnvelope("parse_error", "Invalid request.", 400);
  }

  const email = typeof input.email === "string" ? input.email.trim() : "";
  const password = typeof input.password === "string" ? input.password : "";
  if (!email || !password) {
    return errorEnvelope("invalid", "Enter your email address and a password.", 400);
  }

  const clientIp = clientIpFromHeaders(request.headers);
  const upstream = await callUpstream({
    path: "/auth/register/",
    clientIp,
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({
      email,
      password,
      first_name: typeof input.first_name === "string" ? input.first_name.trim() : "",
      last_name: typeof input.last_name === "string" ? input.last_name.trim() : "",
    }),
  });

  if (upstream.status !== 201) {
    if (upstream.status === 429) {
      // Shares the backend's "auth" throttle scope with sign-in.
      return errorEnvelope("throttled", "Too many attempts. Please wait a minute and try again.", 429);
    }
    return relayUpstreamError(upstream);
  }

  const session = await signInWithPassword(email, password, clientIp);
  if (session.ok) return session;

  // The account exists even though the automatic sign-in did not complete
  // (throttled, or the backend blipped). Reporting a failure here would
  // invite a second sign-up that can only fail as a duplicate.
  return NextResponse.json({ redirectTo: "/login?registered=1" }, { status: 201 });
}

export const dynamic = "force-dynamic";
