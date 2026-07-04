"use client";

import { MessageSquareTextIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";

import { Tooltip } from "../tooltip";

import { useMaybeSidecar } from "./context";

export function SidecarTrigger() {
  const { t } = useI18n();
  const sidecar = useMaybeSidecar();

  if (!sidecar?.sidecarThreadId) {
    return null;
  }

  const label = sidecar.open ? t.sidecar.close : t.sidecar.open;

  return (
    <Tooltip content={label}>
      <Button
        aria-label={label}
        className="text-muted-foreground hover:text-foreground"
        data-testid="sidecar-header-trigger"
        size="icon"
        type="button"
        variant={sidecar.open ? "secondary" : "ghost"}
        onClick={() => {
          if (sidecar.open) {
            sidecar.close();
            return;
          }
          sidecar.openSidecar();
        }}
      >
        <MessageSquareTextIcon />
      </Button>
    </Tooltip>
  );
}
