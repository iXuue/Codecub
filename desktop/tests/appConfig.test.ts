import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { defaultSettings, globalConfigDir, sanitizeSettingsForDisk, saveSettings, settingsPath } from "../electron/appConfig";

describe("app settings sanitization", () => {
  beforeEach(async () => {
    process.env.CODECUB_GLOBAL_DIR = await mkdtemp(join(tmpdir(), "codecub-global-settings-"));
    vi.resetModules();
  });

  it("keeps provider metadata in settings without a plaintext api key", () => {
    const settings = sanitizeSettingsForDisk({
      ...defaultSettings,
      appearance: {
        themeMode: "light",
        accentColor: "#8B5CF6",
      },
      provider: {
        provider: "openai",
        model: "qwen-flash",
        baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
        host: "http://127.0.0.1:11434",
        credential: {
          configured: true,
          source: "global-file",
          displayHint: "saved ending 1234",
        },
      },
    });

    const serialized = JSON.stringify(settings);
    expect(serialized).toContain("qwen-flash");
    expect(serialized).toContain("global-file");
    expect(serialized).toContain("#8B5CF6");
    expect(serialized).not.toContain("sk-test");
    expect(serialized).not.toContain("apiKey");
  });

  it("stores settings under the global directory next to the executable", async () => {
    await saveSettings({
      ...defaultSettings,
      provider: {
        ...defaultSettings.provider,
        provider: "deepseek",
        model: "deepseek-v4-flash",
        baseUrl: "https://api.deepseek.com",
        credential: { configured: true, source: "global-file", displayHint: "saved ending 0790" },
      },
    });

    expect(globalConfigDir()).toBe(process.env.CODECUB_GLOBAL_DIR);
    expect(settingsPath()).toBe(join(process.env.CODECUB_GLOBAL_DIR ?? "", "settings.json"));
    const saved = JSON.parse(await readFile(settingsPath(), "utf-8"));
    expect(saved.provider.provider).toBe("deepseek");
    expect(saved.provider.credential.source).toBe("global-file");
  });

  it("defaults to dark glass appearance with a non-red accent", () => {
    expect(defaultSettings.appearance).toEqual({
      themeMode: "dark",
      accentColor: "#38BDF8",
    });
    expect(defaultSettings.executionMode).toBe("single");
  });
});
