import { useState, type Dispatch, type SetStateAction } from "react";
import type { I18nKey, Locale } from "../i18n";
import type { AppSettings } from "../../electron/ipcTypes";
import { Toolbar } from "./Toolbar";

type ApprovalPolicy = "ask" | "auto" | "never";

type SettingsPageProps = {
  locale: Locale;
  setLocale: Dispatch<SetStateAction<Locale>>;
  approvalPolicy: ApprovalPolicy;
  setApprovalPolicy: Dispatch<SetStateAction<ApprovalPolicy>>;
  providerSettings: AppSettings["provider"] | null;
  setProviderSettings: Dispatch<SetStateAction<AppSettings["provider"] | null>>;
  appearanceSettings: AppSettings["appearance"];
  setAppearanceSettings: Dispatch<SetStateAction<AppSettings["appearance"]>>;
  t: (key: I18nKey) => string;
  onBack: () => void;
};

const accentPresets = ["#38BDF8", "#22C55E", "#8B5CF6", "#F59E0B", "#14B8A6"];

type ConnectionPreset = "rightcode-codex" | "rightcode-claude" | "openai-official" | "anthropic-official" | "deepseek-official" | "kimi-official" | "minimax-official" | "ollama-local";

const providerDefaults: Record<ConnectionPreset, AppSettings["provider"]> = {
  "rightcode-codex": {
    provider: "openai",
    model: "gpt-5.4",
    baseUrl: "https://www.right.codes/codex/v1",
    host: "http://127.0.0.1:11434",
    credential: { configured: false, source: "none", displayHint: "not configured" },
    connectionProfileId: "rightcode-codex", connectionType: "relay", apiOperator: "right.codes",
    modelVendor: "openai", protocol: "openai-responses", responseSchema: "rightcode-codex-unverified",
    credentialId: "rightcode", verificationStatus: "verified", endpointVerificationStatus: "verified", usageSchemaVerificationStatus: "unverified",
  },
  "openai-official": {
    provider: "openai", model: "gpt-5.2", baseUrl: "https://api.openai.com/v1", host: "http://127.0.0.1:11434",
    credential: { configured: false, source: "none", displayHint: "not configured" },
    connectionProfileId: "openai-official", connectionType: "direct", apiOperator: "openai",
    modelVendor: "openai", protocol: "openai-responses", responseSchema: "openai-responses",
    credentialId: "openai-official", verificationStatus: "verified", endpointVerificationStatus: "verified", usageSchemaVerificationStatus: "verified",
  },
  "anthropic-official": {
    provider: "anthropic", model: "claude-sonnet-4-6", baseUrl: "https://api.anthropic.com/v1", host: "http://127.0.0.1:11434",
    credential: { configured: false, source: "none", displayHint: "not configured" },
    connectionProfileId: "anthropic-official", connectionType: "direct", apiOperator: "anthropic",
    modelVendor: "anthropic", protocol: "anthropic-messages", responseSchema: "anthropic-messages",
    credentialId: "anthropic-official", verificationStatus: "verified", endpointVerificationStatus: "verified", usageSchemaVerificationStatus: "verified",
  },
  "deepseek-official": {
    provider: "deepseek",
    model: "deepseek-v4-flash",
    baseUrl: "https://api.deepseek.com",
    host: "http://127.0.0.1:11434",
    credential: { configured: false, source: "none", displayHint: "not configured" },
    connectionProfileId: "deepseek-official", connectionType: "direct", apiOperator: "deepseek",
    modelVendor: "deepseek", protocol: "openai-chat", responseSchema: "deepseek-chat",
    credentialId: "deepseek-official", verificationStatus: "verified", endpointVerificationStatus: "verified", usageSchemaVerificationStatus: "verified",
  },
  "kimi-official": {
    provider: "kimi",
    model: "moonshot-v1-8k",
    baseUrl: "https://api.moonshot.cn/v1",
    host: "http://127.0.0.1:11434",
    credential: { configured: false, source: "none", displayHint: "not configured" },
    connectionProfileId: "kimi-official", connectionType: "direct", apiOperator: "moonshot",
    modelVendor: "moonshot", protocol: "openai-chat", responseSchema: "moonshot-chat",
    credentialId: "kimi-official", verificationStatus: "verified", endpointVerificationStatus: "verified", usageSchemaVerificationStatus: "unverified",
  },
  "minimax-official": {
    provider: "minimax",
    model: "MiniMax-M3",
    baseUrl: "https://api.minimax.io/v1",
    host: "http://127.0.0.1:11434",
    credential: { configured: false, source: "none", displayHint: "not configured" },
    connectionProfileId: "minimax-official", connectionType: "direct", apiOperator: "minimax",
    modelVendor: "minimax", protocol: "openai-chat", responseSchema: "minimax-chat",
    credentialId: "minimax-official", verificationStatus: "verified", endpointVerificationStatus: "verified", usageSchemaVerificationStatus: "verified",
  },
  "rightcode-claude": {
    provider: "anthropic",
    model: "claude-sonnet-4-6",
    baseUrl: "https://www.right.codes/claude/v1",
    host: "http://127.0.0.1:11434",
    credential: { configured: false, source: "none", displayHint: "not configured" },
    connectionProfileId: "rightcode-claude", connectionType: "relay", apiOperator: "right.codes",
    modelVendor: "anthropic", protocol: "anthropic-messages", responseSchema: "rightcode-claude-unverified",
    credentialId: "rightcode", verificationStatus: "verified", endpointVerificationStatus: "verified", usageSchemaVerificationStatus: "unverified",
  },
  "ollama-local": {
    provider: "ollama",
    model: "qwen3.5:4b",
    baseUrl: "",
    host: "http://127.0.0.1:11434",
    credential: { configured: false, source: "none", displayHint: "not configured" },
    connectionProfileId: "ollama-local", connectionType: "local", apiOperator: "local",
    modelVendor: "ollama", protocol: "ollama-generate", responseSchema: "ollama",
    credentialId: "ollama-local", verificationStatus: "verified", endpointVerificationStatus: "verified", usageSchemaVerificationStatus: "unverified",
  },
};

function customConnection(baseUrl: string, settings: AppSettings["provider"]): Partial<AppSettings["provider"]> {
  let hash = 2166136261;
  const source = `${settings.provider}|${baseUrl.trim().toLowerCase()}`;
  for (let index = 0; index < source.length; index += 1) {
    hash ^= source.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  const fingerprint = (hash >>> 0).toString(16).padStart(8, "0");
  let operator = "custom";
  try { operator = new URL(baseUrl).hostname || "custom"; } catch { /* Keep invalid edits visibly unverified. */ }
  return {
    baseUrl,
    connectionProfileId: `custom-${fingerprint}`,
    connectionType: "custom",
    apiOperator: operator,
    responseSchema: "custom-unverified",
    credentialId: `custom:${fingerprint}`,
    verificationStatus: "unverified",
    endpointVerificationStatus: "unverified",
    usageSchemaVerificationStatus: "unverified",
  };
}

export function SettingsPage({
  locale,
  setLocale,
  approvalPolicy,
  setApprovalPolicy,
  providerSettings,
  setProviderSettings,
  appearanceSettings,
  setAppearanceSettings,
  t,
  onBack,
}: SettingsPageProps) {
  const [apiKey, setApiKey] = useState("");
  const settings = providerSettings ?? providerDefaults["rightcode-codex"];

  const updateProviderSettings = (next: Partial<AppSettings["provider"]>) => {
    setProviderSettings({ ...settings, ...next });
  };

  const selectProvider = (preset: ConnectionPreset) => {
    setProviderSettings(providerDefaults[preset]);
    setApiKey("");
  };

  const saveSettings = async () => {
    const providerRequest = {
      provider: settings.provider,
      model: settings.model,
      baseUrl: settings.baseUrl,
      host: settings.host,
      apiKey,
      connectionProfileId: settings.connectionProfileId,
      connectionType: settings.connectionType,
      apiOperator: settings.apiOperator,
      modelVendor: settings.modelVendor,
      protocol: settings.protocol,
      responseSchema: settings.responseSchema,
      credentialId: settings.credentialId,
      verificationStatus: settings.verificationStatus,
      endpointVerificationStatus: settings.endpointVerificationStatus,
      usageSchemaVerificationStatus: settings.usageSchemaVerificationStatus,
    };
    const savedProviderSettings = (await window.codecub.saveProviderSettings(providerRequest)).provider;
    const saved = await window.codecub.saveSettings({
      provider: savedProviderSettings,
      language: locale,
      approvalPolicy,
      appearance: appearanceSettings,
    });
    setLocale(saved.language);
    setApprovalPolicy(saved.approvalPolicy);
    setProviderSettings(saved.provider);
    setApiKey("");
  };

  const clearApiKey = async () => {
    const savedProviderSettings = (await window.codecub.clearProviderCredential(settings.credentialId || settings.provider)).provider;
    setProviderSettings(savedProviderSettings);
    setApiKey("");
  };

  return (
    <div className="app-shell">
      <Toolbar t={t} title={t("settings")} meta={t("appName")} onBack={onBack} />
      <main className="settings-page">
        <section className="settings-section">
          <h2>{t("modelApiSettings")}</h2>
          <div className="credential-status">{t("globalSettingsStorage")}</div>
          <label>
            {t("connectionPreset")}
            <select value={settings.connectionProfileId || "rightcode-codex"} onChange={(event) => selectProvider(event.target.value as ConnectionPreset)}>
              <option value="rightcode-codex">{t("connectionRightCodeCodex")}</option>
              <option value="rightcode-claude">{t("connectionRightCodeClaude")}</option>
              <option value="openai-official">{t("connectionOpenAIOfficial")}</option>
              <option value="anthropic-official">{t("connectionAnthropicOfficial")}</option>
              <option value="deepseek-official">{t("providerDeepSeek")}</option>
              <option value="kimi-official">{t("providerKimi")}</option>
              <option value="minimax-official">{t("providerMiniMax")}</option>
              <option value="ollama-local">{t("providerOllama")}</option>
            </select>
          </label>
          <div className="connection-facts" aria-label={t("connectionFacts")}>
            <span>{connectionLabel(settings.connectionType, t)}</span>
            <span>{settings.apiOperator || t("unknownValue")}</span>
            <span>{settings.protocol || t("unknownValue")}</span>
            <span className={`connection-evidence ${settings.endpointVerificationStatus || settings.verificationStatus || "unverified"}`}>{t("endpointIdentity")}: {verificationLabel(settings.endpointVerificationStatus || settings.verificationStatus, t)}</span>
            <span className={`connection-evidence ${settings.usageSchemaVerificationStatus || "unverified"}`}>{t("usageSchema")}: {verificationLabel(settings.usageSchemaVerificationStatus, t)}</span>
          </div>
          <label>
            {t("model")}
            <input value={settings.model} onChange={(event) => updateProviderSettings({ model: event.target.value })} />
          </label>
          {settings.provider === "ollama" ? (
            <label>
              {t("host")}
              <input value={settings.host} onChange={(event) => updateProviderSettings({ host: event.target.value })} />
            </label>
          ) : (
            <label>
              {t("baseUrl")}
              <input
                value={settings.baseUrl}
                onChange={(event) => updateProviderSettings(customConnection(event.target.value, settings))}
              />
            </label>
          )}
          {settings.provider === "ollama" ? (
            <div className="credential-status">{t("notRequiredForOllama")}</div>
          ) : (
            <>
              <label>
                {t("apiKey")}
                <input
                  type="password"
                  value={apiKey}
                  placeholder={t("apiKeyPlaceholder")}
                  onChange={(event) => setApiKey(event.target.value)}
                />
              </label>
              <div className="credential-row">
                <span>{t("credentialStatus")}</span>
                <strong>{settings.credential.displayHint}</strong>
              </div>
              <button className="button secondary" type="button" onClick={clearApiKey}>
                {t("clearApiKey")}
              </button>
            </>
          )}
        </section>

        <section className="settings-section">
          <h2>{t("appearance")}</h2>
          <div className="appearance-toggle" role="group" aria-label={t("themeMode")}>
            <button
              className={appearanceSettings.themeMode === "dark" ? "theme-option active" : "theme-option"}
              type="button"
              onClick={() => setAppearanceSettings((current) => ({ ...current, themeMode: "dark" }))}
            >
              {t("darkTheme")}
            </button>
            <button
              className={appearanceSettings.themeMode === "light" ? "theme-option active" : "theme-option"}
              type="button"
              onClick={() => setAppearanceSettings((current) => ({ ...current, themeMode: "light" }))}
            >
              {t("lightTheme")}
            </button>
          </div>
          <label>
            {t("highlightColor")}
            <div className="accent-picker">
              {accentPresets.map((color) => (
                <button
                  aria-label={`${t("highlightColor")} ${color}`}
                  className={appearanceSettings.accentColor.toUpperCase() === color ? "accent-swatch active" : "accent-swatch"}
                  key={color}
                  style={{ background: color }}
                  type="button"
                  onClick={() => setAppearanceSettings((current) => ({ ...current, accentColor: color }))}
                />
              ))}
              <input
                aria-label={t("customHighlightColor")}
                className="accent-input"
                type="color"
                value={appearanceSettings.accentColor}
                onChange={(event) =>
                  setAppearanceSettings((current) => ({ ...current, accentColor: event.target.value.toUpperCase() }))
                }
              />
            </div>
          </label>
        </section>

        <section className="settings-section">
          <label>
            {t("approvalPolicy")}
            <select value={approvalPolicy} onChange={(event) => setApprovalPolicy(event.target.value as ApprovalPolicy)}>
              <option value="ask">ask</option>
              <option value="auto">auto</option>
              <option value="never">never</option>
            </select>
          </label>
          <label>
            {t("language")}
            <select value={locale} onChange={(event) => setLocale(event.target.value as Locale)}>
              <option value="zh-CN">中文</option>
              <option value="en-US">English</option>
            </select>
          </label>
          <button className="button primary" type="button" onClick={saveSettings}>
            {t("save")}
          </button>
        </section>
      </main>
    </div>
  );
}

function connectionLabel(type: AppSettings["provider"]["connectionType"], t: (key: I18nKey) => string) {
  if (type === "relay") return t("connectionRelay");
  if (type === "local") return t("connectionLocal");
  if (type === "custom") return t("connectionCustom");
  return t("connectionDirect");
}

function verificationLabel(status: "verified" | "unverified" | undefined, t: (key: I18nKey) => string) {
  return status === "verified" ? t("verified") : t("unverified");
}
