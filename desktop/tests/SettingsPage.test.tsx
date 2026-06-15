import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SettingsPage } from "../src/components/SettingsPage";
import { t } from "../src/i18n";
import type { AppSettings } from "../electron/ipcTypes";

const provider: AppSettings["provider"] = {
  provider: "openai",
  model: "qwen-flash",
  baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
  host: "http://127.0.0.1:11434",
  credential: { configured: true, source: "secure-store", displayHint: "saved ending 1234" },
};

describe("SettingsPage", () => {
  it("renders API key input as password and clears it after saving", async () => {
    const savedSettings: AppSettings = {
      language: "en-US",
      approvalPolicy: "ask",
      provider,
    };
    const saveProviderSettings = vi.fn(async () => savedSettings);
    const saveSettings = vi.fn(async (settings: AppSettings) => settings);
    (window as unknown as { codecub: Partial<Window["codecub"]> }).codecub = {
      saveSettings,
      saveProviderSettings,
      clearProviderCredential: vi.fn(),
    };

    render(
      <SettingsPage
        locale="en-US"
        setLocale={vi.fn()}
        approvalPolicy="ask"
        setApprovalPolicy={vi.fn()}
        providerSettings={provider}
        setProviderSettings={vi.fn()}
        t={(key) => t("en-US", key)}
        onBack={vi.fn()}
      />,
    );

    const apiKeyInput = screen.getByLabelText("API Key") as HTMLInputElement;
    expect(apiKeyInput.getAttribute("type")).toBe("password");
    fireEvent.change(apiKeyInput, { target: { value: "sk-secret-value" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(apiKeyInput.value).toBe(""));
    expect(saveProviderSettings).toHaveBeenCalledWith(
      expect.objectContaining({ apiKey: "sk-secret-value", provider: "openai" }),
    );
    expect(saveSettings).toHaveBeenCalledWith(expect.not.objectContaining({ apiKey: expect.any(String) }));
  });
});
