import { cn } from "@/lib/cn";

/**
 * "Access EasyBook" (Workeasy360 sign-in): a real link, not a fetch. The sign-in is a
 * chain of top-level navigations (here → Workeasy360 → the callback), it must
 * work before hydration, and the CSP's connect-src would refuse a script
 * talking to another origin anyway.
 */
export function Workeasy360Button({ nextPath, primary }: { nextPath: string | null; primary: boolean }) {
  const href = nextPath
    ? `/api/auth/workeasy360/start?next=${encodeURIComponent(nextPath)}`
    : "/api/auth/workeasy360/start";
  return (
    <a
      href={href}
      className={cn(
        "inline-flex h-9 w-full items-center justify-center gap-2 rounded-md border px-3.5 text-sm font-medium transition-colors",
        primary
          ? "border-brand-700 bg-brand-700 text-white hover:bg-brand-800"
          : "border-ink-300 bg-white text-ink-800 hover:bg-ink-50",
      )}
    >
      Access EasyBook
    </a>
  );
}
