import { redirect } from "next/navigation";

/**
 * The app has no marketing surface — "/" is a signpost. Middleware has
 * already sent anyone without a session to /login before this renders.
 */
export default function RootPage() {
  redirect("/dashboard");
}
