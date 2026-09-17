/**
 * Minimal JWT payload reader.
 *
 * This decodes ONLY to schedule a refresh before the access token expires —
 * it deliberately does not verify the signature. Django verifies every token
 * on every request (SimpleJWT), and re-verifying here would mean shipping the
 * signing key to the web tier, which is exactly what we are avoiding. Treat
 * everything this returns as a hint, never as an authorization decision.
 */

export interface AccessTokenClaims {
  /** Seconds since epoch. */
  exp?: number;
  user_id?: string;
  token_type?: string;
}

export function decodeJwtPayload(token: string): AccessTokenClaims | null {
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  const payload = parts[1];
  if (!payload) return null;

  try {
    const normalized = payload.replace(/-/g, "+").replace(/_/g, "/");
    const padded = normalized.padEnd(normalized.length + ((4 - (normalized.length % 4)) % 4), "=");
    const json =
      typeof atob === "function"
        ? atob(padded)
        : Buffer.from(padded, "base64").toString("binary");
    const parsed: unknown = JSON.parse(json);
    if (typeof parsed !== "object" || parsed === null) return null;
    return parsed as AccessTokenClaims;
  } catch {
    return null;
  }
}

/**
 * True when the token is within `skewSeconds` of expiry. The skew exists so a
 * request that is about to be made does not race the expiry it just passed —
 * SimpleJWT's default access lifetime here is 15 minutes.
 */
export function isExpiringSoon(token: string, skewSeconds = 60): boolean {
  const claims = decodeJwtPayload(token);
  if (!claims?.exp) return true;
  const nowSeconds = Math.floor(Date.now() / 1000);
  return claims.exp - skewSeconds <= nowSeconds;
}

export function expiresAt(token: string): number | null {
  const claims = decodeJwtPayload(token);
  return claims?.exp ? claims.exp * 1000 : null;
}
