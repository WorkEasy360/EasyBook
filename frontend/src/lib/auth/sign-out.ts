/**
 * Ends EasyBook's own session from the browser (POST /api/auth/logout, which
 * revokes the refresh token and clears the cookies). True only when the server
 * says it did.
 *
 * Callers navigate on only after a true: a sign-out that failed must not carry
 * on to /login (the session is still there, so the proxy would bounce straight
 * back) or, worse, on to "...and Workeasy360", ending the Workeasy360 session
 * while the person is still signed in here. They stay, and say so.
 */
export async function endEasyBookSession(): Promise<boolean> {
  try {
    const response = await fetch("/api/auth/logout", { method: "POST" });
    return response.ok;
  } catch {
    return false;
  }
}

export const SIGN_OUT_FAILED_MESSAGE = "Could not sign you out. Please try again.";
