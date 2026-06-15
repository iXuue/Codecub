import { beforeEach, describe, expect, it, vi } from "vitest";

const passwords = new Map<string, string>();

vi.mock("keytar", () => ({
  default: {
    getPassword: vi.fn(async (_service: string, account: string) => passwords.get(account) ?? null),
    setPassword: vi.fn(async (_service: string, account: string, password: string) => {
      passwords.set(account, password);
    }),
    deletePassword: vi.fn(async (_service: string, account: string) => passwords.delete(account)),
  },
}));

describe("credential store", () => {
  beforeEach(() => {
    passwords.clear();
    delete process.env.OPENAI_API_KEY;
    delete process.env.ANTHROPIC_API_KEY;
    vi.resetModules();
  });

  it("stores and reads API keys through keytar without exposing plaintext in status", async () => {
    const store = await import("../electron/credentialStore");
    const status = await store.saveApiKey("openai", "sk-test-123456");
    expect(status).toEqual({
      configured: true,
      source: "secure-store",
      displayHint: "saved ending 3456",
    });
    expect(await store.readApiKey("openai")).toBe("sk-test-123456");
    expect(JSON.stringify(status)).not.toContain("sk-test");
  });

  it("reports environment fallback without reading it into app settings", async () => {
    process.env.OPENAI_API_KEY = "sk-env-secret";
    const store = await import("../electron/credentialStore");
    await expect(store.apiKeyStatus("openai")).resolves.toEqual({
      configured: true,
      source: "environment",
      displayHint: "OPENAI_API_KEY",
    });
  });

  it("clears saved API keys", async () => {
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
