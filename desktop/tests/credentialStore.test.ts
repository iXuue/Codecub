import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";

describe("credential store", () => {
  beforeEach(async () => {
    process.env.CODECUB_GLOBAL_DIR = await mkdtemp(join(tmpdir(), "codecub-global-secrets-"));
    delete process.env.OPENAI_API_KEY;
    delete process.env.DEEPSEEK_API_KEY;
    delete process.env.MOONSHOT_API_KEY;
    delete process.env.MINIMAX_API_KEY;
    delete process.env.ANTHROPIC_API_KEY;
    vi.resetModules();
  });

  it("stores and reads API keys through codecub-global secrets.json", async () => {
    const store = await import("../electron/credentialStore");

    const status = await store.saveApiKey("openai", "sk-test-123456");
    const raw = await readFile(store.secretsPath(), "utf-8");

    expect(status).toEqual({
      configured: true,
      source: "global-file",
      displayHint: "saved ending 3456",
    });
    expect(JSON.parse(raw)).toEqual({ apiKeys: { openai: "sk-test-123456" } });
    expect(await store.readApiKey("openai")).toBe("sk-test-123456");
    expect(JSON.stringify(status)).not.toContain("sk-test");
  });

  it("does not treat provider environment variables as configured credentials", async () => {
    process.env.OPENAI_API_KEY = "sk-env-secret";
    const store = await import("../electron/credentialStore");

    await expect(store.apiKeyStatus("openai")).resolves.toEqual({
      configured: false,
      source: "none",
      displayHint: "not configured",
    });
  });

  it("keeps provider-specific keys isolated inside the global secrets file", async () => {
    const store = await import("../electron/credentialStore");

    await store.saveApiKey("deepseek", "sk-deepseek");
    await store.saveApiKey("kimi", "sk-kimi");
    await store.saveApiKey("minimax", "sk-minimax");

    await expect(store.readApiKey("deepseek")).resolves.toBe("sk-deepseek");
    await expect(store.readApiKey("kimi")).resolves.toBe("sk-kimi");
    await expect(store.readApiKey("minimax")).resolves.toBe("sk-minimax");
    await expect(store.readApiKey("openai")).resolves.toBe("");
  });

  it("lets Right Code Codex and Claude share one credential without overwriting official keys", async () => {
    const store = await import("../electron/credentialStore");

    await store.saveApiKey("rightcode", "sk-relay");
    await store.saveApiKey("openai-official", "sk-openai");

    await expect(store.readApiKey("rightcode", "openai")).resolves.toBe("sk-relay");
    await expect(store.readApiKey("openai-official", "openai")).resolves.toBe("sk-openai");
  });

  it("can read a legacy provider key only as a migration fallback", async () => {
    const store = await import("../electron/credentialStore");
    await store.saveApiKey("openai", "sk-legacy");

    await expect(store.readApiKey("rightcode", "openai")).resolves.toBe("sk-legacy");
    await expect(store.readApiKey("anthropic-official", "anthropic")).resolves.toBe("");
  });

  it("clears saved API keys from the global secrets file", async () => {
    const store = await import("../electron/credentialStore");

    await store.saveApiKey("anthropic", "sk-anthropic");
    await expect(store.readApiKey("anthropic")).resolves.toBe("sk-anthropic");
    await expect(store.clearApiKey("anthropic")).resolves.toEqual({
      configured: false,
      source: "none",
      displayHint: "not configured",
    });
    await expect(store.readApiKey("anthropic")).resolves.toBe("");
  });
});
