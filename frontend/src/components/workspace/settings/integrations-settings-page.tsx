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
import { Input } from "@/components/ui/input";
import { useAuth } from "@/core/auth/AuthProvider";
import { useI18n } from "@/core/i18n/hooks";
import {
  LarkIntegrationRequestError,
  type LarkAuthStartRequest,
  type LarkAuthStartResponse,
  type LarkConfigStartResponse,
  type LarkIntegrationStatus,
  useCompleteLarkAuthorization,
  useCompleteLarkConfiguration,
  useInstallLarkIntegration,
  useLarkIntegrationStatus,
  useStartLarkAuthorization,
  useStartLarkConfiguration,
} from "@/core/integrations/lark";
import { env } from "@/env";
import { cn } from "@/lib/utils";

import { SettingsSection } from "./settings-section";

type PendingLarkFlow =
  | ({ kind: "config" } & LarkConfigStartResponse)
  | ({ kind: "auth" } & LarkAuthStartResponse);

type LarkAuthDomain = "calendar" | "docs" | "drive" | "all";

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
  const [isCheckingConnection, setIsCheckingConnection] = useState(false);
  const [selectedAuthDomains, setSelectedAuthDomains] = useState<
    LarkAuthDomain[]
  >([]);
  const [customAuthScope, setCustomAuthScope] = useState("");
  const browserWindowRef = useRef<Window | null>(null);
  const authRequestRef = useRef<LarkAuthStartRequest>({ recommend: true });
  const connectBusy =
    startConfig.isPending || completeConfig.isPending || startAuth.isPending;
  const connectActionBusy = connectBusy || isCheckingConnection;
  const isConnected = data?.auth.status === "authenticated";
  const trimmedCustomAuthScope = customAuthScope.trim();
  const hasAdditionalPermissionRequest =
    selectedAuthDomains.length > 0 || trimmedCustomAuthScope.length > 0;

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

  const startUserAuth = (
    browserWindow = browserWindowRef.current,
    request = authRequestRef.current,
  ) => {
    startAuth.mutate(request, {
      onSuccess: (result) => {
        setPendingFlow({ kind: "auth", ...result });
        openAuthorizationUrl(result.verification_url, browserWindow);
        toast.success(t.settings.integrations.lark.authStarted);
      },
      onError: (err) => {
        closePendingBrowserWindow(browserWindow);
        toast.error(err instanceof Error ? err.message : String(err));
      },
    });
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

  const startConnectionFlow = (
    status: LarkIntegrationStatus,
    browserWindow: Window | null,
  ) => {
    if (!status.app_configured) {
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

  const buildAuthRequest = (): LarkAuthStartRequest => ({
    recommend: true,
    domains: selectedAuthDomains,
    scope: trimmedCustomAuthScope.length > 0 ? trimmedCustomAuthScope : null,
  });

  const toggleAuthDomain = (domain: LarkAuthDomain) => {
    setSelectedAuthDomains((current) => {
      if (domain === "all") {
        return current.includes("all") ? [] : ["all"];
      }
      const withoutAll = current.filter((item) => item !== "all");
      if (withoutAll.includes(domain)) {
        return withoutAll.filter((item) => item !== domain);
      }
      return [...withoutAll, domain];
    });
  };

  const handleConnect = async () => {
    if (!data) return;
    authRequestRef.current = buildAuthRequest();
    const browserWindow =
      data.auth.status === "authenticated" && !hasAdditionalPermissionRequest
        ? null
        : openPendingBrowserWindow();
    setIsCheckingConnection(true);
    try {
      const refreshed = await refetch();
      const latestStatus = refreshed.data ?? data;
      if (
        latestStatus.auth.status === "authenticated" &&
        !hasAdditionalPermissionRequest
      ) {
        closePendingBrowserWindow(browserWindow);
        toast.info(t.settings.integrations.lark.alreadyConnected);
        return;
      }
      startConnectionFlow(latestStatus, browserWindow);
    } catch (err) {
      closePendingBrowserWindow(browserWindow);
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setIsCheckingConnection(false);
    }
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
    connectActionBusy;

  const connectButtonLabel = isCheckingConnection
    ? t.settings.integrations.lark.checkingConnection
    : connectBusy
      ? t.settings.integrations.lark.authStarting
      : isConnected && hasAdditionalPermissionRequest
        ? t.settings.integrations.lark.requestPermissions
        : isConnected
          ? t.settings.integrations.lark.connectedAction
          : t.settings.integrations.lark.connect;

  const permissionDomains = [
    {
      id: "calendar",
      label: t.settings.integrations.lark.authDomainCalendar,
      description: t.settings.integrations.lark.authDomainCalendarDescription,
    },
    {
      id: "docs",
      label: t.settings.integrations.lark.authDomainDocs,
      description: t.settings.integrations.lark.authDomainDocsDescription,
    },
    {
      id: "drive",
      label: t.settings.integrations.lark.authDomainDrive,
      description: t.settings.integrations.lark.authDomainDriveDescription,
    },
    {
      id: "all",
      label: t.settings.integrations.lark.authDomainAll,
      description: t.settings.integrations.lark.authDomainAllDescription,
    },
  ] satisfies Array<{
    id: LarkAuthDomain;
    label: string;
    description: string;
  }>;

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
            <RefreshCwIcon
              className={cn("size-4", isFetching && "animate-spin")}
            />
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
              connected={isConnected}
            />
            {data.installed && data.cli.available && (
              <div className="rounded-lg border p-3">
                <div className="space-y-1">
                  <div className="text-sm font-medium">
                    {t.settings.integrations.lark.permissionTitle}
                  </div>
                  <p className="text-muted-foreground text-sm">
                    {t.settings.integrations.lark.permissionDescription}
                  </p>
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  {permissionDomains.map((domain) => {
                    const selected = selectedAuthDomains.includes(domain.id);
                    return (
                      <Button
                        key={domain.id}
                        type="button"
                        size="sm"
                        variant={selected ? "default" : "outline"}
                        onClick={() => toggleAuthDomain(domain.id)}
                        disabled={connectActionBusy}
                        title={domain.description}
                      >
                        {domain.label}
                      </Button>
                    );
                  })}
                </div>
                <div className="mt-3 space-y-1">
                  <Input
                    value={customAuthScope}
                    onChange={(event) =>
                      setCustomAuthScope(event.currentTarget.value)
                    }
                    disabled={connectActionBusy}
                    placeholder={
                      t.settings.integrations.lark.customScopePlaceholder
                    }
                    aria-label={t.settings.integrations.lark.customScopeLabel}
                  />
                  <p className="text-muted-foreground text-xs">
                    {t.settings.integrations.lark.customScopeDescription}
                  </p>
                </div>
              </div>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <Button onClick={handleInstall} disabled={installDisabled}>
                {install.isPending ? (
                  <RefreshCwIcon className="size-4 animate-spin" />
                ) : null}
                {install.isPending
                  ? t.settings.integrations.installing
                  : data.installed
                    ? t.settings.integrations.reinstall
                    : t.settings.integrations.install}
              </Button>
              <Button
                variant="outline"
                onClick={() => void handleConnect()}
                disabled={authDisabled}
              >
                {connectActionBusy ? (
                  <RefreshCwIcon className="size-4 animate-spin" />
                ) : null}
                {connectButtonLabel}
              </Button>
              {!isAdmin && (
                <span className="text-muted-foreground text-sm">
                  {t.settings.integrations.adminRequired}
                </span>
              )}
            </div>
            {install.isPending && (
              <Alert>
                <RefreshCwIcon className="size-4 animate-spin" />
                <AlertTitle>
                  {t.settings.integrations.lark.installingTitle}
                </AlertTitle>
                <AlertDescription>
                  {t.settings.integrations.lark.installingDescription}
                </AlertDescription>
              </Alert>
            )}
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
  connected,
}: {
  installed: boolean;
  cliReady: boolean;
  connected: boolean;
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
  if (connected) {
    return (
      <Alert>
        <CheckCircle2Icon />
        <AlertTitle>{t.settings.integrations.lark.connectedTitle}</AlertTitle>
        <AlertDescription>
          {t.settings.integrations.lark.connectedDescription}
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
