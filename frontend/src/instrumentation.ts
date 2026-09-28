/**
 * Runs once when the Next.js server starts. The production configuration
 * preflight is Node-only (src/instrumentation-node.ts); importing it only on
 * the Node runtime keeps Node APIs out of the Edge bundle.
 */
export async function register(): Promise<void> {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    const { runProductionPreflight } = await import("./instrumentation-node");
    runProductionPreflight();
  }
}
