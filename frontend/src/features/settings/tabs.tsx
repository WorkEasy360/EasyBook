import { DetailTabs } from "@/components/ui/detail";

/** Settings sub-views as link tabs. Every role may open each of them. */
export function SettingsTabs() {
  return (
    <DetailTabs
      tabs={[
        { label: "Organization", href: "/settings" },
        { label: "Members", href: "/settings/members" },
        { label: "Your profile", href: "/settings/profile" },
      ]}
    />
  );
}
