import { getAPIClient } from "@/core/api";
import { fetch as fetchWithAuth } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";
import type { AgentThread } from "@/core/threads";

import type { SidecarContext } from "./context";
import {
  SIDECAR_METADATA_KEY,
  buildSidecarThreadMetadata,
  isSidecarThread,
} from "./thread";

type SidecarThreadSearchClient = {
  threads: {
    search: (query: Record<string, unknown>) => Promise<AgentThread[]>;
  };
};

export async function createSidecarThread({
  parentThreadId,
  context,
}: {
  parentThreadId: string;
  context: SidecarContext | SidecarContext[];
}): Promise<AgentThread> {
  const response = await fetchWithAuth(`${getBackendBaseURL()}/api/threads`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      metadata: buildSidecarThreadMetadata(parentThreadId, context),
    }),
  });

  if (!response.ok) {
    throw new Error("Failed to create side conversation.");
  }

  return (await response.json()) as AgentThread;
}

export async function findLatestSidecarThread({
  parentThreadId,
  isMock,
  apiClient = getAPIClient(isMock) as SidecarThreadSearchClient,
}: {
  parentThreadId: string;
  isMock?: boolean;
  apiClient?: SidecarThreadSearchClient;
}): Promise<AgentThread | null> {
  const response = await apiClient.threads.search({
    metadata: {
      [SIDECAR_METADATA_KEY]: true,
      parent_thread_id: parentThreadId,
    },
    limit: 1,
    offset: 0,
    sortBy: "updated_at",
    sortOrder: "desc",
  });

  return (
    response.find(
      (thread) =>
        isSidecarThread(thread) &&
        thread.metadata?.parent_thread_id === parentThreadId,
    ) ?? null
  );
}
