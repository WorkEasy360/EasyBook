import { redirect } from "next/navigation";

/**
 * The app has no marketing surface — "/" is a signpost. Middleware has
 * already sent anyone without a session to /login (or, when EasyBook is
 * SSO-only, on to Workeasy360 sign-in) before this renders.
 */
export default function RootPage() {
  redirect("/dashboard");
}
