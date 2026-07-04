import { getBackendBaseURL } from "@/core/config";

import type { WorkspaceChangesResponse } from "./types";

export async function fetchWorkspaceChanges({
  threadId,
  runId,
  includeFiles = true,
}: {
  threadId: string;
  runId: string;
  includeFiles?: boolean;
}): Promise<WorkspaceChangesResponse> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/threads/${encodeURIComponent(
      threadId,
    )}/runs/${encodeURIComponent(
      runId,
    )}/workspace-changes?include_files=${includeFiles ? "true" : "false"}`,
  );

  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ detail: "Failed to load workspace changes." }));
    throw new Error(error.detail ?? "Failed to load workspace changes.");
  }

  return response.json();
}
