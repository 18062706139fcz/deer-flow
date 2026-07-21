import { describe, expect, it } from "@rstest/core";

import {
  MAX_AGENT_OUTPUT_TOKENS,
  parseAgentModelSettingsDraft,
} from "@/components/workspace/agents/agent-settings-dialog-helpers";

describe("parseAgentModelSettingsDraft", () => {
  it("rejects invalid temperature values before save", () => {
    expect(
      parseAgentModelSettingsDraft({ temperature: "-0.1", maxTokens: "" }),
    ).toEqual({ ok: false, error: "temperature" });
    expect(
      parseAgentModelSettingsDraft({ temperature: "2.1", maxTokens: "" }),
    ).toEqual({ ok: false, error: "temperature" });
    expect(
      parseAgentModelSettingsDraft({ temperature: "warm", maxTokens: "" }),
    ).toEqual({ ok: false, error: "temperature" });
  });

  it("rejects invalid max token values before save", () => {
    expect(
      parseAgentModelSettingsDraft({ temperature: "", maxTokens: "0" }),
    ).toEqual({ ok: false, error: "max_tokens" });
    expect(
      parseAgentModelSettingsDraft({ temperature: "", maxTokens: "1.5" }),
    ).toEqual({ ok: false, error: "max_tokens" });
    expect(
      parseAgentModelSettingsDraft({
        temperature: "",
        maxTokens: String(MAX_AGENT_OUTPUT_TOKENS + 1),
      }),
    ).toEqual({ ok: false, error: "max_tokens" });
  });

  it("returns null settings when both fields inherit", () => {
    expect(
      parseAgentModelSettingsDraft({ temperature: " ", maxTokens: "" }),
    ).toEqual({ ok: true, modelSettings: null });
  });

  it("keeps explicit nulls for cleared sub-fields when another setting remains", () => {
    expect(
      parseAgentModelSettingsDraft({ temperature: "0.2", maxTokens: "" }),
    ).toEqual({
      ok: true,
      modelSettings: { temperature: 0.2, max_tokens: null },
    });
  });
});
