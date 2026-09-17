import type { NavIcon as NavIconName } from "@/lib/navigation";
import { Icons } from "./icons";

/**
 * Renders a navigation glyph by name.
 *
 * A component rather than `const Icon = Icons[name]` at the call site: looking
 * up a component type during render gives React a new element type whenever
 * the lookup result changes, which remounts the subtree instead of updating it
 * (react-hooks/static-components). Doing the switch inside one stable
 * component keeps the element type constant.
 */
export function NavIcon({ name, className }: { name: NavIconName; className?: string }) {
  const Glyph = Icons[name];
  return <Glyph className={className} />;
}
