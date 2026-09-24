"use client";

import { cn } from "@/lib/cn";
import { Icons } from "@/components/ui/icons";
import { useTheme } from "@/components/providers/theme-provider";
import type { ThemePreference } from "@/lib/theme";

const OPTIONS: Array<{ value: ThemePreference; label: string; icon: typeof Icons.sun }> = [
  { value: "light", label: "Light", icon: Icons.sun },
  { value: "dark", label: "Dark", icon: Icons.moon },
  { value: "system", label: "System", icon: Icons.monitor },
];

/**
 * Light / dark / system. Three pressed-state buttons rather than a radio
 * group: each is its own Tab stop with no arrow-key contract to half-honour
 * (the same reasoning as the account menu that hosts it).
 */
export function ThemeToggle() {
  const { preference, setPreference } = useTheme();

  return (
    <div role="group" aria-label="Appearance" className="grid grid-cols-3 gap-0.5 rounded-md bg-ink-100 p-0.5">
      {OPTIONS.map((option) => {
        const selected = option.value === preference;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={selected}
            onClick={() => setPreference(option.value)}
            className={cn(
              "flex h-7 items-center justify-center gap-1.5 rounded text-xs font-medium transition-colors",
              selected ? "bg-brand-50 text-brand-800 shadow-xs" : "text-ink-600 hover:text-ink-900",
            )}
          >
            <option.icon className="size-3.5" />
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
