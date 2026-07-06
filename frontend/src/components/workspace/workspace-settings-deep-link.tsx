"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { SettingsDialog, type SettingsSection } from "./settings";

const SETTINGS_SECTIONS = new Set<SettingsSection>([
  "account",
  "appearance",
  "channels",
  "integrations",
  "memory",
  "tools",
  "skills",
  "notification",
  "about",
]);

function asSettingsSection(value: string | null): SettingsSection | null {
  if (!value) return null;
  return SETTINGS_SECTIONS.has(value as SettingsSection)
    ? (value as SettingsSection)
    : null;
}

export function WorkspaceSettingsDeepLink() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [open, setOpen] = useState(false);
  const [section, setSection] = useState<SettingsSection>("appearance");

  useEffect(() => {
    const nextSection = asSettingsSection(searchParams.get("settings"));
    if (nextSection) {
      setSection(nextSection);
      setOpen(true);
    }
  }, [searchParams]);

  const handleOpenChange = (nextOpen: boolean) => {
    setOpen(nextOpen);
    if (!nextOpen && searchParams.has("settings")) {
      const next = new URLSearchParams(searchParams);
      next.delete("settings");
      const suffix = next.toString();
      router.replace(suffix ? `${pathname}?${suffix}` : pathname, {
        scroll: false,
      });
    }
  };

  return (
    <SettingsDialog
      open={open}
      onOpenChange={handleOpenChange}
      defaultSection={section}
    />
  );
}
