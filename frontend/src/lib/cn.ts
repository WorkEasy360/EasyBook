/**
 * Conditional className joiner.
 *
 * Deliberately not `clsx` + `tailwind-merge`: root CLAUDE.md rule 8 says add a
 * dependency only when nothing already available does the job. This covers
 * every case the components here need. It does NOT de-duplicate conflicting
 * Tailwind utilities, so variants are composed by choosing one class per
 * property rather than by layering and overriding — which is the clearer
 * pattern anyway.
 */
export type ClassValue =
  | string
  | number
  | bigint
  | null
  | undefined
  | false
  | ClassValue[]
  | Record<string, boolean | undefined | null>;

export function cn(...values: ClassValue[]): string {
  const out: string[] = [];

  const walk = (value: ClassValue): void => {
    if (!value && value !== 0) return;
    if (typeof value === "string" || typeof value === "number" || typeof value === "bigint") {
      out.push(String(value));
      return;
    }
    if (Array.isArray(value)) {
      for (const entry of value) walk(entry);
      return;
    }
    if (typeof value === "object") {
      for (const [key, enabled] of Object.entries(value)) {
        if (enabled) out.push(key);
      }
    }
  };

  for (const value of values) walk(value);
  return out.join(" ");
}
