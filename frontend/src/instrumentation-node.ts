import { productionConfigProblems } from "@/lib/production-preflight";

/**
 * Node-runtime half of src/instrumentation.ts (kept separate so the Edge
 * bundle never contains process.exit). In production, a server missing
 * security-relevant configuration refuses to start instead of serving with
 * non-Secure session cookies or a shared rate-limit identity. Never during
 * `next build`, which has no runtime configuration by design (no secret is
 * baked into the image).
 */
export function runProductionPreflight(): void {
  if (process.env.NODE_ENV !== "production") return;
  if (process.env.NEXT_PHASE === "phase-production-build") return;

  const problems = productionConfigProblems(process.env);
  if (problems.length > 0) {
    console.error(JSON.stringify({ level: "ERROR", event: "frontend_preflight_failed", problems }));
    process.exit(1);
  }
}
