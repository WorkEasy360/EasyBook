/**
 * Inline icon set.
 *
 * Hand-drawn on a 24px grid rather than pulled from an icon package: the app
 * needs about twenty glyphs, and a dependency for that would add a barrel
 * import to every route for no benefit (root CLAUDE.md rule 8). All are
 * stroke-based so they inherit currentColor and stay legible at 16px.
 *
 * Icons are decorative here — every one is paired with a text label, so they
 * are hidden from assistive technology.
 */

interface IconProps {
  className?: string;
}

function Svg({ className, children }: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      className={className ?? "size-4"}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

export const Icons = {
  dashboard: (props: IconProps) => (
    <Svg {...props}>
      <rect x="3" y="3" width="7" height="8" rx="1.5" />
      <rect x="14" y="3" width="7" height="5" rx="1.5" />
      <rect x="14" y="11" width="7" height="10" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" />
    </Svg>
  ),
  items: (props: IconProps) => (
    <Svg {...props}>
      <path d="M21 8.5 12 3 3 8.5v7L12 21l9-5.5v-7Z" />
      <path d="m3 8.5 9 5.5 9-5.5" />
      <path d="M12 14v7" />
    </Svg>
  ),
  sales: (props: IconProps) => (
    <Svg {...props}>
      <path d="M4 3h13l3 4v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V4a1 1 0 0 1 0-1Z" />
      <path d="M8 11h8M8 15h5" />
    </Svg>
  ),
  purchases: (props: IconProps) => (
    <Svg {...props}>
      <path d="M3 5h2l2.2 10.2a2 2 0 0 0 2 1.6h7.4a2 2 0 0 0 2-1.5L20 8H6.5" />
      <circle cx="10" cy="20" r="1.2" />
      <circle cx="17" cy="20" r="1.2" />
    </Svg>
  ),
  time: (props: IconProps) => (
    <Svg {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3.2 2" />
    </Svg>
  ),
  banking: (props: IconProps) => (
    <Svg {...props}>
      <path d="M3 9.5 12 4l9 5.5" />
      <path d="M5 10v8M10 10v8M14 10v8M19 10v8" />
      <path d="M3 21h18" />
    </Svg>
  ),
  accounting: (props: IconProps) => (
    <Svg {...props}>
      <rect x="4" y="3" width="16" height="18" rx="2" />
      <path d="M8 7h8M8 11h3M8 15h3M15 11v5M13 13.5h4" />
    </Svg>
  ),
  tax: (props: IconProps) => (
    <Svg {...props}>
      <path d="M5 3h14v18l-3.5-2-3.5 2-3.5-2L5 21Z" />
      <path d="M9.5 9.5 14.5 14.5M14.5 9.5 9.5 14.5" />
    </Svg>
  ),
  reports: (props: IconProps) => (
    <Svg {...props}>
      <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />
    </Svg>
  ),
  documents: (props: IconProps) => (
    <Svg {...props}>
      <path d="M14 3H7a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V7Z" />
      <path d="M14 3v4h4" />
      <path d="M9 13h6M9 17h4" />
    </Svg>
  ),
  automation: (props: IconProps) => (
    <Svg {...props}>
      <path d="M13 2 4 14h7l-1 8 9-12h-7Z" />
    </Svg>
  ),
  ai: (props: IconProps) => (
    <Svg {...props}>
      <path d="M12 3a4 4 0 0 1 4 4v1a4 4 0 0 1 0 8v1a4 4 0 0 1-8 0v-1a4 4 0 0 1 0-8V7a4 4 0 0 1 4-4Z" />
      <path d="M12 3v18" />
    </Svg>
  ),
  settings: (props: IconProps) => (
    <Svg {...props}>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v3M12 19v3M22 12h-3M5 12H2M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1M18.4 18.4l-2.1-2.1M7.7 7.7 5.6 5.6" />
    </Svg>
  ),
  chevronDown: (props: IconProps) => (
    <Svg {...props}>
      <path d="m6 9 6 6 6-6" />
    </Svg>
  ),
  chevronRight: (props: IconProps) => (
    <Svg {...props}>
      <path d="m9 6 6 6-6 6" />
    </Svg>
  ),
  menu: (props: IconProps) => (
    <Svg {...props}>
      <path d="M4 6h16M4 12h16M4 18h16" />
    </Svg>
  ),
  close: (props: IconProps) => (
    <Svg {...props}>
      <path d="m6 6 12 12M18 6 6 18" />
    </Svg>
  ),
  search: (props: IconProps) => (
    <Svg {...props}>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </Svg>
  ),
  plus: (props: IconProps) => (
    <Svg {...props}>
      <path d="M12 5v14M5 12h14" />
    </Svg>
  ),
  download: (props: IconProps) => (
    <Svg {...props}>
      <path d="M12 3v12M7 11l5 5 5-5" />
      <path d="M4 20h16" />
    </Svg>
  ),
  external: (props: IconProps) => (
    <Svg {...props}>
      <path d="M14 4h6v6" />
      <path d="M20 4 11 13" />
      <path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />
    </Svg>
  ),
  filter: (props: IconProps) => (
    <Svg {...props}>
      <path d="M3 5h18l-7 8v6l-4 2v-8Z" />
    </Svg>
  ),
  check: (props: IconProps) => (
    <Svg {...props}>
      <path d="m5 12.5 4.5 4.5L19 7" />
    </Svg>
  ),
  warning: (props: IconProps) => (
    <Svg {...props}>
      <path d="M12 4 2.5 20h19Z" />
      <path d="M12 10v4M12 17.2v.1" />
    </Svg>
  ),
  inbox: (props: IconProps) => (
    <Svg {...props}>
      <path d="M3 13h5l1.5 3h5L16 13h5" />
      <path d="M5.5 5h13l2.5 8v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-6Z" />
    </Svg>
  ),
  logout: (props: IconProps) => (
    <Svg {...props}>
      <path d="M10 4H6a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h4" />
      <path d="M15 8.5 18.5 12 15 15.5" />
      <path d="M18.5 12H10" />
    </Svg>
  ),
} as const;
