import { describe, expect, it } from "vitest";
import { defaultSettings, sanitizeSettingsForDisk } from "../electron/appConfig";

describe("app settings sanitization", () => {
  it("keeps provider metadata but never serializes a plaintext api key", () => {
    const settings = sanitizeSettingsForDisk({
      ...defaultSettings,
      provider: {
        provider: "openai",
        model: "qwen-flash",
        baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
        host: "http://127.0.0.1:11434",
        credential: {
          configured: true,
          source: "secure-store",
          displayHint: "saved ending 1234",
        },
      },
    });

    const serialized = JSON.stringify(settings);
    expect(serialized).toContain("qwen-flash");
    expect(serialized).toContain("secure-store");
    expect(serialized).not.toContain("sk-test");
    expect(serialized).not.toContain("apiKey");
  });
});
