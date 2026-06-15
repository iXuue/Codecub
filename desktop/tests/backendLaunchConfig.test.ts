import { describe, expect, it } from "vitest";
import { buildBackendLaunchConfig } from "../electron/backendLaunchConfig";
import type { AppSettings } from "../electron/ipcTypes";

function settings(provider: AppSettings["provider"]["provider"]): AppSettings {
  return {
    language: "zh-CN",
    approvalPolicy: "ask",
    provider: {
      provider,
      model: provider === "ollama" ? "qwen3.5:4b" : "qwen-flash",
      baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
      host: "http://127.0.0.1:11434",
      credential: { configured: true, source: "secure-store", displayHint: "saved" },
    },
  };
}

describe("backend launch config", () => {
  it("passes OpenAI-compatible provider settings and API key through env", () => {
    const config = buildBackendLaunchConfig("D:/repo", settings("openai"), "sk-secret", {});
    expect(config.args).toEqual([
      "--app-mode",
      "--cwd",
      "D:/repo",
      "--approval",
      "ask",
      "--provider",
      "openai",
      "--model",
      "qwen-flash",
      "--base-url",
      "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ]);
    expect(config.env.OPENAI_API_KEY).toBe("sk-secret");
    expect(config.env.ANTHROPIC_API_KEY).toBeUndefined();
  });

  it("passes Anthropic-compatible API key through the anthropic env name", () => {
    const config = buildBackendLaunchConfig("D:/repo", settings("anthropic"), "sk-anthropic", {});
    expect(config.args).toContain("anthropic");
    expect(config.env.ANTHROPIC_API_KEY).toBe("sk-anthropic");
    expect(config.env.OPENAI_API_KEY).toBeUndefined();
  });

  it("does not inject API key for Ollama", () => {
    const config = buildBackendLaunchConfig("D:/repo", settings("ollama"), "sk-ignored", {});
    expect(config.args).toContain("--host");
    expect(config.args).toContain("http://127.0.0.1:11434");
    expect(config.env.OPENAI_API_KEY).toBeUndefined();
    expect(config.env.ANTHROPIC_API_KEY).toBeUndefined();
  });

  it("passes resume session id when requested", () => {
    const config = buildBackendLaunchConfig("D:/repo", settings("openai"), "", {}, "session-123");
    expect(config.args).toContain("--resume");
    expect(config.args).toContain("session-123");
  });
});
