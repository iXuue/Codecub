import { describe, expect, it } from "vitest";
import { buildBackendLaunchConfig, encodeUtf8Base64 } from "../electron/backendLaunchConfig";
import type { AppSettings } from "../electron/ipcTypes";

function settings(provider: AppSettings["provider"]["provider"]): AppSettings {
  return {
    language: "zh-CN",
    approvalPolicy: "ask",
    executionMode: "single",
    appearance: { themeMode: "dark", accentColor: "#38BDF8" },
    provider: {
      provider,
      model: provider === "ollama" ? "qwen3.5:4b" : "qwen-flash",
      baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
      host: "http://127.0.0.1:11434",
      credential: { configured: true, source: "global-file", displayHint: "saved" },
    },
  };
}

describe("backend launch config", () => {
  it("passes OpenAI-compatible provider settings and API key through env", () => {
    const config = buildBackendLaunchConfig("D:/repo", settings("openai"), "sk-secret", {});
    expect(config.args).toEqual([
      "--app-mode",
      "--cwd-b64",
      "RDovcmVwbw==",
      "--approval",
      "ask",
      "--provider",
      "openai",
      "--connection-profile-b64",
      expect.any(String),
      "--model",
      "qwen-flash",
      "--base-url",
      "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ]);
    expect(config.env.OPENAI_API_KEY).toBe("sk-secret");
    expect(config.env.ANTHROPIC_API_KEY).toBeUndefined();
    expect(config.env.PYTHONUTF8).toBe("1");
    expect(config.env.PYTHONIOENCODING).toBe("utf-8");
  });

  it("base64 encodes non-ASCII project paths as UTF-8", () => {
    const encoded = encodeUtf8Base64("D:\\代码备份\\项目");

    expect(Buffer.from(encoded, "base64").toString("utf-8")).toBe("D:\\代码备份\\项目");
  });

  it("passes Anthropic-compatible API key through the anthropic env name", () => {
    const config = buildBackendLaunchConfig("D:/repo", settings("anthropic"), "sk-anthropic", {});
    expect(config.args).toContain("anthropic");
    expect(config.env.ANTHROPIC_API_KEY).toBe("sk-anthropic");
    expect(config.env.OPENAI_API_KEY).toBeUndefined();
  });

  it("passes DeepSeek API key through the DeepSeek env name", () => {
    const config = buildBackendLaunchConfig("D:/repo", settings("deepseek"), "sk-deepseek", {});
    expect(config.args).toContain("deepseek");
    expect(config.env.DEEPSEEK_API_KEY).toBe("sk-deepseek");
    expect(config.env.OPENAI_API_KEY).toBeUndefined();
  });

  it("removes provider variables inherited from the parent process", () => {
    const config = buildBackendLaunchConfig("D:/repo", settings("deepseek"), "sk-deepseek", {
      CODECUB_PROVIDER: "openai",
      OPENAI_API_KEY: "sk-old-openai",
      OPENAI_MODEL: "old-model",
      DEEPSEEK_API_KEY: "sk-old-deepseek",
      PATH: "keep-path",
    });

    expect(config.env.PATH).toBe("keep-path");
    expect(config.env.CODECUB_PROVIDER).toBeUndefined();
    expect(config.env.OPENAI_API_KEY).toBeUndefined();
    expect(config.env.OPENAI_MODEL).toBeUndefined();
    expect(config.env.DEEPSEEK_API_KEY).toBe("sk-deepseek");
  });

  it("passes Kimi API key through the Moonshot env name", () => {
    const config = buildBackendLaunchConfig("D:/repo", settings("kimi"), "sk-kimi", {});
    expect(config.args).toContain("kimi");
    expect(config.env.MOONSHOT_API_KEY).toBe("sk-kimi");
    expect(config.env.OPENAI_API_KEY).toBeUndefined();
  });

  it("passes MiniMax API key through the MiniMax env name", () => {
    const config = buildBackendLaunchConfig("D:/repo", settings("minimax"), "sk-minimax", {});
    expect(config.args).toContain("minimax");
    expect(config.env.MINIMAX_API_KEY).toBe("sk-minimax");
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

  it("passes multi-agent mode only when explicitly selected", () => {
    const single = buildBackendLaunchConfig("D:/repo", settings("openai"), "", {});
    const multi = buildBackendLaunchConfig("D:/repo", { ...settings("openai"), executionMode: "multi_agent" }, "", {});

    expect(single.args).not.toContain("--multi-agent");
    expect(multi.args).toContain("--multi-agent");
  });
});
