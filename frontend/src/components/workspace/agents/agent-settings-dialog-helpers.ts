import type { AgentModelSettings } from "@/core/agents";

export const MAX_AGENT_OUTPUT_TOKENS = 200_000;

export type AgentSettingsValidationError = "temperature" | "max_tokens";

export type ParsedAgentModelSettings =
  | {
      ok: true;
      modelSettings: AgentModelSettings | null;
    }
  | {
      ok: false;
      error: AgentSettingsValidationError;
    };

export function parseAgentModelSettingsDraft({
  temperature,
  maxTokens,
}: {
  temperature: string;
  maxTokens: string;
}): ParsedAgentModelSettings {
  const trimmedTemp = temperature.trim();
  const trimmedMax = maxTokens.trim();

  let temperatureValue: number | null = null;
  if (trimmedTemp !== "") {
    temperatureValue = Number(trimmedTemp);
    if (
      Number.isNaN(temperatureValue) ||
      temperatureValue < 0 ||
      temperatureValue > 2
    ) {
      return { ok: false, error: "temperature" };
    }
  }

  let maxTokensValue: number | null = null;
  if (trimmedMax !== "") {
    maxTokensValue = Number(trimmedMax);
    if (
      !Number.isInteger(maxTokensValue) ||
      maxTokensValue < 1 ||
      maxTokensValue > MAX_AGENT_OUTPUT_TOKENS
    ) {
      return { ok: false, error: "max_tokens" };
    }
  }

  const hasSettings = temperatureValue != null || maxTokensValue != null;
  return {
    ok: true,
    modelSettings: hasSettings
      ? { temperature: temperatureValue, max_tokens: maxTokensValue }
      : null,
  };
}
