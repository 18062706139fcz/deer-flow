"use client";

import {
  CheckCircle2Icon,
  CopyIcon,
  ExternalLinkIcon,
  PlugZapIcon,
  RefreshCwIcon,
  XCircleIcon,
} from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useAuth } from "@/core/auth/AuthProvider";
import { useI18n } from "@/core/i18n/hooks";
import {
  LarkIntegrationRequestError,
  type LarkAuthStartResponse,
  type LarkConfigStartResponse,
  useCompleteLarkAuthorization,
  useCompleteLarkConfiguration,
  useInstallLarkIntegration,
  useLarkIntegrationStatus,
  useStartLarkAuthorization,
  useStartLarkConfiguration,
} from "@/core/integrations/lark";
import { env } from "@/env";

import { SettingsSection } from "./settings-section";

type PendingLarkFlow =
  | ({ kind: "config" } & LarkConfigStartResponse)
  | ({ kind: "auth" } & LarkAuthStartResponse);

export function IntegrationsSettingsPage() {
  const { t } = useI18n();
  return (
    <SettingsSection
      title={t.settings.integrations.title}
      description={t.settings.integrations.description}
    >
      <LarkIntegrationCard />
    </SettingsSection>
  );
}

function LarkIntegrationCard() {
  const { t } = useI18n();
  const { user } = useAuth();
  const isAdmin = user?.system_role === "admin";
  const { data, isLoading, error, refetch, isFetching } =
    useLarkIntegrationStatus();
  const install = useInstallLarkIntegration();
  const startConfig = useStartLarkConfiguration();
  const completeConfig = useCompleteLarkConfiguration();
  const startAuth = useStartLarkAuthorization();
  const completeAuth = useCompleteLarkAuthorization();
  const [pendingFlow, setPendingFlow] = useState<PendingLarkFlow | null>(null);
  const browserWindowRef = useRef<Window | null>(null);
  const connectBusy =
    startConfig.isPending || completeConfig.isPending || startAuth.isPending;

  const handleInstall = () => {
    install.mutate(undefined, {
      onSuccess: (result) => toast.success(result.message),
      onError: (err) => {
        if (err instanceof LarkIntegrationRequestError && err.isAdminRequired) {
          toast.error(t.settings.integrations.adminRequired);
          return;
        }
        toast.error(err instanceof Error ? err.message : String(err));
      },
    });
  };

  const openPendingBrowserWindow = () => {
    const browserWindow = window.open("about:blank", "_blank");
    if (browserWindow) {
      browserWindow.opener = null;
      browserWindowRef.current = browserWindow;
    }
    return browserWindow;
  };

  const closePendingBrowserWindow = (browserWindow: Window | null) => {
    if (!browserWindow) return;
    browserWindow.close();
    if (browserWindowRef.current === browserWindow) {
      browserWindowRef.current = null;
    }
  };

  const openAuthorizationUrl = (
    url: string,
    browserWindow = browserWindowRef.current,
  ) => {
    if (browserWindow && !browserWindow.closed) {
      browserWindow.location.href = url;
      browserWindowRef.current = browserWindow;
      return;
    }
    browserWindowRef.current = window.open(
      url,
      "_blank",
      "noopener,noreferrer",
    );
  };

  const startUserAuth = (browserWindow = browserWindowRef.current) => {
    startAuth.mutate(
      { recommend: true },
      {
        onSuccess: (result) => {
          setPendingFlow({ kind: "auth", ...result });
          openAuthorizationUrl(result.verification_url, browserWindow);
          toast.success(t.settings.integrations.lark.authStarted);
        },
        onError: (err) => {
          closePendingBrowserWindow(browserWindow);
          toast.error(err instanceof Error ? err.message : String(err));
        },
      },
    );
  };

  const handleContinueConnection = () => {
    if (!pendingFlow || pendingFlow.kind !== "config") return;
    completeConfig.mutate(
      {
        device_code: pendingFlow.device_code,
        brand: pendingFlow.brand,
        interval: pendingFlow.interval,
        expires_in: pendingFlow.expires_in,
      },
      {
        onSuccess: () => {
          toast.success(t.settings.integrations.lark.connectionReady);
          setPendingFlow(null);
          startUserAuth(browserWindowRef.current);
        },
        onError: (err) => {
          setPendingFlow(null);
          toast.error(err instanceof Error ? err.message : String(err));
        },
      },
    );
  };

  const handleConnect = () => {
    const browserWindow = openPendingBrowserWindow();
    if (!data?.app_configured) {
      startConfig.mutate(
        { brand: "feishu" },
        {
          onSuccess: (result) => {
            setPendingFlow({ kind: "config", ...result });
            openAuthorizationUrl(result.verification_url, browserWindow);
            toast.success(t.settings.integrations.lark.connectionStarted);
          },
          onError: (err) => {
            closePendingBrowserWindow(browserWindow);
            toast.error(err instanceof Error ? err.message : String(err));
          },
        },
      );
      return;
    }
    startUserAuth(browserWindow);
  };

  const handleCompleteAuth = () => {
    if (!pendingFlow) return;
    if (pendingFlow.kind !== "auth") {
      return;
    }
    completeAuth.mutate(
      { device_code: pendingFlow.device_code },
      {
        onSuccess: (result) => {
          toast.success(result.message);
          setPendingFlow(null);
          browserWindowRef.current = null;
        },
        onError: (err) =>
          toast.error(err instanceof Error ? err.message : String(err)),
      },
    );
  };

  const handleCopyAuthLink = async () => {
    if (!pendingFlow) return;
    try {
      await navigator.clipboard.writeText(pendingFlow.verification_url);
      toast.success(t.clipboard.copiedToClipboard);
    } catch {
      toast.error(t.clipboard.failedToCopyToClipboard);
    }
  };

  const installDisabled =
    env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" ||
    !isAdmin ||
    install.isPending;
  const authDisabled =
    env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" ||
    !data?.installed ||
    !data?.cli.available ||
    connectBusy;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-3">
          <div className="bg-primary/10 text-primary rounded-lg p-2">
            <PlugZapIcon className="size-5" />
          </div>
          <div>
            <CardTitle>{t.settings.integrations.lark.title}</CardTitle>
            <CardDescription>
              {t.settings.integrations.lark.description}
            </CardDescription>
          </div>
        </div>
        <CardAction>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void refetch()}
            disabled={isFetching}
          >
            <RefreshCwIcon className="size-4" />
            {t.settings.integrations.refresh}
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoading ? (
          <div className="text-muted-foreground text-sm">
            {t.common.loading}
          </div>
        ) : error ? (
          <Alert variant="destructive">
            <XCircleIcon />
            <AlertTitle>{t.settings.integrations.loadFailed}</AlertTitle>
            <AlertDescription>
              {error instanceof Error ? error.message : String(error)}
            </AlertDescription>
          </Alert>
        ) : data ? (
          <>
            <div className="grid gap-3 md:grid-cols-3">
              <StatusItem
                label={t.settings.integrations.lark.skillPack}
                ok={data.installed}
                value={
                  data.installed
                    ? t.settings.integrations.lark.skillsInstalled(
                        data.skills_installed,
                        data.skills_expected,
                      )
                    : t.settings.integrations.lark.notInstalled
                }
              />
              <StatusItem
                label={t.settings.integrations.lark.gatewayCli}
                ok={data.cli.available}
                value={
                  data.cli.available
                    ? (data.cli.version ?? t.settings.integrations.available)
                    : (data.cli.error ?? t.settings.integrations.unavailable)
                }
              />
              <StatusItem
                label={t.settings.integrations.lark.auth}
                ok={data.auth.status === "authenticated"}
                value={
                  data.auth.status === "authenticated"
                    ? (data.auth.user ?? t.settings.integrations.connected)
                    : t.settings.integrations.lark.authNotConfigured
                }
              />
            </div>
            <IntegrationNextStep
              installed={data.installed}
              cliReady={data.cli.available}
            />
            <div className="flex flex-wrap items-center gap-2">
              <Button onClick={handleInstall} disabled={installDisabled}>
                {install.isPending
                  ? t.settings.integrations.installing
                  : data.installed
                    ? t.settings.integrations.reinstall
                    : t.settings.integrations.install}
              </Button>
              <Button
                variant="outline"
                onClick={handleConnect}
                disabled={authDisabled}
              >
                {connectBusy
                  ? t.settings.integrations.lark.authStarting
                  : t.settings.integrations.lark.connect}
              </Button>
              {!isAdmin && (
                <span className="text-muted-foreground text-sm">
                  {t.settings.integrations.adminRequired}
                </span>
              )}
            </div>
            {pendingFlow && (
              <Alert>
                <ExternalLinkIcon />
                <AlertTitle>
                  {pendingFlow.kind === "config"
                    ? t.settings.integrations.lark.openConnectionLinkTitle
                    : t.settings.integrations.lark.openAuthLinkTitle}
                </AlertTitle>
                <AlertDescription>
                  <div className="space-y-3">
                    <p>
                      {pendingFlow.kind === "config"
                        ? t.settings.integrations.lark
                            .openConnectionLinkDescription
                        : t.settings.integrations.lark.openAuthLinkDescription}
                    </p>
                    <div className="bg-muted text-foreground rounded-md px-3 py-2 text-xs break-all">
                      {pendingFlow.verification_url}
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button size="sm" asChild>
                        <a
                          href={pendingFlow.verification_url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          <ExternalLinkIcon className="size-4" />
                          {t.settings.integrations.lark.openAuthLink}
                        </a>
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => void handleCopyAuthLink()}
                      >
                        <CopyIcon className="size-4" />
                        {t.settings.integrations.lark.copyAuthLink}
                      </Button>
                      {pendingFlow.kind === "config" ? (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={handleContinueConnection}
                          disabled={completeConfig.isPending}
                        >
                          {completeConfig.isPending ? (
                            <RefreshCwIcon className="size-4 animate-spin" />
                          ) : null}
                          {completeConfig.isPending
                            ? t.settings.integrations.lark
                                .preparingAuthorization
                            : t.settings.integrations.lark.continueAuth}
                        </Button>
                      ) : (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={handleCompleteAuth}
                          disabled={completeAuth.isPending}
                        >
                          {completeAuth.isPending
                            ? t.settings.integrations.lark.completingAuth
                            : t.settings.integrations.lark.completeAuth}
                        </Button>
                      )}
                    </div>
                    {pendingFlow.expires_in != null && (
                      <p className="text-muted-foreground text-xs">
                        {t.settings.integrations.lark.authExpiresIn(
                          pendingFlow.expires_in,
                        )}
                      </p>
                    )}
                  </div>
                </AlertDescription>
              </Alert>
            )}
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}

function StatusItem({
  label,
  ok,
  value,
}: {
  label: string;
  ok: boolean;
  value: string;
}) {
  const { t } = useI18n();
  return (
    <div className="rounded-lg border p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="text-sm font-medium">{label}</div>
        <Badge variant={ok ? "secondary" : "outline"}>
          {ok ? (
            <CheckCircle2Icon className="size-3" />
          ) : (
            <XCircleIcon className="size-3" />
          )}
          {ok ? t.settings.integrations.ready : t.settings.integrations.pending}
        </Badge>
      </div>
      <div className="text-muted-foreground text-sm break-words">{value}</div>
    </div>
  );
}

function IntegrationNextStep({
  installed,
  cliReady,
}: {
  installed: boolean;
  cliReady: boolean;
}) {
  const { t } = useI18n();
  if (!installed) {
    return (
      <Alert>
        <AlertTitle>{t.settings.integrations.lark.installNextTitle}</AlertTitle>
        <AlertDescription>
          {t.settings.integrations.lark.installNextDescription}
        </AlertDescription>
      </Alert>
    );
  }
  if (!cliReady) {
    return (
      <Alert>
        <AlertTitle>{t.settings.integrations.lark.cliNextTitle}</AlertTitle>
        <AlertDescription>
          {t.settings.integrations.lark.cliNextDescription}
        </AlertDescription>
      </Alert>
    );
  }
  return (
    <Alert>
      <ExternalLinkIcon />
      <AlertTitle>{t.settings.integrations.lark.authNextTitle}</AlertTitle>
      <AlertDescription>
        {t.settings.integrations.lark.authNextDescription}
      </AlertDescription>
    </Alert>
  );
}
