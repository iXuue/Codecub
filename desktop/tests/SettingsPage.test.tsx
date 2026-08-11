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
  credential: { configured: true, source: "global-file", displayHint: "saved ending 1234" },
};

const appearance: AppSettings["appearance"] = {
  themeMode: "dark",
  accentColor: "#38BDF8",
};

describe("SettingsPage", () => {
  it("renders API key input as password and clears it after saving", async () => {
    const savedSettings: AppSettings = {
      language: "en-US",
      approvalPolicy: "ask",
      appearance,
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
        appearanceSettings={appearance}
        setAppearanceSettings={vi.fn()}
        t={(key) => t("en-US", key)}
        onBack={vi.fn()}
      />,
    );

    const apiKeyInput = screen.getByLabelText("API Key") as HTMLInputElement;
    expect(screen.getByText(/Saved globally next to CodeCub.exe/)).toBeTruthy();
    expect(apiKeyInput.getAttribute("type")).toBe("password");
    fireEvent.change(apiKeyInput, { target: { value: "sk-secret-value" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(apiKeyInput.value).toBe(""));
    expect(saveProviderSettings).toHaveBeenCalledWith(
      expect.objectContaining({ apiKey: "sk-secret-value", provider: "openai" }),
    );
    expect(saveSettings).toHaveBeenCalledWith(expect.not.objectContaining({ apiKey: expect.any(String) }));
  });

  it("saves appearance theme and highlight settings", async () => {
    const nextAppearance: AppSettings["appearance"] = { themeMode: "light", accentColor: "#8B5CF6" };
    const savedSettings: AppSettings = {
      language: "en-US",
      approvalPolicy: "ask",
      appearance: nextAppearance,
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
        appearanceSettings={nextAppearance}
        setAppearanceSettings={vi.fn()}
        t={(key) => t("en-US", key)}
        onBack={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(saveSettings).toHaveBeenCalledWith(expect.objectContaining({ appearance: nextAppearance })),
    );
  });

  it("offers hosted provider presets in the provider selector", () => {
    (window as unknown as { codecub: Partial<Window["codecub"]> }).codecub = {
      saveSettings: vi.fn(),
      saveProviderSettings: vi.fn(),
      clearProviderCredential: vi.fn(),
    };
    const setProviderSettings = vi.fn();

    render(
      <SettingsPage
        locale="en-US"
        setLocale={vi.fn()}
        approvalPolicy="ask"
        setApprovalPolicy={vi.fn()}
        providerSettings={provider}
        setProviderSettings={setProviderSettings}
        appearanceSettings={appearance}
        setAppearanceSettings={vi.fn()}
        t={(key) => t("en-US", key)}
        onBack={vi.fn()}
      />,
    );

    expect(screen.getByRole("option", { name: "DeepSeek" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "Kimi / Moonshot" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "MiniMax" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "Right Code · Codex" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "Right Code · Claude" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "OpenAI · Official API" })).toBeTruthy();

    fireEvent.change(screen.getByLabelText("API connection"), { target: { value: "deepseek-official" } });

    expect(setProviderSettings).toHaveBeenCalledWith(expect.objectContaining({ provider: "deepseek" }));
  });

  it("marks a manually edited base URL as a separate unverified connection", () => {
    (window as unknown as { codecub: Partial<Window["codecub"]> }).codecub = {
      saveSettings: vi.fn(), saveProviderSettings: vi.fn(), clearProviderCredential: vi.fn(),
    };
    const setProviderSettings = vi.fn();
    render(
      <SettingsPage locale="en-US" setLocale={vi.fn()} approvalPolicy="ask" setApprovalPolicy={vi.fn()}
        providerSettings={provider} setProviderSettings={setProviderSettings} appearanceSettings={appearance}
        setAppearanceSettings={vi.fn()} t={(key) => t("en-US", key)} onBack={vi.fn()} />,
    );

    fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "https://relay.example/v1" } });
    expect(setProviderSettings).toHaveBeenCalledWith(expect.objectContaining({
      connectionType: "custom", apiOperator: "relay.example", verificationStatus: "unverified",
    }));
  });

  it("shows endpoint and usage verification separately", () => {
    (window as unknown as { codecub: Partial<Window["codecub"]> }).codecub = {
      saveSettings: vi.fn(), saveProviderSettings: vi.fn(), clearProviderCredential: vi.fn(),
    };
    render(<SettingsPage locale="en-US" setLocale={vi.fn()} approvalPolicy="ask" setApprovalPolicy={vi.fn()}
      providerSettings={{ ...provider, connectionType: "relay", endpointVerificationStatus: "verified", usageSchemaVerificationStatus: "unverified" }} setProviderSettings={vi.fn()} appearanceSettings={appearance}
      setAppearanceSettings={vi.fn()} t={(key) => t("en-US", key)} onBack={vi.fn()} />);
    expect(screen.getByText("Endpoint: Verified")).toBeTruthy();
    expect(screen.getByText("Usage data: Unverified")).toBeTruthy();
  });
});
