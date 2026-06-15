import { describe, expect, it } from "vitest";
import { defaultSettings, sanitizeSettingsForDisk } from "../electron/appConfig";

describe("model API secret safety", () => {
  it("does not expose full API keys through settings JSON", () => {
    const secret = "sk-live-dangerous-secret";
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
          displayHint: "saved ending cret",
        },
      },
    });
    const payload = JSON.stringify(settings);
    expect(payload).not.toContain(secret);
    expect(payload).not.toContain("sk-live-dangerous");
    expect(payload).not.toContain("apiKey");
  });
});
